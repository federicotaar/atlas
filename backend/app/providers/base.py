"""Astrazione provider LLM (spec sez. 1).

Un'unica interfaccia `LLMProvider.chat(messages, tools, stream)`: il loop
dell'agente non sa quale provider e' attivo. I tool-call arrivano normalizzati
(`ToolCall` con nome + argomenti dict) qualunque sia il formato nativo del
provider. La chiave API non compare mai in repr, log o messaggi d'errore.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator

# Un messaggio di chat: {"role": "system"|"user"|"assistant"|"tool", "content": str}
# (stage 3 aggiunge tool_calls su assistant e tool_call_id su tool).
Message = dict[str, Any]

# Uno strumento offerto al modello:
# {"name": str, "description": str, "parameters": <JSON Schema>}
ToolSpec = dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """Chiamata a strumento normalizzata: stessa forma per ogni provider."""

    name: str
    arguments: dict[str, Any]


@dataclass
class ProviderResponse:
    """Risposta completa di una chat non in streaming (o finale di uno stream)."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    finish_reason: str = ""
    raw: dict[str, Any] | None = None


class ProviderError(Exception):
    """Errore di un provider. Il messaggio non contiene MAI la chiave API.

    retryable=True: vale la pena riprovare lo stesso provider piu' tardi
    (rete giu', timeout, 5xx, rate limit). Il fallback di catena passa al
    provider successivo su QUALUNQUE ProviderError, retryable o meno
    (spec: fallback automatico se un provider fallisce o va in rate limit).
    """

    retryable = False

    def __init__(self, provider: str, message: str):
        self.provider = provider
        super().__init__(f"{provider}: {message}")


class ProviderUnavailable(ProviderError):
    """Rete giu', timeout o 5xx: provider momentaneamente irraggiungibile."""

    retryable = True


class RateLimitError(ProviderError):
    """429 o equivalente: troppo traffico su questo provider."""

    retryable = True


class AuthenticationError(ProviderError):
    """Chiave rifiutata (401/403): configurazione da correggere, ma il
    fallback passa comunque al provider successivo (spec: la catena regge
    anche con una chiave invalida)."""

    retryable = False


class ModelNotFound(ProviderError):
    """Il modello configurato non esiste presso il provider."""

    retryable = False


class AllProvidersFailed(ProviderError):
    """Ogni provider della catena ha fallito: riporta i tentativi in chiaro
    (senza segreti) cosi' l'utente capisce cosa sistemare."""

    def __init__(self, attempts: list[tuple[str, str]]):
        self.attempts = attempts
        summary = "; ".join(f"{name} ({err})" for name, err in attempts) or "nessun provider configurato"
        super().__init__("chain", f"tutti i provider hanno fallito: {summary}")




def translate_tool_messages(messages: list[Message], *,
                            arguments_as_json: bool) -> list[Message]:
    """Traduce la forma normalizzata del loop (assistant.tool_calls come
    {name, arguments}, tool con tool_name) nella forma chat OpenAI/Ollama:
    tool_calls con id + type function, tool con tool_call_id. Gli id sono
    sintetizzati in ordine di conversazione (call_1, call_2, ...) e i
    messaggi tool consumano gli id in coda. Messaggi gia' in forma nativa
    (tool_calls con 'function') passano invariati.
    """
    import json as _json
    out: list[Message] = []
    pending_ids: list[str] = []
    counter = 0
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            if "function" in (m["tool_calls"][0] or {}):
                out.append(m)  # gia' nativo
                continue
            tcs = []
            for tc in m["tool_calls"]:
                counter += 1
                call_id = f"call_{counter}"
                pending_ids.append(call_id)
                args = tc.get("arguments") or {}
                tcs.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": tc.get("name", ""),
                        "arguments": (_json.dumps(args, ensure_ascii=False)
                                      if arguments_as_json else args),
                    },
                })
            out.append({"role": "assistant", "content": m.get("content") or "",
                        "tool_calls": tcs})
        elif role == "tool":
            entry: Message = {"role": "tool", "content": str(m.get("content", ""))}
            if pending_ids:
                entry["tool_call_id"] = pending_ids.pop(0)
            if m.get("tool_name"):
                entry["name"] = m["tool_name"]
            out.append(entry)
        else:
            out.append(m)
    return out


class ChatStream:
    """Stream di risposta: itera i delta di testo; a fine stream `.response`
    contiene la ProviderResponse completa (testo accumulato + tool-call).

    `chunks_emitted` dice se e' gia' uscito del testo: il fallback di catena
    puo' cambiare provider solo prima del primo delta, dopo sarebbe testo
    duplicato/mischiato per l'utente.
    """

    def __init__(self, events: Iterator[Any], provider: str, model: str):
        self._events = events
        self._pending: list[str] = []
        self._chunks: list[str] = []
        self._provider = provider
        self._model = model
        self.response: ProviderResponse | None = None
        self.chunks_emitted = 0

    def _prepend(self, delta: str) -> None:
        self._pending.append(delta)

    def close(self) -> None:
        """Chiude lo stream (annullamento con /stop): il contesto HTTP del
        provider viene rilasciato subito, senza leggere il resto del corpo."""
        close = getattr(self._events, "close", None)
        if callable(close):
            close()

    def __iter__(self) -> "ChatStream":
        return self

    def __next__(self) -> str:
        if self._pending:
            return self._pending.pop(0)
        for item in self._events:
            if isinstance(item, ProviderResponse):
                item.text = "".join(self._chunks) or item.text
                self.response = item
                continue
            delta = str(item)
            self._chunks.append(delta)
            self.chunks_emitted += 1
            return delta
        if self.response is None:
            self.response = ProviderResponse(
                text="".join(self._chunks), provider=self._provider, model=self._model
            )
        raise StopIteration


class LLMProvider(ABC):
    """Interfaccia unica dei provider (spec 1): chat(messages, tools, stream).

    - stream=False -> ProviderResponse completa.
    - stream=True  -> ChatStream (delta di testo; .response alla fine).
    La chiave API e' privata dell'istanza: mai in repr/log/errori.
    """

    type: str = "generic"

    def __init__(self, *, name: str, model: str, base_url: str = "",
                 api_key: str = "", timeout: float = 60.0, max_tokens: int = 0):
        self.name = name
        self.model = model
        self.base_url = (base_url or "").rstrip("/")
        self._api_key = api_key or ""
        self.timeout = timeout
        self.max_tokens = max_tokens  # 0 = nessun tetto

    def __repr__(self) -> str:  # niente chiave: repr finisce nei log
        return f"<{type(self).__name__} name={self.name!r} model={self.model!r}>"

    @property
    def has_key(self) -> bool:
        return bool(self._api_key)

    @abstractmethod
    def chat(self, messages: list[Message], tools: list[ToolSpec] | None = None,
             stream: bool = False) -> ProviderResponse | ChatStream:
        """Una chiamata di chat. `tools` sono ToolSpec normalizzati; il provider
        li traduce nel suo formato nativo e normalizza i tool-call in uscita."""

    def list_models(self) -> list[str]:
        """Modelli disponibili presso il provider (best-effort: [] se non
        supportato o irraggiungibile)."""
        return []

    def ping(self) -> bool:
        """Sonda di salute rapida (self-test, spec self-healing). Default:
        nessuna sonda (True); i provider concreti la implementano davvero."""
        return True
