"""Utility date/ora condivise dai servizi."""
from datetime import datetime, timezone


def aware(dt: datetime) -> datetime:
    """Rende il datetime consapevole: i naive sono interpretati come UTC.

    I timestamp salvati da SQLite perdono il fuso; trattarli come UTC e' la
    convenzione usata in tutto il backend."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def days_between(start: datetime, end: datetime) -> int:
    """Giorni interi tra due datetime (naive = UTC); negativo se end precede start."""
    return (aware(end) - aware(start)).days


def start_of_day(dt: datetime) -> datetime:
    """Mezzanotte (stesso fuso) del giorno di dt; i naive restano UTC."""
    return aware(dt).replace(hour=0, minute=0, second=0, microsecond=0)


def week_start(dt: datetime) -> datetime:
    """Lunedi' alle 00:00 della settimana ISO di dt."""
    from datetime import timedelta
    day = start_of_day(dt)
    return day - timedelta(days=day.weekday())


def to_iso_z(dt: datetime) -> str:
    """ISO 8601 in UTC con suffisso Z, senza microsecondi."""
    from datetime import timezone as _tz
    return aware(dt).astimezone(_tz.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def end_of_day(dt: datetime) -> datetime:
    """Ultimo istante (23:59:59.999999) del giorno di dt, nello stesso fuso."""
    return aware(dt).replace(hour=23, minute=59, second=59, microsecond=999999)
