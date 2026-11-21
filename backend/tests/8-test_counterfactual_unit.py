"""Test unitari puri per services/counterfactual.py (matematica degli scenari)."""
from app.services.counterfactual import scenario_estimate, select_rates


def test_select_rates_all_best_worst():
    weekly = {(2026, 40): 2, (2026, 41): 5, (2026, 42): 1}
    assert select_rates(weekly, "all") == [2.0, 5.0, 1.0]
    assert select_rates(weekly, "best") == [5.0]
    assert select_rates(weekly, "worst") == [1.0]
    assert select_rates({}, "all") == []


def test_scenario_velocity_factor_halves_time():
    base = scenario_estimate(10, [2.0], velocity_factor=1.0)
    fast = scenario_estimate(10, [2.0], velocity_factor=2.0)
    assert base["central"] == 5.0
    assert fast["central"] == 2.5
    assert fast["low"] <= fast["central"] <= fast["high"]


def test_scenario_extra_completed_reduces_remaining():
    est = scenario_estimate(10, [2.0], extra_completed_tasks=6)
    assert est["remaining_effective"] == 4
    assert est["central"] == 2.0


def test_scenario_all_done_is_zero_weeks():
    est = scenario_estimate(3, [2.0], extra_completed_tasks=5)
    assert est["possible"] and est["central"] == 0.0
    assert est["remaining_effective"] == 0


def test_scenario_without_observed_rates_is_refused():
    est = scenario_estimate(10, [], velocity_factor=2.0)
    assert not est["possible"]
    assert "velocita" in est["reason"]


def test_scenario_tiny_factor_stays_positive():
    est = scenario_estimate(10, [1.0], velocity_factor=0.01)
    assert est["possible"] and est["high"] > 0.0
