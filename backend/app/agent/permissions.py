"""Modi di permesso (spec sez. 4): ask (default), auto-safe, yolo.

Regole fisse, qualunque sia il modo:
- blocklist (safety.is_blocked): SEMPRE negato, anche in yolo;
- fuori dal workspace: SEMPRE da approvare esplicitamente;
- i file dello strato di sicurezza non si toccano (hard limit self-improving:
  la scrittura su app/agent/{safety,permissions}.py e' vietata ai tool).
"""
from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Any

from .execsafe import validate as validate_command
from .safety import is_blocked, resolve_in_workspace
from .tools import TOOLS_BY_NAME, _is_protected_path

# Strato di sicurezza: TUTTO app/agent/ + agent_cli.py read-only per gli
# strumenti dell'agente (spec sez. 8, hard limit): safety, permissions,
# execsafe, seal, loop, tools - chi applica il confine non puo' riscriverlo.
PROTECTED_PARTS = ("app/agent/", "app/agent_cli.py", "/.git/")

# .env mai in pasto ai tool (spec sez. 6: mai eco di chiavi o .env). Il token
# deve essere esattamente ".env": .env.example resta leggibile.
_ENV_TOKEN = re.compile(r"(?<![\w.\-/])\.env(?![\w.\-])")


class Mode(Enum):
    ASK = "ask"            # default: conferma ogni azione shell e di scrittura
    AUTO_SAFE = "auto-safe"  # letture automatiche, il resto chiede
    YOLO = "yolo"          # nessuna conferma (esplicito, mai default)


class Decision(Enum):
    RUN = "run"        # esegui senza chiedere
    ASK = "ask"        # chiedi conferma all'utente
    DENY = "deny"      # vietato sempre (blocklist, file protetti)


def parse_mode(text: str) -> Mode:
    for mode in Mode:
        if mode.value == text.strip().lower():
            return mode
    raise ValueError(f"modo sconosciuto: {text!r} (ask, auto-safe, yolo)")


def _is_protected(workspace: Path, path: str) -> bool:
    target, _ = resolve_in_workspace(workspace, path)
    norm = str(target).replace("\\", "/")
    return any(part in norm for part in PROTECTED_PARTS)


def _touches_protected(command: str) -> str | None:
    """Comandi shell che puntano a file protetti o a .env: motivo o None."""
    norm = command.replace("\\", "/").lower()
    for part in PROTECTED_PARTS:
        if part in norm:
            return f"il comando tocca {part}: strato di sicurezza read-only"
    if _ENV_TOKEN.search(norm):
        return "il comando tocca .env: mai in lettura/scrittura per i tool"
    return None


def _shell_path_escape(command: str, workspace: Path) -> bool:
    """Euristica: il comando cita percorsi fuori dal workspace (assoluti o
    con ..). NON e' contenimento: cwd non imprigiona una shell. Serve per
    chiedere conferma esplicita in yolo; nei modi ask/auto-safe ogni shell
    la chiede gia'. URL e flag non contano come percorsi."""
    base = str(workspace).lower()
    for token in command.replace('"', " ").replace("'", " ").split():
        low = token.lower()
        if "://" in low or low.startswith("-"):
            continue
        if ".." in token:
            return True
        if low.startswith("/") and not low.startswith(base):
            return True
    return False


def decide(mode: Mode, tool_name: str, args: dict[str, Any],
           workspace: Path) -> tuple[Decision, str]:
    """Ritorna (decisione, motivo). La motivazione finisce nel log azioni."""
    if tool_name not in TOOLS_BY_NAME:
        return Decision.DENY, f"strumento sconosciuto: {tool_name}"

    if tool_name == "shell":
        command = str(args.get("command", ""))
        reason = is_blocked(command)
        if reason:
            return Decision.DENY, f"blocklist: {reason}"
        protected = _touches_protected(command)
        if protected:
            return Decision.DENY, protected
        # Superficie ristretta: validazione strutturale PRIMA dell'esecuzione.
        check = validate_command(command, workspace,
                                 resolve_in_workspace, _is_protected_path)
        if not check.ok:
            return Decision.DENY, check.reason
        if check.outside_paths:
            # Fuori workspace: approvazione esplicita del SINGOLO comando in
            # ogni modo (yolo compreso); non sblocca nient'altro.
            return Decision.ASK, ("comando con percorsi fuori dal workspace "
                                  f"({', '.join(check.outside_paths)}): "
                                  "approvi questo comando specifico?")

    if tool_name in ("write_file", "read_file"):
        path = str(args.get("path", ""))
        if _is_protected(workspace, path):
            return Decision.DENY, "file dello strato di sicurezza: read-only per l'agente"
        if _ENV_TOKEN.search(path.replace("\\", "/").lower()):
            return Decision.DENY, ".env mai in lettura/scrittura per i tool"

    # Fuori dal workspace: serve approvazione esplicita in OGNI modo.
    for key in ("path",):
        if key in args:
            _, inside = resolve_in_workspace(workspace, str(args[key]))
            if not inside:
                return Decision.ASK, "fuori dal workspace: serve la tua approvazione"

    if mode is Mode.YOLO:
        if tool_name == "shell" and _shell_path_escape(
                str(args.get("command", "")), workspace):
            return Decision.ASK, "yolo: il comando cita percorsi fuori dal workspace"
        return Decision.RUN, "yolo: nessuna conferma"
    # ask e auto-safe coincidono qui: letture libere, scritture/shell chiedono
    # (spec sez. 4: ask conferma ogni azione shell e di scrittura; auto-safe
    # esegue da solo solo le letture).
    if TOOLS_BY_NAME[tool_name].read_only:
        return Decision.RUN, f"{mode.value}: azione di sola lettura"
    return Decision.ASK, f"{mode.value}: azione {tool_name} da confermare"
