"""Utility date/ora condivise dai servizi."""
from datetime import datetime, timezone


def aware(dt: datetime) -> datetime:
    """Rende il datetime consapevole: i naive sono interpretati come UTC.

    I timestamp salvati da SQLite perdono il fuso; trattarli come UTC e' la
    convenzione usata in tutto il backend."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
