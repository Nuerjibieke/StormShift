from stormshift_v2.optimizer import empty_report, solve_candidate
from stormshift_v2.risk_shield import assess_conformal_risk
from stormshift_v2.runway_assurance import (
    assess_runway_time_contracts,
    runway_p90_ready_minute,
)
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.schemas import FlightAssignment, PlanCandidate, PlanProfile
from stormshift_v2.simulation import generate_ranked_candidates, simulate_candidate


def _single_use(minute: int) -> PlanCandidate:
    return PlanCandidate(
        profile=PlanProfile.balanced,
        title="test",
        actions=[],
        report=empty_report(),
        score=0,
        assignments=[FlightAssignment(
            flight_id="OZ284",
            runway="07R",
            landing_minute=minute,
            gate="A3",
            hold_minutes=max(0, minute - 12),
            taxi_minutes=4,
            remote_stand=False,
        )],
    )


def test_runway_time_contract_separates_point_and_p90_clearance():
    scenario = storm_scenario()
    assert runway_p90_ready_minute(scenario, "07R") == 33

    conditional = assess_runway_time_contracts(scenario, _single_use(29))
    clear = assess_runway_time_contracts(scenario, _single_use(33))

    assert conditional["status"] == "conditional"
    assert conditional["conditional_actions"] == 1
    assert clear["status"] == "clear"


def test_conditional_runway_use_blocks_unattended_execution():
    scenario = storm_scenario()
    candidate = _single_use(29)
    candidate.report = simulate_candidate(scenario, candidate, runs=20)

    shield = assess_conformal_risk(scenario, candidate)

    assert shield["status"] == "escalate"
    assert shield["blocks_unattended_execution"] is True
    assert shield["runway_clearance"]["conditional_actions"] == 1


def test_resilient_cp_sat_never_uses_a_closed_runway_before_p90():
    scenario = storm_scenario()
    plans, failure = generate_ranked_candidates(
        scenario,
        [PlanProfile.balanced, PlanProfile.passenger, PlanProfile.resilient, PlanProfile.green],
        runs=20,
    )

    assert failure is None
    resilient = next(item for item in plans if item.profile == PlanProfile.resilient)
    p90 = runway_p90_ready_minute(scenario, "07R")
    assert p90 is not None
    assert all(
        item.runway != "07R" or item.landing_minute >= p90
        for item in resilient.assignments
    )
    assert resilient.risk_shield["runway_clearance"]["status"] == "clear"
    window = resilient.risk_shield["runway_clearance"]["windows"][0]
    assert window["runway"] == "07R"
    assert window["status"] in {"unused", "clear"}


def test_nominal_solver_still_respects_point_reopening():
    scenario = storm_scenario()
    candidate, failure = solve_candidate(scenario, PlanProfile.balanced)
    assert failure is None and candidate is not None
    assert all(
        item.runway != "07R" or item.landing_minute >= 24
        for item in candidate.assignments
    )


def test_invalid_contingency_hint_cannot_override_runway_safety():
    scenario = storm_scenario()
    unsafe_hint = FlightAssignment(
        flight_id="OZ284",
        runway="07R",
        landing_minute=10,
        gate="A3",
        hold_minutes=0,
        taxi_minutes=4,
        remote_stand=False,
    )

    candidate, failure = solve_candidate(
        scenario,
        PlanProfile.balanced,
        hint_assignments=[unsafe_hint],
    )

    assert failure is None and candidate is not None
    assert all(
        item.runway != "07R" or item.landing_minute >= 24
        for item in candidate.assignments
    )
    assert any("接受 0/1 架提示" in item for item in candidate.constraint_proof)
