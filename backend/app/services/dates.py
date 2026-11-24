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
