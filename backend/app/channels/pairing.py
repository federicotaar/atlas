"""Pairing dei mittenti: come l'allowlist si popola SENZA cercare ID a mano
e SENZA bot di terze parti.

Modello (decisione di sicurezza):
- il bot non risponde MAI a un mittente non autorizzato (niente oracolo:
  chi trova il bot non scopre nulla, spec sez. 6);
- quando arriva un messaggio da uno sconosciuto, l'evento viene solo
  registrato qui, in locale, come richiesta di pairing in sospeso;
- l'approvazione avviene UNICAMENTE sulla superficie locale fidata (la CLI
  sulla macchina di Fred, comando /pairing): mai dalla chat, cosi' uno
  sconosciuto non puo' approvare se stesso;
- si autorizza l'ID numerico stabile di Telegram (l'username e' mutabile e
  vale solo come aiuto visivo per riconoscersi);
- le richieste scadono (default 24h) e sono deduplicate per (canale, id).

Stato in data/pairing.json: {"pending": [...], "paired": {canale: [id]}}.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

PENDING_TTL = 24 * 3600
MAX_PENDING = 50


class PairingStore:
    def __init__(self, path: Path, *, ttl: float = PENDING_TTL,
                 max_pending: int = MAX_PENDING):
        self._path = path
        self._ttl = ttl
        self._max = max_pending
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    # --- persistenza --------------------------------------------------------

    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("pending", [])
        data.setdefault("paired", {})
        return data

    def _save(self, data: dict) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self._path)

    # --- richieste in sospeso -----------------------------------------------

    def record(self, channel: str, sender: str, display: str = "") -> None:
        """Registra (o rinfresca) una richiesta di pairing. Nessuna risposta
        viene mai inviata al mittente."""
        now = time.time()
        with self._lock:
            data = self._load()
            pending = [p for p in data["pending"]
                       if now - p.get("ts", 0) <= self._ttl
                       and not (p["channel"] == channel and p["sender"] == sender)]
            pending.append({"channel": channel, "sender": sender,
                            "display": display, "ts": now})
            data["pending"] = pending[-self._max:]
            self._save(data)

    def pending(self) -> list[dict]:
        now = time.time()
        with self._lock:
            return [p for p in self._load()["pending"]
                    if now - p.get("ts", 0) <= self._ttl]

    def reject(self, channel: str, sender: str) -> bool:
        with self._lock:
            data = self._load()
            before = len(data["pending"])
            data["pending"] = [p for p in data["pending"]
                               if not (p["channel"] == channel and p["sender"] == sender)]
            changed = len(data["pending"]) != before
            if changed:
                self._save(data)
            return changed

    # --- approvazione --------------------------------------------------------

    def approve(self, channel: str, sender: str) -> dict | None:
        """Sposta un pending tra i paired. Ritorna la voce o None."""
        with self._lock:
            data = self._load()
            entry = next((p for p in data["pending"]
                          if p["channel"] == channel and p["sender"] == sender), None)
            if entry is None:
                return None
            data["pending"] = [p for p in data["pending"]
                               if not (p["channel"] == channel and p["sender"] == sender)]
            ids = data["paired"].setdefault(channel, [])
            if sender not in ids:
                ids.append(sender)
            self._save(data)
            return entry

    def paired_ids(self, channel: str) -> set[str]:
        with self._lock:
            return set(self._load()["paired"].get(channel, []))
