"""Astrazione canali (spec sez. 6): ogni canale alimenta lo stesso loop
agente e la stessa memoria; la sicurezza dei canali e' CENTRALE qui, non
delegata ai singoli canali.

Sicurezza (spec sez. 6, "critical"):
- allowlist per canale: mittenti non in lista IGNORATI in silenzio (niente
  risposta, niente loop; l'evento resta solo nel log canali);
- azioni shell/scrittura chieste da una chat vanno confermate rispondendo
  "yes" NELLA STESSA chat, in QUALUNQUE modo permessi: i task nati da un
  canale girano quindi sempre in modo ASK (le letture restano libere);
- rate limit per mittente;
- mai eco di chiavi o .env: tutto l'outbound passa dal SecretFilter;
- ogni messaggio in entrata e in uscita finisce in logs/channels.log.
"""
from __future__ import annotations

import json
import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..agent.actionlog import ActionLog
from ..agent.commands import CommandContext, handle_command
from ..agent.loop import run_agent_loop
from ..agent.memory import MemoryStore, memories_context
from ..agent.permissions import Mode
from ..agent.safety import SecretFilter
from ..agent.seal import SealGuard
from ..agent.tools import ToolContext
from ..providers.base import AllProvidersFailed, ProviderError
from ..providers.registry import ProviderRegistry
from .pairing import PairingStore


@dataclass(frozen=True)
class ChannelMessage:
    sender: str
    channel: str
    text: str
    display: str = ""  # nome/username mostrato nel pairing locale


class Channel(ABC):
    """Contratto della spec: start(), send(to, text), on_message(handler)."""

    name: str

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def send(self, to: str, text: str) -> None: ...

    @abstractmethod
    def on_message(self, handler: Callable[[ChannelMessage], None]) -> None: ...


class ChannelLog:
    """logs/channels.log: una riga JSON per messaggio, segreti redatti."""

    def __init__(self, path: Path, secrets: SecretFilter):
        self._path = path
        self._secrets = secrets
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, direction: str, channel: str, sender: str, text: str) -> None:
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                 "direction": direction, "channel": channel,
                 "sender": sender, "text": self._secrets.redact(text)}
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


class RateLimiter:
    """Finestra scorrevole per mittente (default 20 messaggi/minuto)."""

    def __init__(self, max_per_minute: int = 20):
        self._max = max_per_minute
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, sender: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[sender]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self._max:
                return False
            hits.append(now)
            return True


class _Conversation:
    def __init__(self) -> None:
        self.lock = threading.Lock()          # un task alla volta
        self.task_running = False
        self.cancel: threading.Event | None = None
        self.pending_question = threading.Event()
        self.confirm_answer: str | None = None


class ChannelHub:
    """Smista i messaggi dei canali verso loop, memoria e conferme in-chat."""

    def __init__(self, *, registry: ProviderRegistry, active_getter,
                 ctx: ToolContext, store: MemoryStore, log: ActionLog,
                 seal: SealGuard | None, secrets: SecretFilter,
                 channels_log: ChannelLog,
                 allowed: dict[str, set[str]],
                 set_active=None, describe_registry=None,
                 pairing: PairingStore | None = None,
                 max_steps: int = 10,
                 rate_per_minute: int = 20,
                 confirm_timeout: float = 120.0):
        self._registry = registry
        self._active_getter = active_getter
        self._set_active = set_active or (lambda name: None)
        self._describe_registry = describe_registry or (
            lambda registry, active, out: out("elenco provider non disponibile qui"))
        self._state = {"mode": Mode.ASK}
        self._ctx = ctx
        self._store = store
        self._log = log
        self._seal = seal
        self._secrets = secrets
        self._channels_log = channels_log
        self._allowed = allowed
        self._pairing = pairing
        self._max_steps = max_steps
        self._rate = RateLimiter(rate_per_minute)
        self._confirm_timeout = confirm_timeout
        self._convos: dict[tuple[str, str], _Conversation] = {}
        self._convos_lock = threading.Lock()

    # --- utilita' -----------------------------------------------------------

    def _convo(self, sender: str, channel: str) -> _Conversation:
        key = (sender, channel)
        with self._convos_lock:
            if key not in self._convos:
                self._convos[key] = _Conversation()
            return self._convos[key]

    @staticmethod
    def is_allowed(sender: str, channel: str, allowed: dict[str, set[str]]) -> bool:
        ids = allowed.get(channel, set())
        return sender in ids

    def approve_pairing(self, channel: str, sender: str) -> dict | None:
        """Approva un pairing in sospeso (chiamato SOLO dalla CLI locale):
        sposta l'id nei paired e lo attiva subito nell'allowlist."""
        if self._pairing is None:
            return None
        entry = self._pairing.approve(channel, sender)
        if entry is not None:
            self._allowed.setdefault(channel, set()).add(sender)
        return entry

    # --- ingresso -----------------------------------------------------------

    def handle(self, msg: ChannelMessage, reply: Callable[[str], None]) -> None:
        """Punto unico di ingresso. `reply` manda testo al mittente sullo
        stesso canale (log outbound + redaction inclusi)."""
        if not self.is_allowed(msg.sender, msg.channel, self._allowed):
            # Challenge monouso (condivisa solo con Fred sul canale autenticato):
            # chi la presenta dimostra il possesso e viene abbinato. Solo DOPO
            # l'abbinamento il bot risponde una prima volta.
            if self._pairing is not None and self._pairing.consume_challenge(
                    msg.text.strip()):
                self._allowed.setdefault(msg.channel, set()).add(msg.sender)
                self._pairing.approve_direct(msg.channel, msg.sender, msg.display)
                self._channels_log.record("in", msg.channel, msg.sender, "<pairing challenge>")
                safe = self._secrets.redact(
                    "Pairing confermato: questo account ora e' autorizzato.")
                self._channels_log.record("out", msg.channel, msg.sender, safe)
                reply(safe)
                return
            # Ignorato in silenzio (spec sez. 6): nessuna risposta, mai.
            # In locale resta solo il MINIMO: richiesta di pairing (id+display,
            # approvabile SOLO dalla CLI) e log senza testo. Il contenuto di un
            # mittente sconosciuto non entra in log ne' nel contesto del modello.
            if self._pairing is not None:
                self._pairing.record(msg.channel, msg.sender, msg.display)
            self._channels_log.record("in", msg.channel, msg.sender,
                                      "<mittente non autorizzato: testo non registrato>")
            return
        if not self._rate.allow(msg.sender):
            return  # oltre il rate limit: ignora in silenzio

        self._channels_log.record("in", msg.channel, msg.sender, msg.text)
        convo = self._convo(msg.sender, msg.channel)

        def send_reply(text: str) -> None:
            safe = self._secrets.redact(text)
            self._channels_log.record("out", msg.channel, msg.sender, safe)
            reply(safe)

        # Una conferma in sospeso cattura il prossimo messaggio del mittente:
        # solo "yes" (spec sez. 6) approva, qualunque altra cosa rifiuta.
        if convo.pending_question.is_set():
            convo.confirm_answer = msg.text.strip().lower()
            convo.pending_question.clear()
            return

        text = msg.text.strip()
        if text.startswith("/") and text != "/stop":
            handled = handle_command(text, CommandContext(
                registry=self._registry, state=self._state, store=self._store,
                out=send_reply, get_active=self._active_getter,
                set_active=self._set_active,
                describe_registry=self._describe_registry))
            if handled:
                return
        if text == "/stop":
            if convo.task_running and convo.cancel is not None:
                convo.cancel.set()
                send_reply("(annullamento richiesto)")
            else:
                send_reply("Nessun task in corso.")
            return

        with convo.lock:
            if convo.task_running:
                send_reply("Un task e' gia' in corso: /stop per annullarlo.")
                return
            convo.task_running = True
            convo.cancel = threading.Event()
        threading.Thread(target=self._run_task, args=(convo, msg, text, send_reply),
                         daemon=True).start()

    # --- esecuzione ---------------------------------------------------------

    def _run_task(self, convo: _Conversation, msg: ChannelMessage,
                  text: str, send_reply: Callable[[str], None]) -> None:
        try:
            send_reply(self._answer(convo, msg, text, send_reply))
        finally:
            with convo.lock:
                convo.task_running = False
                convo.cancel = None

    def _answer(self, convo: _Conversation, msg: ChannelMessage,
                text: str, send_reply: Callable[[str], None]) -> str:
        sender, channel = msg.sender, msg.channel
        history = self._store.history(sender, channel)
        history.append({"role": "user", "content": text})
        notes_block = memories_context(self._store.relevant(text))
        call_messages = ([{"role": "system", "content": notes_block}]
                         if notes_block else []) + history

        def ask_fn(question: str) -> bool:
            # Conferma nella STESSA chat (spec sez. 6): si approva solo con
            # "yes"; timeout o qualunque altra risposta = rifiuto.
            convo.confirm_answer = None
            convo.pending_question.set()
            send_reply(f"{question}\nRispondi \"yes\" per confermare, altro per annullare.")
            deadline = time.monotonic() + self._confirm_timeout
            while convo.pending_question.is_set() and time.monotonic() < deadline:
                time.sleep(0.05)
            approved = convo.confirm_answer == "yes"
            convo.pending_question.clear()
            return approved

        try:
            result = run_agent_loop(
                self._registry, self._active_getter(), call_messages,
                self._ctx, Mode.ASK, self._log,
                ask_fn=ask_fn, max_steps=self._max_steps,
                cancel=convo.cancel, on_delta=None, seal=self._seal)
        except (AllProvidersFailed, ProviderError) as exc:
            return f"Errore: {exc}"
        if result.cancelled:
            return "(task annullato con /stop)"
        final = result.notes[-1] if result.stopped_by_limit else result.text
        self._store.add_message(sender, channel, "user", text)
        self._store.add_message(sender, channel, "assistant", final)
        return final
