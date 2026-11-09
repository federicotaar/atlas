"""Fuso orario dell'installazione: esplicito, configurabile, persistito.

Perche': scheduler, ore silenziose e parsing in linguaggio naturale devono
riferirsi al fuso dell'UTENTE, non a quello del processo. Il fuso attivo e'
un nome IANA (es. "Europe/Rome") risolto con zoneinfo.

Precedenza della configurazione:
1. workspace/config/app.json, chiave "timezone" (scelta esplicita, persiste
   tra i riavvii e vince sul fuso del sistema);
2. rilevamento dal sistema (variabile TZ, poi symlink /etc/localtime);
3. "UTC" come ripiego esplicito e documentato.

Regole DST deterministiche (cambio ora legale), applicate da wall_to_epoch:
- orario INESISTENTE (primavera, es. 02:30 del 29 marzo a Roma): spostato in
  avanti del salto -> 03:30. Il job non si perde e non parte in anticipo.
- orario AMBIGUO (autunno, es. 02:30 del 25 ottobre a Roma): vale la PRIMA
  occorrenza. Il job non parte mai due volte.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from datetime import timezone as _dt_tz
from pathlib import Path
from zoneinfo import ZoneInfo

FALLBACK_ZONE = "UTC"


def _valid(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


def detect_system_zone() -> str | None:
    """Nome IANA del fuso del sistema operativo, se determinabile."""
    env = (os.environ.get("TZ") or "").lstrip(":").strip()
    if env and _valid(env):
        return env
    try:
        link = os.readlink("/etc/localtime")
        marker = "zoneinfo/"
        if marker in link:
            name = link.split(marker, 1)[1]
            if _valid(name):
                return name
    except OSError:
        pass
    return None


def _config_path(workspace: Path) -> Path:
    return workspace / "config" / "app.json"


def load_timezone_name(workspace: Path) -> str:
    """Fuso attivo dell'installazione (nome IANA), per precedenza."""
    try:
        data = json.loads(_config_path(workspace).read_text())
        name = str(data.get("timezone", "")).strip()
        if name:
            if _valid(name):
                return name
            # nome non valido in config: non bloccare l'avvio, si ripiega
    except (OSError, ValueError, TypeError):
        pass
    return detect_system_zone() or FALLBACK_ZONE


def get_zone(workspace: Path) -> ZoneInfo:
    return ZoneInfo(load_timezone_name(workspace))


def save_timezone(workspace: Path, name: str) -> str:
    """Imposta il fuso esplicito (persistito). Solleva ValueError se il nome
    non e' un fuso IANA valido."""
    name = name.strip()
    if not _valid(name):
        raise ValueError(f"fuso orario IANA non valido: {name!r}")
    path = _config_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    data["timezone"] = name
    path.write_text(json.dumps(data, indent=2) + "\n")
    return name


def wall_to_epoch(dt: datetime, zone: ZoneInfo) -> float:
    """Data/ora di parete (naive) -> epoch, con regole DST deterministiche."""
    first = dt.replace(tzinfo=zone, fold=0)
    second = dt.replace(tzinfo=zone, fold=1)
    if first.utcoffset() == second.utcoffset():
        return first.timestamp()
    back = first.astimezone(_dt_tz.utc).astimezone(zone).replace(tzinfo=None)
    if back == dt:
        return first.timestamp()  # ambiguo: PRIMA occorrenza
    # inesistente: sposta in avanti del salto, con l'offset post-transizione
    shifted = dt + (second.utcoffset() - first.utcoffset())
    return shifted.replace(tzinfo=zone, fold=1).timestamp()


def epoch_to_wall(ts: float, zone: ZoneInfo) -> datetime:
    return datetime.fromtimestamp(ts, zone)


def fmt_wall(ts: float | None, zone: ZoneInfo) -> str:
    if ts is None:
        return "-"
    return epoch_to_wall(ts, zone).strftime("%Y-%m-%d %H:%M")


def now_wall(zone: ZoneInfo) -> datetime:
    return datetime.now(zone)
