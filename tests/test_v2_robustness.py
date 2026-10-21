from stormshift_v2.optimizer import solve_candidate, validate_assignments
from stormshift_v2.robustness import (
    evaluate_adaptive_horizon,
    _gate_shock,
    evaluate_candidate_regret,
)
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.schemas import FlightAssignment, PlanProfile
from stormshift_v2.simulation import (
    _fcfs_baseline,
    counterfactual_summary,
    simulate_candidate,
)


def _candidate():
    scenario = storm_scenario(seed=2042)
    candidate, failure = solve_candidate(scenario, PlanProfile.balanced)
    assert failure is None and candidate is not None
    candidate.report = simulate_candidate(scenario, candidate, runs=12)
    return scenario, candidate


def test_counterfactual_uses_a_reproducible_fcfs_simulation():
    scenario, candidate = _candidate()
    rows = counterfactual_summary(scenario, candidate)
    baseline = _fcfs_baseline(scenario)
    baseline.report = simulate_candidate(scenario, baseline, runs=30)

    assert rows[0]["name"] == "无优化 · FCFS"
    assert rows[0]["source"] == "30-run-simpy-baseline"
    assert rows[0]["mean_delay"] == baseline.report.mean_delay_min
    assert rows[0]["p90_delay"] == baseline.report.p90_delay_min
    assert rows[-1]["source"] == "12-run-simpy-candidate"


def test_adaptive_horizon_detects_gate_shock_and_is_reproducible():
    scenario, candidate = _candidate()
    first = evaluate_adaptive_horizon(scenario, candidate, runs=8)
    second = evaluate_adaptive_horizon(scenario, candidate, runs=8)

    assert first == second
    assert first["total_worlds"] == 3
    assert first["direct_worlds"] + first["replan_worlds"] == 3
    gate_shock = next(row for row in first["worlds"] if row["id"] == "gate_shock")
    prepared = gate_shock["prepared_response"]
    assert prepared["status"] == "ready"
    assert prepared["hard_constraints_passed"] is True
    assert prepared["assignment_count"] == len(prepared["assignments"])
    assert prepared["plan_id"].endswith(":gate_shock:prepared")
    stressed, _, _ = _gate_shock(scenario, candidate)
    assignments = [
        FlightAssignment.model_validate(item)
        for item in prepared["assignments"]
    ]
    proof = validate_assignments(stressed, assignments)
    assert not any(item.startswith("FAIL") for item in proof)
    assert first["prepared_disturbance_worlds"] == 2
    assert first["disturbance_worlds"] == 2
    assert gate_shock["fixed_safe"] is False
    assert gate_shock["adaptive_available"] is True
    assert gate_shock["status"] == "replan"
    assert gate_shock["saved_p90"] >= 0


def test_minimax_regret_compares_all_candidates_in_the_same_worlds():
    scenario = storm_scenario(seed=2042)
    candidates = []
    for profile in (
        PlanProfile.balanced,
        PlanProfile.passenger,
        PlanProfile.capacity,
        PlanProfile.green,
    ):
        candidate, failure = solve_candidate(scenario, profile)
        assert failure is None and candidate is not None
        candidate.report = simulate_candidate(scenario, candidate, runs=6)
        candidates.append(candidate)

    first = evaluate_candidate_regret(scenario, candidates, runs=4)
    second = evaluate_candidate_regret(scenario, candidates, runs=4)

    assert first == second
    assert first["world_count"] == 3
    assert len(first["candidates"]) == len(candidates)
    assert first["recommended_id"] == first["candidates"][0]["id"]
    assert first["candidates"][0]["max_regret"] == min(
        row["max_regret"] for row in first["candidates"]
    )
    expected_worlds = {"expected", "weather_tail", "gate_shock"}
    for rank, row in enumerate(first["candidates"], start=1):
        assert row["robust_rank"] == rank
        assert {world["world_id"] for world in row["by_world"]} == expected_worlds
        assert 0 <= row["safe_worlds"] <= row["total_worlds"] == 3
