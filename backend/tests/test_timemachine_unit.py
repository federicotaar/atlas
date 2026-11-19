"""Test unitari puri per i helper della time machine (niente DB)."""
from app.services.timemachine import _choose_bucket_days, _gap_runs


def test_choose_bucket_days_boundary():
    assert _choose_bucket_days(1) == 1
    assert _choose_bucket_days(62) == 1
    assert _choose_bucket_days(63) == 7


def test_gap_runs_no_data():
    assert _gap_runs(5, set()) == [(0, 5)]


def test_gap_runs_all_filled():
    assert _gap_runs(5, {0, 1, 2, 3, 4}) == []


def test_gap_runs_middle_and_edges():
    assert _gap_runs(6, {1, 4}) == [(0, 1), (2, 4), (5, 6)]


def test_gap_runs_single_bucket():
    assert _gap_runs(1, set()) == [(0, 1)]
    assert _gap_runs(1, {0}) == []
