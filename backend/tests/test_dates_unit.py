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


def test_start_of_day_zeroes_time_and_keeps_tz():
    from app.services.dates import start_of_day
    tz = timezone(timedelta(hours=1))
    out = start_of_day(datetime(2026, 11, 19, 15, 42, 7, 999, tzinfo=tz))
    assert out == datetime(2026, 11, 19, 0, 0, tzinfo=tz)


def test_start_of_day_naive_becomes_utc():
    from app.services.dates import start_of_day
    assert start_of_day(datetime(2026, 11, 19, 23, 59)).tzinfo is timezone.utc


def test_week_start_is_monday_midnight():
    from app.services.dates import week_start
    assert week_start(datetime(2026, 11, 22, 18, 0)) == datetime(2026, 11, 16, tzinfo=timezone.utc)
    assert week_start(datetime(2026, 11, 16, 0, 0)) == datetime(2026, 11, 16, tzinfo=timezone.utc)


def test_week_start_crosses_year_boundary():
    from app.services.dates import week_start
    assert week_start(datetime(2027, 1, 1)) == datetime(2026, 12, 28, tzinfo=timezone.utc)


def test_to_iso_z_converts_offset_to_utc():
    from app.services.dates import to_iso_z
    tz = timezone(timedelta(hours=2))
    assert to_iso_z(datetime(2026, 11, 19, 12, 30, 5, 123456, tzinfo=tz)) == "2026-11-19T10:30:05Z"


def test_to_iso_z_naive_is_utc():
    from app.services.dates import to_iso_z
    assert to_iso_z(datetime(2026, 11, 19, 8, 0)) == "2026-11-19T08:00:00Z"


def test_aware_does_not_mutate_input():
    naive = datetime(2026, 11, 19, 9, 0)
    aware(naive)
    assert naive.tzinfo is None
