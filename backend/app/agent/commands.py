"""Comandi slash condivisi tra CLI e canali chat (spec sez. 6: /model,
/mode, /remember, /forget devono esistere anche su Telegram/WhatsApp).

/stop e /quit restano per-contesto: la CLI ferma il task del REPL, il canale
ferma il task della conversazione. /help ha un testo per superficie.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..providers.base import ProviderError
from ..providers.registry import ConfigError, ProviderRegistry
from .memory import MemoryStore
from .permissions import parse_mode


@dataclass
class CommandContext:
    registry: ProviderRegistry
    state: dict                       # {"mode": Mode}
    store: MemoryStore
    out: Callable[[str], None]
    get_active: Callable[[], str]
    set_active: Callable[[str], None]
    describe_registry: Callable[[ProviderRegistry, str, Callable[[str], None]], None]


def parse_model_target(arg: str) -> tuple[str, str | None]:
    """'ollama_local:llama3.2:3b' -> ('ollama_local', 'llama3.2:3b')."""
    arg = arg.strip()
    if not arg:
        raise ConfigError("uso: /model use <nome[:modello]>")
    if ":" in arg:
        name, model = arg.split(":", 1)
        if not name.strip() or not model.strip():
            raise ConfigError(f"destinazione non valida: {arg!r}")
        return name.strip(), model.strip()
    return arg, None


def handle_command(line: str, cc: CommandContext) -> bool:
    """True se `line` era un comando condiviso gestito."""
    if line == "/model":
        cc.out(f"provider: {cc.get_active()}  modello: {cc.registry.current_model(cc.get_active())}")
        return True
    if line == "/model list":
        try:
            cc.describe_registry(cc.registry, cc.get_active(), cc.out)
        except ProviderError as exc:
            cc.out(f"Errore: {exc}")
        return True
    if line.startswith("/model use"):
        parts = line.split(maxsplit=2)
        if len(parts) < 3:
            cc.out("Uso: /model use <nome[:modello]>")
            return True
        try:
            name, model = parse_model_target(parts[2])
            cc.registry.spec(name)
        except ConfigError as exc:
            cc.out(f"Errore: {exc}")
            return True
        usable, reason = cc.registry.usable(name)
        if not usable:
            cc.out(f"{name} non e' usabile: {reason}")
            return True
        if model:
            cc.registry.set_model(name, model)
        cc.set_active(name)
        cc.out(f"Provider attivo: {name} (modello {cc.registry.current_model(name)})")
        return True
    if line == "/mode":
        cc.out(f"modo permessi: {cc.state['mode'].value} (ask, auto-safe, yolo)")
        return True
    if line.startswith("/mode "):
        try:
            cc.state["mode"] = parse_mode(line.split(maxsplit=1)[1])
        except ValueError as exc:
            cc.out(f"Errore: {exc}")
            return True
        cc.out(f"modo permessi: {cc.state['mode'].value}")
        return True
    if line.startswith("/remember"):
        try:
            note_id = cc.store.remember(line[len("/remember"):].strip())
        except ValueError as exc:
            cc.out(f"Errore: {exc}")
            return True
        cc.out(f"Ricordato (#{note_id}).")
        return True
    if line.startswith("/forget"):
        try:
            removed = cc.store.forget(line[len("/forget"):].strip())
        except ValueError as exc:
            cc.out(f"Errore: {exc}")
            return True
        # Onesta' semantica: /forget rimuove NOTE, non la cronologia.
        cc.out((f"Dimenticate {removed} note (la cronologia resta)."
                if removed else "Nessuna nota contiene quel testo."))
        return True
    if line == "/memories":
        notes = cc.store.all_memories()
        if not notes:
            cc.out("Nessuna nota salvata. /remember <testo> per aggiungerne una.")
        else:
            for note_id, text in notes:
                cc.out(f"  #{note_id} {text}")
        return True
    return False
