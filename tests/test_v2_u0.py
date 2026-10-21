"""U0 regressions: stable worlds, fair baselines and evidence-bound acceptance."""
import json
import os
import subprocess

import pytest

from stormshift_v2.optimizer import solve_candidate
from stormshift_v2.outcome_audit import build_outcome_audit
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.schemas import OperatorIntent, PlanProfile, ScheduledConstraint
from stormshift_v2.shadow_twin import evaluate_shadow_switch
from stormshift_v2.simulation import _single_run, build_passenger_graph, simulate_candidate


@pytest.fixture
def airport():
    scenario = storm_scenario(seed=2081)
    plan, failure = solve_candidate(scenario, PlanProfile.balanced)
    assert plan is not None and failure is None
    return scenario, plan


@pytest.mark.parametrize("seed", [123, 456, 789])
def test_assignment_and_scenario_permutations_do_not_change_world(airport, seed):
    scenario, plan = airport
    expected = _single_run(scenario, plan, build_passenger_graph(scenario), seed)
    reordered = plan.model_copy(deep=True)
    reordered.assignments.reverse()
    reordered.id = "other-id"
    reordered.title = "Other title"
    shuffled = scenario.model_copy(deep=True)
    shuffled.flights.reverse()
    shuffled.runways.reverse()
    shuffled.gates.reverse()
    assert _single_run(shuffled, reordered, build_passenger_graph(shuffled), seed) == expected


def test_reordered_identical_shadow_has_zero_gain_and_interval(airport):
    scenario, plan = airport
    reordered = plan.model_copy(deep=True)
    reordered.assignments.reverse()
    reordered.id = "shadow"
    first = simulate_candidate(scenario, plan, runs=8)
    assert simulate_candidate(scenario, reordered, runs=8) == first
    shadow = evaluate_shadow_switch(scenario, plan, reordered, runs=8)
    assert shadow["paired_gain"]["mean_points"] == 0
    assert shadow["paired_gain"]["ci95_low"] == 0
    assert shadow["paired_gain"]["ci95_high"] == 0


def _scope_fixture(airport, connections=119, missed=6.0):
    scenario, plan = airport
    plan.assignments = plan.assignments[:1]
    active = plan.assignments[0].flight_id
    for flight in scenario.flights:
        flight.connections = connections if flight.id == active else 0
    plan.report = simulate_candidate(scenario, plan, runs=8)
    plan.report.missed_connections = round(missed)
    plan.report.raw_metrics["missed_connections"] = missed
    plan.report.raw_metrics["mean_delay_min"] = 22.004
    plan.report.mean_delay_min = 22.0
    plan.risk_shield = {"connection_passengers_exposed": 6}
    return scenario, plan


def test_unrounded_ratio_is_consistent_in_all_checks(airport):
    scenario, plan = _scope_fixture(airport)
    intent = OperatorIntent(raw_text="95%", minimum_connection_rate=.95,
                            needs_confirmation=False)
    audit = build_outcome_audit(scenario, plan, intent)
    check = next(row for row in audit["operator_checks"]
                 if row["id"] == "minimum_connection_rate")
    contract = next(row for row in audit["outcome_contract"]["rows"]
                    if row["id"] == "connection_rate")
    assert check["passed"] is False and contract["passed"] is False
    assert audit["passengers"]["predicted_on_time_rate"] == 113 / 119


def test_exact_threshold_and_decimal_targets(airport):
    scenario, plan = _scope_fixture(airport, connections=100, missed=5.0)
    intent = OperatorIntent(raw_text="95%; 22.002", minimum_connection_rate=.95,
                            average_delay_cap=22.002, needs_confirmation=False)
    audit = build_outcome_audit(scenario, plan, intent)
    checks = {row["id"]: row for row in audit["operator_checks"]}
    assert checks["minimum_connection_rate"]["passed"] is True
    assert checks["average_delay_cap"]["passed"] is False
    assert checks["average_delay_cap"]["limit"] == 22.002


def test_tail_threshold_uses_unrounded_ratio(airport):
    scenario, plan = _scope_fixture(airport)
    intent = OperatorIntent(raw_text="尾部5.04%", maximum_tail_exposure_rate=.0504,
                            needs_confirmation=False)
    audit = build_outcome_audit(scenario, plan, intent)
    checks = {row["id"]: row for row in audit["operator_checks"]}
    assert checks["maximum_tail_exposure_rate"]["passed"] is False


def test_unknown_and_missing_scheduled_evidence_never_pass(airport):
    scenario, plan = airport
    intent = OperatorIntent(raw_text="关闭机位", needs_confirmation=False,
        scheduled_constraints=[ScheduledConstraint(kind="close_resource",
            resource_id="NO-SUCH-GATE", minute=0)])
    check = build_outcome_audit(scenario, plan, intent)["operator_checks"][0]
    assert check["passed"] is not True
    assert check["status"] == "rejected"
    intent.scheduled_constraints[0].resource_id = "A4"
    check = build_outcome_audit(scenario, plan, intent)["operator_checks"][0]
    assert check["passed"] is not True and check["status"] == "unverified"


def test_future_requirement_is_pending_not_passed(airport):
    scenario, plan = airport
    intent = OperatorIntent(raw_text="十分钟后关闭A4", needs_confirmation=False,
        scheduled_constraints=[ScheduledConstraint(kind="close_resource",
            resource_id="A4", minute=10)])
    check = build_outcome_audit(scenario, plan, intent)["operator_checks"][0]
    assert check["status"] == "pending" and check["passed"] is None


def test_empty_scope_and_empty_proof_not_all_green(airport):
    scenario, plan = airport
    for flight in scenario.flights:
        flight.connections = 0
    plan.constraint_proof = []
    audit = build_outcome_audit(scenario, plan)
    assert audit["hard_constraints"]["all_passed"] is False
    assert audit["passengers"]["predicted_on_time_rate"] is None
    assert audit["status"] != "passed"


def test_legacy_reports_not_relabelled_as_current(airport):
    scenario, plan = airport
    payload = simulate_candidate(scenario, plan, runs=8).model_dump()
    payload.pop("method_version", None)
    payload.pop("raw_metrics", None)
    plan.report = type(plan.report).model_validate(payload)
    audit = build_outcome_audit(scenario, plan)
    assert plan.report.method_version == "legacy"
    assert audit["evidence"]["precision"] == "legacy_rounded"
    assert audit["automatic_execution_eligible"] is False


def test_recovered_metrics_are_still_forecasts(airport):
    scenario, plan = airport
    plan.report = simulate_candidate(scenario, plan, runs=8)
    audit = build_outcome_audit(scenario, plan, stage="recovered")
    assert audit["metric_kind"] == "forecast"
    assert "actual_on_time" not in audit["passengers"]


def test_world_reproducible_across_process_hash_seeds(airport):
    source = """
import json
from dataclasses import asdict
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.optimizer import solve_candidate
from stormshift_v2.schemas import PlanProfile
from stormshift_v2.simulation import _single_run, build_passenger_graph
s = storm_scenario(seed=2081)
p, _ = solve_candidate(s, PlanProfile.balanced)
print(json.dumps(asdict(_single_run(s, p, build_passenger_graph(s), 123)), sort_keys=True))
"""
    outputs = [subprocess.check_output(
        [os.sys.executable, "-c", source],
        env={**os.environ, "PYTHONHASHSEED": value}, text=True)
        for value in ["1", "734"]]
    assert json.loads(outputs[0]) == json.loads(outputs[1])
