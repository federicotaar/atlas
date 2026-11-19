"""Test unitari puri per services/dates.py."""
from datetime import datetime, timedelta, timezone

from app.services.dates import aware


def test_naive_becomes_utc():
    out = aware(datetime(2026, 11, 19, 12, 0))
    assert out.tzinfo is timezone.utc
    assert out.replace(tzinfo=None) == datetime(2026, 11, 19, 12, 0)


def test_existing_tz_is_kept():
    dt = datetime(2026, 11, 19, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    assert aware(dt) is dt


def test_aware_is_idempotent():
    once = aware(datetime(2026, 1, 1))
    assert aware(once) is once
