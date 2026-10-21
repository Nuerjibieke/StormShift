from stormshift_v2.optimizer import solve_candidate, validate_assignments
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.schemas import PlanProfile, ResourceStatus
from stormshift_v2.shadow_twin import evaluate_shadow_switch


def _solve(scenario, profile):
    plan, failure = solve_candidate(scenario, profile)
    assert failure is None
    assert plan is not None
    return plan


def test_shadow_twin_keeps_identical_challenger_and_is_deterministic():
    scenario = storm_scenario(seed=2081)
    incumbent = _solve(scenario, PlanProfile.balanced)
    challenger = incumbent.model_copy(deep=True)
    challenger.id = "plan_identical_shadow"
    challenger.title = "同构影子方案"

    first = evaluate_shadow_switch(
        scenario, incumbent, challenger, runs=8
    )
    second = evaluate_shadow_switch(
        scenario, incumbent, challenger, runs=8
    )

    assert first == second
    assert first["decision"] == "keep"
    assert first["status"] == "keep_incumbent"
    assert first["paired_gain"]["mean_points"] == 0
    assert first["paired_gain"]["ci95_low"] == 0
    assert first["paired_gain"]["ci95_high"] == 0
    assert first["scope"]["common_flights"] == len(incumbent.assignments)


def test_shadow_twin_mandates_switch_when_incumbent_gate_becomes_unavailable():
    scenario = storm_scenario(seed=2082)
    incumbent = _solve(scenario, PlanProfile.balanced)
    failed_gate_id = incumbent.assignments[0].gate
    failed_gate = next(
        gate for gate in scenario.gates if gate.id == failed_gate_id
    )
    failed_gate.status = ResourceStatus.blocked
    failed_gate.available_minute = 120

    challenger = _solve(scenario, PlanProfile.passenger)
    challenger_proof = validate_assignments(
        scenario, challenger.assignments
    )
    assert not any(item.startswith("FAIL") for item in challenger_proof)

    result = evaluate_shadow_switch(
        scenario, incumbent, challenger, runs=8
    )

    assert result["decision"] == "switch"
    assert result["status"] == "mandatory_switch"
    assert result["hard_gate"]["incumbent_operationally_valid"] is False
    assert result["hard_gate"]["challenger_valid"] is True
    assert result["runs"] == 8

