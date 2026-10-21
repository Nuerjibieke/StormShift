from __future__ import annotations

import pytest

from stormshift_v2.adaptive_envelope import build_adaptive_envelope
from stormshift_v2.risk_shield import conformal_landing_limits
from stormshift_v2.scenarios import build_scenario
from stormshift_v2.schemas import PlanProfile, SessionCreate
from stormshift_v2.simulation import generate_ranked_candidates


@pytest.fixture(scope="module")
def generated():
    scenario = build_scenario(
        SessionCreate(scenario_id="vhhh_storm", seed=2032)
    )
    plans, failure = generate_ranked_candidates(
        scenario,
        [
            PlanProfile.balanced,
            PlanProfile.passenger,
            PlanProfile.resilient,
            PlanProfile.green,
        ],
    )
    assert failure is None
    return scenario, plans


def test_q95_conformal_deadlines_are_never_looser_than_q90(generated):
    scenario, plans = generated
    calibration = plans[0]
    q90 = conformal_landing_limits(scenario, calibration, coverage=.90)
    q95 = conformal_landing_limits(scenario, calibration, coverage=.95)

    assert q90.keys() == q95.keys()
    assert all(q95[flight_id] <= q90[flight_id] for flight_id in q90)


def test_watch_state_generates_a_milestone_aware_cp_sat_candidate(monkeypatch):
    # Force the monitor's WATCH input; a random calibration fixture can instead
    # correctly trigger global drift/abstention and must not be tuned to pass.
    scenario = build_scenario(SessionCreate(
        scenario_id="sandbox", sandbox_flights=4, seed=2032))
    scenario.weather.severity = 1
    for flight in scenario.flights:
        flight.fuel_margin_min = 120
    monitor = {
        "status": "watch", "live_telemetry": False, "target_coverage": .90,
        "overall_empirical_coverage": .85,
        "rows": [{"status": "watch", "flight_id": scenario.flights[0].id,
                  "milestone": "EIBT", "max_e_value": 2.0, "empirical_coverage": .85}],
    }
    monkeypatch.setattr("stormshift_v2.simulation.assess_twin_trust",
                        lambda plan: monitor)
    plans, failure = generate_ranked_candidates(
        scenario, [PlanProfile.balanced, PlanProfile.resilient], runs=50)
    assert failure is None and plans
    assert plans[0].risk_shield["adaptive_envelope"]["trigger_status"] == "watch"
    audit = plans[0].risk_shield["adaptive_envelope"]

    assert audit["status"] in {"applied", "applied_equivalent"}
    assert audit["adaptive_coverage"] == .95
    assert audit["engineering_reserve_min"] >= 1
    assert {"ELDT", "EIBT", "TOBT"} & set(audit["protected_milestones"])
    assert audit["action_source"] == "OR-Tools CP-SAT only"

    adaptive = next(
        plan
        for plan in plans
        if plan.risk_shield["adaptive_envelope"]["applies_to_candidate"]
    )
    limits = audit["landing_limits"]
    assert all(
        assignment.landing_minute <= limits[assignment.flight_id]
        for assignment in adaptive.assignments
    )
    assert any(
        "里程碑" in proof and "CP-SAT" in proof
        for proof in adaptive.constraint_proof
    )


def test_global_drift_selectively_abstains_instead_of_making_actions(generated):
    scenario, plans = generated
    result = build_adaptive_envelope(
        scenario,
        plans[0],
        {"status": "drift", "live_telemetry": False},
    )

    assert result["status"] == "abstain"
    assert result["solver_allowed"] is False
    assert result["landing_limits"] == {}
