from stormshift_v2.contract_repair import analyze_contract_repair
from stormshift_v2.outcome_audit import build_outcome_audit
from stormshift_v2.optimizer import solve_candidate
from stormshift_v2.schemas import OperatorIntent, PlanProfile
from stormshift_v2.scenarios import storm_scenario


def _candidate():
    scenario = storm_scenario()
    plan, failure = solve_candidate(scenario, PlanProfile.balanced)
    assert failure is None and plan is not None
    plan.report.mean_delay_min = 24
    plan.report.missed_connections = 20
    plan.report.remaining_conflicts = 3
    # Explicit synthetic exact expectations, not rounded display-only evidence.
    plan.report.method_version = "entity-world-v1"
    plan.report.runs = 50
    plan.report.raw_metrics = {"mean_delay_min": 24.0, "missed_connections": 20.0,
                               "remaining_conflicts": 3.0}
    plan.risk_shield = {
        "status": "watch",
        "fuel_escalations": 0,
        "connection_passengers_exposed": 100,
        "blocks_unattended_execution": False,
        "rows": [],
    }
    return scenario, plan


def _intent():
    return OperatorIntent(
        raw_text="转机成功率至少98%，平均错失不超过5人，尾部风险不超过10%，平均延误不超过22分钟",
        average_delay_cap=22,
        minimum_connection_rate=.98,
        maximum_missed_connections=5,
        maximum_tail_exposure_rate=.10,
        needs_confirmation=False,
    )


def test_contract_repair_only_relaxes_service_and_risk_targets():
    scenario, plan = _candidate()

    result = analyze_contract_repair(scenario, plan, [plan], _intent())

    assert result["status"] == "minimal_amendment"
    assert result["hard_safety_relaxations"] == 0
    assert {
        "mean_delay",
        "connection_rate",
        "missed_connections",
        "tail_exposure",
        "resource_queues",
    } == set(result["unmet_target_ids"])
    assert all(item["layer"] != "safety" for item in result["repairs"])
    contract_rows = build_outcome_audit(
        scenario, plan, _intent()
    )["outcome_contract"]["rows"]
    assert all(item["to_target"] == next(
        row["actual"]
        for row in contract_rows
        if row["id"] == item["target_id"]
    ) for item in result["repairs"])
    assert "不会改善真实运行表现" in result["tradeoff"]


def test_contract_repair_prefers_a_safer_existing_candidate_before_relaxation():
    scenario, selected = _candidate()
    alternative = selected.model_copy(deep=True)
    alternative.id = "plan_contract_better"
    alternative.title = "目标保障候选"
    alternative.report.mean_delay_min = 20
    alternative.report.missed_connections = 2
    alternative.report.remaining_conflicts = 1
    alternative.report.raw_metrics = {"mean_delay_min": 20.0, "missed_connections": 2.0,
                                      "remaining_conflicts": 1.0}
    alternative.risk_shield["connection_passengers_exposed"] = 20

    result = analyze_contract_repair(
        scenario,
        selected,
        [selected, alternative],
        _intent(),
    )

    assert result["status"] == "switch_plan"
    assert result["recommended_plan_id"] == alternative.id
    assert result["alternatives"][0]["failed_targets"] < next(
        item["failed_targets"]
        for item in result["alternatives"]
        if item["plan_id"] == selected.id
    )


def test_contract_repair_never_turns_hard_failure_into_a_counteroffer():
    scenario, plan = _candidate()
    plan.constraint_proof = ["FAIL 使用关闭跑道"]

    result = analyze_contract_repair(scenario, plan, [plan], _intent())

    assert result["status"] == "safety_block"
    assert result["hard_safety_relaxations"] == 0
    assert "禁止目标协商" in result["recommendation"]
    assert all(item["layer"] != "safety" for item in result["repairs"])

def test_missing_precision_never_becomes_contract_met_or_counteroffer():
    scenario, plan = _candidate()
    plan.report.raw_metrics = {}
    result = analyze_contract_repair(scenario, plan, [plan], _intent())
    assert result["status"] == "evidence_required"
    assert result["repairs"] == [] and result["suggested_command"] == ""
    assert "mean_delay" in result["unknown_target_ids"]
    plan.risk_shield["connection_passengers_exposed"] = 0
    result = analyze_contract_repair(scenario, plan, [plan])
    assert result["status"] == "evidence_required"


def test_unknown_alternative_cannot_win_by_having_no_measured_failures():
    scenario, selected = _candidate()
    unknown = selected.model_copy(deep=True)
    unknown.id = "unknown"
    unknown.report.raw_metrics = {}
    unknown.risk_shield["connection_passengers_exposed"] = 0
    unknown.score = -9999
    result = analyze_contract_repair(scenario, selected, [selected, unknown], _intent())
    assert result["status"] == "minimal_amendment"
    assert result["recommended_plan_id"] == selected.id
    alternative = next(x for x in result["alternatives"] if x["plan_id"] == unknown.id)
    assert not alternative["frontier"] and not alternative["evidence_complete"]


def test_changed_passenger_scope_is_not_a_better_alternative():
    scenario, selected = _candidate()
    different = selected.model_copy(deep=True)
    different.id = "smaller-scope"
    different.assignments = different.assignments[:1]
    different.report.raw_metrics = {"mean_delay_min": 0.0, "missed_connections": 0.0,
                                    "remaining_conflicts": 0.0}
    different.risk_shield["connection_passengers_exposed"] = 0
    result = analyze_contract_repair(scenario, selected, [selected, different], _intent())
    assert result["recommended_plan_id"] == selected.id
    row = next(x for x in result["alternatives"] if x["plan_id"] == different.id)
    assert not row["comparable_scope"] and not row["frontier"]

