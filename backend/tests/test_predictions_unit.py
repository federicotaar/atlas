"""Test unitari puri per services/predictions.py (matematica delle stime)."""
from datetime import datetime, timezone

from app.services.predictions import _interval, _week_key, brier_score


def test_interval_single_rate_is_a_point():
    central, low, high = _interval(8, [4.0])
    assert central == low == high == 2.0


def test_interval_spread_orders_bounds():
    central, low, high = _interval(6, [2.0, 4.0])
    assert central == 2.0
    assert low == 1.5   # settimane alla velocita media+spread (4)
    assert high == 3.0  # settimane alla velocita media-spread (2)
    assert low <= central <= high


def test_interval_slow_rate_never_zero_or_negative():
    central, low, high = _interval(2, [1.0, 3.0])
    assert high > 0.0 and low <= central <= high

from app.services.predictions import calibration_buckets  # noqa: E402


def test_calibration_buckets_edges():
    out = calibration_buckets([(0.95, 1.0)])
    assert out["0.9-1.0"] == {"count": 1, "avg_probability": 0.95, "frequency": 1.0}


def test_calibration_buckets_aggregate():
    out = calibration_buckets([(0.05, 0.0), (0.07, 1.0)])
    assert out["0.0-0.1"] == {"count": 2, "avg_probability": 0.06, "frequency": 0.5}


def test_calibration_buckets_empty():
    assert calibration_buckets([]) == {}

def test_brier_score_known_values():
    assert brier_score(1.0, True) == 0.0
    assert brier_score(0.0, False) == 0.0
    assert brier_score(0.8, True) == 0.04
    assert brier_score(0.8, False) == 0.64


def test_week_key_naive_is_utc():
    naive = datetime(2026, 11, 19, 12, 0)
    aware = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    assert _week_key(naive) == _week_key(aware) == (2026, 47)


def test_week_key_iso_year_boundary():
    # 2021-01-01 appartiene alla settimana 53 dell'anno ISO 2020.
    assert _week_key(datetime(2021, 1, 1, tzinfo=timezone.utc)) == (2020, 53)
