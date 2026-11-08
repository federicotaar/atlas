"""Registro azioni (spec sez. 4): logs/actions.log, una riga JSON per azione
con timestamp, strumento, argomenti, decisione e risultato. Tutto passa dal
SecretFilter: i segreti non compaiono mai nel log.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .safety import SecretFilter


class ActionLog:
    def __init__(self, path: Path, secrets: SecretFilter):
        self.path = path
        self.secrets = secrets

    def record(self, *, tool: str, args: dict[str, Any], decision: str,
               reason: str, result: str = "") -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool": tool,
            "args": self.secrets.redact(json.dumps(args, ensure_ascii=False, default=str)),
            "decision": decision,
            "reason": reason,
            "result": self.secrets.redact(result)[:500],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
