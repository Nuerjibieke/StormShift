import pytest

from stormshift_v2.operational_baseline import compare_operational_baselines
from stormshift_v2.optimizer import validate_assignments
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.schemas import DataMode, PlanProfile
from stormshift_v2.simulation import fcfs_baseline, generate_ranked_candidates
from stormshift_v2.trusted_state import build_trusted_operational_state


@pytest.fixture(scope="module")
def baseline_audit():
    scenario = storm_scenario()
    original = scenario.model_dump(mode="json")
    plans, failure = generate_ranked_candidates(
        scenario,
        [
            PlanProfile.balanced,
            PlanProfile.passenger,
            PlanProfile.resilient,
            PlanProfile.green,
        ],
        runs=8,
    )
    assert failure is None and plans
    result = compare_operational_baselines(
        scenario,
        build_trusted_operational_state(scenario, DataMode.synthetic),
        plans[0],
        runs=6,
    )
    return scenario, original, result


def test_rule_baseline_is_not_a_nominal_strawman():
    scenario = storm_scenario()
    baseline = fcfs_baseline(scenario)

    failures = [
        item
        for item in validate_assignments(scenario, baseline.assignments)
        if item.startswith("FAIL")
    ]

    assert not failures
    assert len(baseline.assignments) == 8
    assert all(item.gate != "A5" for item in baseline.assignments)


def test_shared_optimizer_fault_audit_is_safe_and_falsifiable(baseline_audit):
    _, _, result = baseline_audit
    rows = {item["id"]: item for item in result["rows"]}
    worlds = result["scope"]["fault_worlds"]

    assert set(rows) == {"rules", "solo_cp_sat", "updated_cp_sat", "stormshift"}
    assert all(item["nominal"]["hard_constraints_passed"] for item in rows.values())
    assert rows["rules"]["fault_trials"]["unsafe_execution_worlds"] >= 1
    assert rows["solo_cp_sat"]["fault_trials"]["unsafe_execution_worlds"] == worlds
    assert rows["solo_cp_sat"]["fault_trials"]["metric_eligible_worlds"] == 0
    assert rows["stormshift"]["fault_trials"]["unsafe_execution_worlds"] == 0
    assert rows["stormshift"]["fault_trials"]["hard_passed_worlds"] == worlds
    assert rows["stormshift"]["fault_trials"]["safe_decision_rate"] == 1.0
    assert result["nominal_solver_gap_min"] <= 1.0

    for world in result["worlds"]:
        plain = world["outcomes"]["updated_cp_sat"]
        agent = world["outcomes"]["stormshift"]
        assert plain["input_digest"] == agent["input_digest"]
        assert plain["solver_settings"] == agent["solver_settings"]
        assert plain["status"] == agent["status"] == "safe"
        assert plain["action_signature"] == agent["action_signature"]

def test_audit_is_scoped_and_does_not_mutate_the_scenario(baseline_audit):
    scenario, original, result = baseline_audit

    assert scenario.model_dump(mode="json") == original
    assert result["scope"]["commercial_products_compared"] == 0
    assert "不是" in result["disclaimer"]
    assert {item["id"] for item in result["worlds"]} == {
        "late_gate_readiness",
        "runway_inspection_hold",
        "fuel_clock_correction",
    }
    for world in result["worlds"]:
        assert world["outcomes"]["solo_cp_sat"]["status"] == "unsafe_against_truth"
        assert world["outcomes"]["stormshift"]["status"] == "safe"
