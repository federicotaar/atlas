"""Sicurezza degli strumenti (spec sez. 4): blocklist comandi distruttivi,
sandbox del workspace, filtro segreti per log e messaggi.

Questo modulo fa parte dello strato di sicurezza che l'agente non puo' MAI
modificare da solo (hard limit spec self-improving)."""
from __future__ import annotations

import re
from pathlib import Path

# Comandi distruttivi: bloccati SEMPRE, in qualunque permission mode (anche
# yolo). Match su sottostringa normalizzata (spazi compressi, minuscole).
BLOCKLIST: tuple[str, ...] = (
    "rm -rf /", "rm -fr /", "rm -rf /*", "rm -rf ~", "rm -rf $home",
    "mkfs", "mkfs.", "fdisk", "parted",
    "dd of=/dev/", "dd if=/dev/zero of=/dev",
    "> /dev/sd", "of=/dev/sd", "of=/dev/nvme",
    ":(){ :|:& };:", ":(){:|:&};:",
    "shutdown", "poweroff", "reboot", "halt",
    "chmod -r 777 /", "chown -r",
    "mv / ", "mv /*",
    "wipefs", "shred /dev",
    "curl ",  # valutata sotto con regole pipe-to-shell
)


def _normalize(cmd: str) -> str:
    # Spazi compressi, minuscole, virgolette e backslash rimossi: riduce le
    # evasioni lessicali banali (rm "-rf" /, rm -rf \"/\"). Resta un filtro
    # LESSICALE: non e' una barriera di sicurezza completa (variabili, alias,
    # sotto-shell possono nascondere un comando) - il vero controllo resta la
    # conferma utente nei modi ask/auto-safe.
    squashed = " ".join(cmd.lower().split())
    return squashed.replace('"', "").replace("'", "").replace("\\", "")


# pattern pipe-to-shell: scarica ed esegue (curl ... | sh, wget -O- ... | bash)
_PIPE_SHELL = re.compile(r"(curl|wget)[^|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b")

# rm ricorsivo+forzato su radice/home/glob, con flag in qualunque ordine o
# forma (rm -rf /, rm -fr /, rm -rfv /, rm -r -f /): dopo la normalizzazione
# le virgolette non ostacolano il match.
_RM_FLAGS = re.compile(r"\brm\s+((?:--?\w+\s+)+)(\S+)")


def _rm_targets_root(norm: str) -> bool:
    m = _RM_FLAGS.match(norm + " ")  # tollera target finale
    if not m:
        return False
    flags = m.group(1)
    target = m.group(2)
    return "r" in flags and "f" in flags and target in ("/", "/*", "~", "$home", "*")


def is_blocked(cmd: str) -> str | None:
    """Motivo del blocco, o None se il comando passa la blocklist."""
    norm = _normalize(cmd)
    if _PIPE_SHELL.search(norm):
        return "download eseguito in pipe a una shell (curl|sh e simili)"
    if _rm_targets_root(norm):
        return "cancellazione ricorsiva di radice/home (rm -rf su /, ~ o *)"
    for pattern in BLOCKLIST:
        if pattern == "curl ":
            continue  # curl da solo e' lecito; conta la pipe a shell
        if pattern in norm:
            return f"comando distruttivo bloccato ('{pattern.strip()}')"
    return None


class SandboxError(Exception):
    """Accesso fuori dal workspace senza approvazione."""


def resolve_in_workspace(workspace: Path, path: str) -> tuple[Path, bool]:
    """Risolve un path dentro il workspace. Ritorna (path_assoluto, dentro):
    dentro=False se il path esce dalla sandbox (serve approvazione esplicita)."""
    base = workspace.resolve()
    target = (base / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    try:
        target.relative_to(base)
        return target, True
    except ValueError:
        return target, False


class SecretFilter:
    """Tiene i valori segreti (chiavi API da .env) fuori da log, contesto
    del modello e messaggi: ogni occorrenza viene sostituita con [REDACTED].
    I valori non vengono mai esposti dal filtro stesso."""

    def __init__(self, secrets: list[str] | None = None):
        self._secrets: set[str] = {s for s in (secrets or []) if s and len(s) >= 8}

    def add(self, secret: str) -> None:
        if secret and len(secret) >= 8:
            self._secrets.add(secret)

    def redact(self, text: str) -> str:
        out = text
        for s in self._secrets:
            if s in out:
                out = out.replace(s, "[REDACTED]")
        return out
