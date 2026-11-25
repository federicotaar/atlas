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


def test_end_of_day_is_last_microsecond():
    from app.services.dates import end_of_day
    out = end_of_day(datetime(2026, 11, 19, 8, 30))
    assert (out.hour, out.minute, out.second, out.microsecond) == (23, 59, 59, 999999)
    assert out.tzinfo is timezone.utc
    tz = timezone(timedelta(hours=1))
    assert end_of_day(datetime(2026, 11, 19, 8, 30, tzinfo=tz)).tzinfo is tz


def test_month_start_resets_day_and_time():
    from app.services.dates import month_start
    assert month_start(datetime(2026, 11, 30, 23, 59)) == datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert month_start(datetime(2026, 1, 1)) == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_parse_iso_z_round_trip():
    from app.services.dates import parse_iso_z, to_iso_z
    original = datetime(2026, 11, 19, 12, 5, 9, tzinfo=timezone.utc)
    assert parse_iso_z(to_iso_z(original)) == original


def test_parse_iso_z_accepts_offsets_and_naive_text():
    from app.services.dates import parse_iso_z
    assert parse_iso_z("2026-11-19T12:00:00+02:00").utcoffset() == timedelta(hours=2)
    assert parse_iso_z("2026-11-19T12:00:00").tzinfo is timezone.utc


def test_parse_iso_z_rejects_garbage():
    import pytest
    from app.services.dates import parse_iso_z
    with pytest.raises(ValueError):
        parse_iso_z("domani")


def test_is_same_day_compares_utc_dates():
    from app.services.dates import is_same_day
    assert is_same_day(datetime(2026, 11, 19, 0, 0), datetime(2026, 11, 19, 23, 59))
    assert not is_same_day(datetime(2026, 11, 19, 23, 59), datetime(2026, 11, 20, 0, 0))
    late = datetime(2026, 11, 19, 23, 30, tzinfo=timezone(timedelta(hours=-3)))
    assert is_same_day(late, datetime(2026, 11, 20, 2, 30))


def test_humanize_age_buckets():
    from app.services.dates import humanize_age
    now = datetime(2026, 11, 25, 15, 0)
    assert humanize_age(datetime(2026, 11, 25, 1, 0), now) == "oggi"
    assert humanize_age(datetime(2026, 11, 24, 23, 0), now) == "ieri"
    assert humanize_age(datetime(2026, 11, 22), now) == "3 giorni fa"
    assert humanize_age(datetime(2026, 11, 4), now) == "3 settimane fa"
    assert humanize_age(datetime(2026, 8, 25), now) == "3 mesi fa"


def test_humanize_age_future():
    from app.services.dates import humanize_age
    now = datetime(2026, 11, 25, 15, 0)
    assert humanize_age(datetime(2026, 11, 26), now) == "domani"
    assert humanize_age(datetime(2026, 11, 30), now) == "tra 5 giorni"


def test_iter_days_inclusive_range():
    from app.services.dates import iter_days
    days = iter_days(datetime(2026, 11, 28, 18, 0), datetime(2026, 12, 1, 2, 0))
    assert [d.day for d in days] == [28, 29, 30, 1]
    assert all(d.hour == 0 for d in days)


def test_iter_days_single_and_reversed():
    from app.services.dates import iter_days
    assert len(iter_days(datetime(2026, 11, 28), datetime(2026, 11, 28, 23))) == 1
    assert iter_days(datetime(2026, 11, 29), datetime(2026, 11, 28)) == []
