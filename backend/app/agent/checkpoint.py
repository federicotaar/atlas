"""Checkpoint dei task lunghi (spec sez. 8, build order 12).

La CLI salva la conversazione in corso dopo ogni risposta completata; se il
processo muore e riparte, propone di riprendere da dov'era invece di
ricominciare. Un checkpoint vecchio (>24h) non viene proposto.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

MAX_AGE_SECONDS = 24 * 3600


class CheckpointStore:
    def __init__(self, path: Path):
        self._path = path

    def save(self, history: list[dict], note: str = "",
             now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"ts": now, "note": note, "history": history[-40:]}
        self._path.write_text(json.dumps(payload, ensure_ascii=False))

    def load(self, now: float | None = None) -> dict | None:
        """Il checkpoint se esiste ed e' fresco, altrimenti None."""
        now = time.time() if now is None else now
        if not self._path.exists():
            return None
        try:
            payload = json.loads(self._path.read_text())
        except ValueError:
            return None
        if now - payload.get("ts", 0) > MAX_AGE_SECONDS:
            return None
        if not payload.get("history"):
            return None
        return payload

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)
