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
