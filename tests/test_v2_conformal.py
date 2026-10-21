from stormshift_v2.optimizer import solve_candidate
from stormshift_v2.schemas import PlanProfile
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.simulation import simulate_candidate


_RADIUS_KEYS = (
    "eldt_q90_radius",
    "eibt_q90_radius",
    "tobt_q90_radius",
)


def _average_radius(report) -> float:
    values = [
        metrics[key]
        for metrics in report.per_flight.values()
        for key in _RADIUS_KEYS
    ]
    return sum(values) / len(values)


def test_scenario_calibrated_conformal_envelopes_are_deterministic_and_weather_aware():
    mild = storm_scenario(seed=2060)
    mild.weather.severity = 1
    mild.weather.closure_std_min = 1
    plan, failure = solve_candidate(mild, PlanProfile.balanced)
    assert failure is None and plan is not None

    mild_report = simulate_candidate(mild, plan, runs=60)
    severe = mild.model_copy(deep=True)
    severe.weather.severity = 5
    severe.weather.closure_std_min = 10
    severe_report = simulate_candidate(severe, plan.model_copy(deep=True), runs=60)
    repeated = simulate_candidate(severe, plan.model_copy(deep=True), runs=60)

    assert severe_report.per_flight == repeated.per_flight
    assert _average_radius(severe_report) > _average_radius(mild_report)
    assert all(
        metrics["conformal_coverage"] == .9
        and metrics["calibration_samples"] == 30
        and metrics["evaluation_samples"] == 30
        and metrics["split_independent"] == 1.0
        and all(metrics[key] >= 0 for key in _RADIUS_KEYS)
        for metrics in severe_report.per_flight.values()
    )
    empirical = [
        metrics["eldt_empirical_coverage"]
        for metrics in severe_report.per_flight.values()
    ]
    assert sum(empirical) / len(empirical) >= .80
