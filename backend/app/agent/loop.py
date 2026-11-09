"""Loop agente tool-calling (spec sez. 2): il modello sceglie uno strumento,
lo strumento gira (dopo permessi + log), il modello legge il risultato e
continua finche' non risponde con testo puro o si raggiunge max_steps.

Il loop non sa quale provider e' attivo: parla con la registry, che applica
la catena di fallback a OGNI passo (spec sez. 1). /stop: cancel.is_set()
viene controllato a ogni passo e chiude subito.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from threading import Event
from typing import Any, Callable

from ..providers.base import Message, ProviderResponse, ToolCall
from ..providers.registry import ProviderRegistry
from .actionlog import ActionLog
from .permissions import Decision, Mode, decide
from .seal import SealGuard
from .tools import TOOLS_BY_NAME, ToolContext

DEFAULT_MAX_STEPS = 10
# Un risultato strumento puo' essere lungo: oltre questo tetto il modello
# riceve il troncato (il log tiene comunque solo 500 caratteri).
TOOL_RESULT_FOR_MODEL = 2000


@dataclass
class LoopResult:
    """Esito di un run: testo finale, passi usati, eventuali note per la CLI
    (strumenti eseguiti/negati, stop per limite)."""
    text: str
    steps: int
    notes: list[str] = field(default_factory=list)
    stopped_by_limit: bool = False
    cancelled: bool = False


AskFn = Callable[[str], bool]           # domanda -> True se approvato
FallbackFn = Callable[[str, str, str], None]
DeltaFn = Callable[[str], None]         # delta di testo in streaming verso la CLI


def _parse_text_tool_call(text: str) -> ToolCall | None:
    """Fallback JSON-in-testo (spec sez. 2): un modello senza tool-calling
    nativo puo' emettere la chiamata come oggetto JSON nel testo
    ({"name": ..., "arguments": {...}}, anche in blocco ```json). Vale solo
    se il nome e' un tool noto e gli argomenti un oggetto: altrimenti il
    testo resta la risposta finale, mai eseguito."""
    import re

    candidates: list[str] = []
    for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S):
        candidates.append(m.group(1))
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    for cand in candidates:
        try:
            data = json.loads(cand)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("name") or data.get("tool")
        args = data.get("arguments") or data.get("args") or data.get("parameters")
        if isinstance(name, str) and name in TOOLS_BY_NAME and isinstance(args, dict):
            return ToolCall(name=name, arguments=args)
    return None


def _describe_call(name: str, args: dict[str, Any]) -> str:
    if name == "shell":
        return f"shell: {args.get('command', '')!r}"
    if name in ("read_file", "write_file"):
        return f"{name}: {args.get('path', '')!r}"
    return f"{name}: {json.dumps(args, ensure_ascii=False)[:120]}"


def run_agent_loop(
    registry: ProviderRegistry,
    active: str,
    messages: list[Message],
    ctx: ToolContext,
    mode: Mode,
    log: ActionLog,
    *,
    ask_fn: AskFn,
    max_steps: int = DEFAULT_MAX_STEPS,
    cancel: Event | None = None,
    on_fallback: FallbackFn | None = None,
    on_step: Callable[[str], None] | None = None,
    on_delta: DeltaFn | None = None,
    seal: SealGuard | None = None,
) -> LoopResult:
    """Esegue il loop su `messages` (mutata: storia condivisa con la chat).

    Ogni passo chiama il provider in streaming (spec sez. 2: output in
    streaming nella CLI): i delta escono via on_delta man mano. Se a fine
    stream la risposta contiene tool-call, il testo gia' mostrato resta come
    preambolo del modello e il loop esegue gli strumenti; se non ci sono
    tool-call la risposta in streaming e' quella finale. Una registry che
    risponde non-stream (test, provider senza stream) funziona lo stesso."""
    notes: list[str] = []
    tool_specs = [t.spec() for t in TOOLS_BY_NAME.values()]

    for step in range(1, max_steps + 1):
        if cancel is not None and cancel.is_set():
            return LoopResult(text="", steps=step - 1, notes=notes, cancelled=True)

        result = registry.chat_with_fallback(
            active, messages, tools=tool_specs, stream=True, on_fallback=on_fallback)
        if isinstance(result, ProviderResponse):  # registry non-stream (test)
            response = result
            if on_delta and response.text and not response.tool_calls:
                on_delta(response.text)
        else:
            for delta in result:
                if cancel is not None and cancel.is_set():
                    result.close()
                    return LoopResult(text="", steps=step, notes=notes, cancelled=True)
                if on_delta:
                    on_delta(delta)
            response = result.response or ProviderResponse(text="")
        if on_delta and (not response.tool_calls or response.text):
            on_delta("\n")  # chiusura riga dopo lo stream / prima delle note strumento

        if not response.tool_calls:
            fallback = _parse_text_tool_call(response.text or "")
            if fallback is None:
                return LoopResult(text=response.text, steps=step, notes=notes)
            note = f"[tool-call letta dal testo (fallback JSON): {fallback.name}]"
            notes.append(note)
            if on_step:
                on_step(note)
            response = ProviderResponse(text="", tool_calls=[fallback])

        # Turno assistente con tool-call: va nella storia prima dei risultati.
        messages.append({
            "role": "assistant",
            "content": response.text or "",
            "tool_calls": [{"name": tc.name, "arguments": tc.arguments}
                           for tc in response.tool_calls],
        })

        for call in response.tool_calls:
            if cancel is not None and cancel.is_set():
                return LoopResult(text="", steps=step, notes=notes, cancelled=True)

            tool = TOOLS_BY_NAME.get(call.name)
            decision, reason = decide(mode, call.name, call.arguments, ctx.workspace)
            desc = _describe_call(call.name, call.arguments)

            if decision is Decision.DENY:
                note = f"[negato: {desc} - {reason}]"
                notes.append(note)
                if on_step:
                    on_step(note)
                log.record(tool=call.name, args=call.arguments,
                           decision="denied", reason=reason)
                result_text = f"richiesta negata: {reason}"
            else:
                if decision is Decision.ASK:
                    approved = ask_fn(f"{desc} ({reason})")
                    log.record(tool=call.name, args=call.arguments,
                               decision="asked", reason=reason,
                               result="approvato" if approved else "rifiutato")
                    if not approved:
                        note = f"[non approvato da te: {desc}]"
                        notes.append(note)
                        if on_step:
                            on_step(note)
                        result_text = ("Azione NON approvata dall'utente. NON riproporre "
                                       "questo strumento per questa richiesta: rispondi "
                                       "spiegando che serve la sua approvazione per procedere.")
                        messages.append({"role": "tool", "tool_name": call.name,
                                         "content": result_text})
                        continue
                assert tool is not None
                raw = tool.run(call.arguments, ctx)
                log.record(tool=call.name, args=call.arguments,
                           decision="run", reason=reason, result=raw)
                if seal is not None:
                    for incident in seal.verify_and_restore():
                        # Hard limit spec sez. 8: la manomissione non persiste.
                        note = f"[sigillo: {incident}]"
                        notes.append(note)
                        if on_step:
                            on_step(note)
                        log.record(tool="_seal", args={}, decision="restored",
                                   reason=incident)
                note = f"[eseguito: {desc}]"
                notes.append(note)
                if on_step:
                    on_step(note)
                result_text = ctx.secrets.redact(raw)[:TOOL_RESULT_FOR_MODEL]

            messages.append({"role": "tool", "tool_name": call.name,
                             "content": result_text})

    stop_note = (f"limite di {max_steps} passi raggiunto: mi fermo qui. "
                 "Riformula o alza max_steps in config se serve continuare.")
    notes.append(stop_note)
    return LoopResult(text="", steps=max_steps, notes=notes, stopped_by_limit=True)
