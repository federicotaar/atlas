"""config/channels.yaml: abilita/disabilita i canali (spec sez. 6)."""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT = "config/channels.yaml"


def load_channels_config(start: Path | None = None) -> dict:
    """Ritorna {"channels": {...}}; file assente = nessun canale attivo."""
    here = (start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        candidate = folder / DEFAULT
        if candidate.exists():
            data = yaml.safe_load(candidate.read_text(encoding="utf-8"))
            return data or {}
    return {}


def channel_enabled(config: dict, name: str) -> bool:
    return bool(config.get("channels", {}).get(name, {}).get("enabled"))
