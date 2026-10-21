from stormshift_v2.optimizer import solve_candidate, validate_assignments
from stormshift_v2.passenger import (
    connection_buffer_minutes,
    connection_risk_margin_minutes,
    is_tight_connection_group,
    risk_adjusted_connection_buffer_minutes,
)
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.schemas import PlanProfile
from stormshift_v2.simulation import simulate_candidate, report_score


def test_connection_buffers_are_stable_and_operationally_bounded():
    values = [connection_buffer_minutes(2026, f"F{index}") for index in range(20)]
    assert values == [connection_buffer_minutes(2026, f"F{index}") for index in range(20)]
    assert all(18 <= value <= 33 for value in values)
    assert len(set(values)) > 1
    assert connection_risk_margin_minutes(4) == 12
    assert connection_risk_margin_minutes(5) > connection_risk_margin_minutes(1)
    assert risk_adjusted_connection_buffer_minutes(25, 4) == 13
    assert is_tight_connection_group(27, 4)
    assert not is_tight_connection_group(28, 4)


def test_profiles_use_common_measured_utility_not_a_guaranteed_sample_order():
    scenario = storm_scenario(seed=2026)
    candidates = {}
    for profile in (PlanProfile.balanced, PlanProfile.passenger, PlanProfile.green):
        candidate, failure = solve_candidate(scenario, profile)
        assert failure is None and candidate is not None
        assert not any(item.startswith("FAIL") for item in validate_assignments(scenario, candidate.assignments))
        candidate.report = simulate_candidate(scenario, candidate, runs=20)
        candidates[profile] = candidate

    balanced = candidates[PlanProfile.balanced]
    passenger = candidates[PlanProfile.passenger]
    green = candidates[PlanProfile.green]
    # Profiles change the CP-SAT surrogate and schedule, not a guarantee about
    # every finite Monte Carlo batch. Seed 2026 reverses the missed-passenger
    # order between 20 and 200 paired worlds; increasing runs just to pass this
    # assertion would conceal that uncertainty.
    assert "不保证" in passenger.explanation
    for candidate in candidates.values():
        score = report_score(scenario, candidate, None)
        renamed = candidate.model_copy(deep=True)
        renamed.profile = PlanProfile.passenger
        renamed.title = "not a performance guarantee"
        assert report_score(scenario, renamed, None) == score
        worse = candidate.model_copy(deep=True)
        worse.report.missed_connections += 10
        # Hold every other outcome fixed: misses must increase loss regardless
        # of which profile generated the plan.
        assert report_score(scenario, worse, None) > score
        assert candidate.report.runs == 20
        assert candidate.report.method_version == "entity-world-v1"
        assert candidate.report.missed_connections == round(
            candidate.report.raw_metrics["missed_connections"])
    assert green.report.emissions_index <= balanced.report.emissions_index
    assert sum(item.taxi_minutes for item in green.assignments) <= sum(
        item.taxi_minutes for item in balanced.assignments
    )
