"""Versioning git delle auto-modifiche e rollback (spec sez. 8, stage 14).

Ogni auto-modifica (skill salvate, tool generati) e' un commit con messaggio
chiaro, confinato a skills/ e tools_generated/ - l'agente non modifica MAI
il proprio codice (app/), la sicurezza, .env o i log.

/rollback annulla l'ultima auto-modifica: revert del commit piu' recente
che tocca SOLO quei percorsi; un commit che tocca altro non si tocca.
Rollback automatico se il self-test fallisce subito dopo un'attivazione.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

SELFMOD_PATHS = ("skills/", "tools_generated/")


def _git(workspace: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=workspace, capture_output=True, text=True,
        timeout=30)


def ensure_repo(workspace: Path) -> bool:
    """Il workspace dei dati e' un repo git a se' (versiona SOLO
    skills/, tools_generated/ e habits.md, non i database)."""
    if (workspace / ".git").exists():
        return True
    if _git(workspace, "init", "-q").returncode != 0:
        return False
    gitignore = workspace / ".gitignore"
    gitignore.write_text(
        "# dati runtime: non versionati\n*.db\n*.db-journal\ndata/\nlogs/\n"
        "backups/\n.env\n__pycache__/\n.skills.json.bak\n"
        "# si versionano solo le auto-modifiche\n!skills/\n!tools_generated/\n"
        "!habits.md\n")
    _git(workspace, "add", ".gitignore")
    _git(workspace, "-c", "user.name=ATLAS agent",
         "-c", "user.email=atlas-agent@local",
         "commit", "-q", "-m", "init: versioning delle auto-modifiche")
    return True


def commit_selfmod(workspace: Path, message: str) -> str | None:
    """Committa skills/, tools_generated/ e habits.md. Ritorna lo sha del
    commit o None se non c'era nulla da committare. Ogni auto-modifica e'
    un commit con messaggio chiaro (spec)."""
    if not ensure_repo(workspace):
        return None
    paths = [p for p in ("skills/", "tools_generated/", "habits.md")
             if (workspace / p).exists()]
    if not paths:
        return None
    _git(workspace, "add", *paths)
    status = _git(workspace, "status", "--porcelain").stdout.strip()
    if not status:
        return None
    proc = _git(workspace, "-c", "user.name=ATLAS agent",
                "-c", "user.email=atlas-agent@local",
                "commit", "-q", "-m", f"atlas: {message}")
    if proc.returncode != 0:
        return None
    return _git(workspace, "rev-parse", "HEAD").stdout.strip()


def last_selfmod_commit(workspace: Path) -> str | None:
    """L'ultimo commit che tocca SOLO i percorsi delle auto-modifiche."""
    proc = _git(workspace, "log", "--format=%H", "-10")
    if proc.returncode != 0:
        return None
    for sha in proc.stdout.split():
        files = _git(workspace, "show", "--format=", "--name-only",
                     sha).stdout.split()
        if files and all(any(f.startswith(p) or f == "habits.md"
                             for p in SELFMOD_PATHS) for f in files):
            return sha
    return None


def rollback(workspace: Path) -> tuple[bool, str]:
    """Annulla l'ultima auto-modifica con git revert. Rifiuta commit che
    toccano qualcosa oltre skills/, tools_generated/ e habits.md."""
    sha = last_selfmod_commit(workspace)
    if sha is None:
        return False, "nessuna auto-modifica da annullare"
    proc = _git(workspace, "-c", "user.name=ATLAS agent",
                "-c", "user.email=atlas-agent@local",
                "revert", "--no-edit", sha)
    if proc.returncode != 0:
        return False, f"revert fallito: {proc.stderr.strip()[:200]}"
    return True, f"annullata l'auto-modifica {sha[:8]}"
