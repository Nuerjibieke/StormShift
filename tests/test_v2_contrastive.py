from stormshift_v2.contrastive import build_flight_contrastive_explanation
from stormshift_v2.optimizer import solve_candidate
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.schemas import PlanProfile


def test_contrastive_explanation_rejects_closed_runway_before_reopening():
    scenario = storm_scenario(seed=2026)
    plan, failure = solve_candidate(scenario, PlanProfile.balanced)
    assert failure is None and plan is not None
    first = min(plan.assignments, key=lambda item: item.landing_minute)

    result = build_flight_contrastive_explanation(
        scenario,
        plan,
        first.flight_id,
        simulation_runs=6,
    )

    assert result["method"] == (
        "forced-action CP-SAT re-optimization + paired-seed SimPy"
    )
    assert result["flight_id"] == first.flight_id
    assert len(result["witnesses"]) == 5
    assert all(
        witness["status"] == "pass"
        for witness in result["witnesses"]
        if witness["hard"]
    )
    alternate_runway = next(
        item for item in result["alternatives"]
        if item["id"] == "alternate_runway"
    )
    assert alternate_runway["to"] == "07R"
    assert alternate_runway["status"] == "infeasible"
    assert any(
        "T+24" in evidence and f"T+{first.landing_minute}" in evidence
        for evidence in alternate_runway["evidence"]
    )
    assert "不是 LLM 生成的理由" in result["safety_statement"]


def test_feasible_contrasts_are_resolved_and_revalidated():
    scenario = storm_scenario(seed=2034)
    plan, failure = solve_candidate(scenario, PlanProfile.green)
    assert failure is None and plan is not None
    last = max(plan.assignments, key=lambda item: item.landing_minute)

    result = build_flight_contrastive_explanation(
        scenario,
        plan,
        last.flight_id,
        simulation_runs=6,
    )

    assert len(result["alternatives"]) >= 2
    for alternative in result["alternatives"]:
        assert alternative["status"] in {"feasible", "infeasible"}
        assert alternative["evidence"]
        if alternative["status"] == "feasible":
            assert alternative["delta"] is not None
            assert alternative["changed_flights"] >= 1
