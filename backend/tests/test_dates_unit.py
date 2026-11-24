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


def test_days_between_mixes_naive_and_aware():
    from app.services.dates import days_between
    a = datetime(2026, 11, 1, 12, 0)
    b = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    assert days_between(a, b) == 3
    assert days_between(b, a) == -3


def test_days_between_drops_partial_days():
    from app.services.dates import days_between
    assert days_between(datetime(2026, 11, 1, 12), datetime(2026, 11, 2, 11, 59)) == 0
