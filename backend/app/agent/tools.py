"""Strumenti dell'agente (spec sez. 3): read_file, write_file, list_dir, shell.

Ogni strumento e' una funzione(args: dict, ctx: ToolContext) -> str con il suo
ToolSpec (nome, descrizione, JSON schema) per il provider. Il contesto porta
workspace e filtro segreti: i risultati passano dal SecretFilter prima di
tornare al modello, cosi' una chiave letta per sbaglio non entra nel contesto.
L'esecuzione vera e' nel loop: qui ci sono definizione ed effetto, li' il
controllo permessi e il logging.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .execsafe import validate as validate_command
from .safety import SecretFilter, is_blocked, resolve_in_workspace

SHELL_TIMEOUT = 60  # secondi fissi: un comando appeso non blocca l'agente
MAX_OUTPUT = 4000   # caratteri restituiti al modello per chiamata


@dataclass
class ToolContext:
    workspace: Path
    secrets: SecretFilter


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any], ToolContext], str]
    read_only: bool = False  # True: nessuna modifica a file o sistema

    def spec(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters}


def _clip(text: str) -> str:
    if len(text) > MAX_OUTPUT:
        return text[:MAX_OUTPUT] + f"\n... [troncato: {len(text)} caratteri totali]"
    return text


def _read_file(args: dict[str, Any], ctx: ToolContext) -> str:
    path = str(args.get("path", "")).strip()
    if not path:
        return "errore: path vuoto"
    target, _ = resolve_in_workspace(ctx.workspace, path)
    if not target.is_file():
        return f"errore: {path} non e' un file"
    try:
        return _clip(target.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        return f"errore lettura: {exc}"


def _write_file(args: dict[str, Any], ctx: ToolContext) -> str:
    path = str(args.get("path", "")).strip()
    content = str(args.get("content", ""))
    if not path:
        return "errore: path vuoto"
    target, _ = resolve_in_workspace(ctx.workspace, path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"errore scrittura: {exc}"
    return f"scritto {target} ({len(content)} caratteri)"


def _list_dir(args: dict[str, Any], ctx: ToolContext) -> str:
    path = str(args.get("path", ".")).strip() or "."
    target, _ = resolve_in_workspace(ctx.workspace, path)
    if not target.is_dir():
        return f"errore: {path} non e' una cartella"
    entries = []
    for entry in sorted(target.iterdir(), key=lambda e: e.name)[:200]:
        kind = "dir " if entry.is_dir() else "file"
        entries.append(f"{kind} {entry.name}")
    return "\n".join(entries) or "(cartella vuota)"


def _scrubbed_env() -> dict[str, str]:
    """Ambiente dei comandi shell SENZA segreti: i processi figli non ereditano
    chiavi API/token/segreti (enforcement reale, non lessicale: `env`,
    `cat $VAR`, subshell e script figli non vedono nulla). Restano solo le
    variabili innocue necessarie a eseguire comandi comuni."""
    keep = {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "USER",
            "LOGNAME", "SHELL", "TMPDIR", "SYSTEMROOT", "COMSPEC"}
    return {k: v for k, v in os.environ.items()
            if k in keep and not any(
                mark in k.upper() for mark in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))}


# git legge configurazione da /etc, $HOME e .git/config: alias, diff esterni,
# textconv, pager possono eseguire processi arbitrari, e .git/config sarebbe
# scrivibile via write_file. Neutralizziamo TUTTE le sorgenti: il git della
# superficie ristretta gira senza nessuna configurazione ereditabile.
_GIT_NEUTRAL_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_LOCAL": "/dev/null",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "GIT_EXTERNAL_DIFF": "",
    "GIT_DIFF_OPTS": "",
    "GIT_SSH": "/bin/false",
    "GIT_SSH_COMMAND": "/bin/false",
    "GIT_ASKPASS": "/bin/false",
    "SSH_ASKPASS": "/bin/false",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_EDITOR": "/bin/false",
    "EDITOR": "/bin/false",
    "VISUAL": "/bin/false",
}


def _tool_env() -> dict[str, str]:
    env = _scrubbed_env()
    env.update(_GIT_NEUTRAL_ENV)
    return env


def _is_protected_path(target: Path) -> bool:
    norm = str(target).replace("\\", "/")
    if "/.git/" in norm or norm.endswith("/.git"):
        return True  # config/hooks/attributes del repo: mai scrivibili dai tool
    if any(part in norm for part in ("app/agent/", "app/agent_cli.py")):
        return True
    return target.name == ".env"


def _shell(args: dict[str, Any], ctx: ToolContext) -> str:
    command = str(args.get("command", "")).strip()
    if not command:
        return "errore: comando vuoto"
    # Blocklist: gia' controllata dal loop, resta come ultima rete.
    reason = is_blocked(command)
    if reason:
        return f"BLOCCATO: {reason}"
    # NESSUNA shell. argv diretto, allowlist, path
    # risolti (symlink compresi) prima dell'esecuzione: la prevenzione e'
    # strutturale, non un filtro sul testo del comando.
    check = validate_command(command, ctx.workspace,
                             resolve_in_workspace, _is_protected_path)
    if not check.ok:
        return f"NON DISPONIBILE: {check.reason}"
    try:
        proc = subprocess.run(
            check.argv, shell=False, cwd=str(ctx.workspace),
            capture_output=True, text=True, timeout=SHELL_TIMEOUT,
            errors="replace", env=_tool_env(), stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return f"errore: comando oltre il timeout di {SHELL_TIMEOUT}s, terminato"
    except FileNotFoundError:
        return f"errore: {check.argv[0]} non trovato su questo sistema"
    out = (proc.stdout or "") + (proc.stderr or "")
    return _clip(f"exit {proc.returncode}\n{out}" if out else f"exit {proc.returncode}")


READ_FILE = Tool(
    name="read_file",
    description="Legge un file di testo dal workspace dell'agente.",
    parameters={"type": "object",
                "properties": {"path": {"type": "string", "description": "percorso del file, relativo al workspace"}},
                "required": ["path"]},
    run=_read_file,
    read_only=True,
)

WRITE_FILE = Tool(
    name="write_file",
    description="Scrive (o sovrascrive) un file di testo nel workspace dell'agente.",
    parameters={"type": "object",
                "properties": {
                    "path": {"type": "string", "description": "percorso del file, relativo al workspace"},
                    "content": {"type": "string", "description": "contenuto completo del file"},
                },
                "required": ["path", "content"]},
    run=_write_file,
)

LIST_DIR = Tool(
    name="list_dir",
    description="Elenca il contenuto di una cartella del workspace.",
    parameters={"type": "object",
                "properties": {"path": {"type": "string", "description": "cartella, default '.'"}},
                "required": []},
    run=_list_dir,
    read_only=True,
)

SHELL = Tool(
    name="shell",
    description=("Esegue un comando dalla superficie ristretta nel workspace: solo "
                 "comandi di lettura/ispezione allowlistati (ls, cat, grep su file "
                 "espliciti, git status/log...), senza pipe, redirect o variabili - "
                 "non esiste una shell. Le scritture passano da write_file. Comandi "
                 "fuori superficie: non disponibili. Soggetto a permessi e blocklist."),
    parameters={"type": "object",
                "properties": {"command": {"type": "string", "description": "comando da eseguire"}},
                "required": ["command"]},
    run=_shell,
)

ALL_TOOLS: tuple[Tool, ...] = (READ_FILE, WRITE_FILE, LIST_DIR, SHELL)
TOOLS_BY_NAME: dict[str, Tool] = {t.name: t for t in ALL_TOOLS}
