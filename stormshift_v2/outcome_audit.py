"""Evidence-bound forecast acceptance. Display rounding never decides a pass."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .schemas import ExecutionEvent, OperatorIntent, PlanCandidate, ScenarioSpec

DEFAULT_CONNECTION_RATE_FLOOR = .90
DEFAULT_TAIL_EXPOSURE_RATE_CEILING = .40
DEFAULT_RESOURCE_QUEUE_CEILING = 2


def intent_digest(intent: OperatorIntent | None) -> str | None:
    if intent is None or intent.needs_confirmation:
        return None
    # Compiler/UI metadata is not an execution requirement. Excluding additive
    # fields also preserves digests for pre-compiler saved sessions.
    payload = intent.model_dump(mode="json", exclude={
        "summary", "source", "needs_confirmation", "base_minute", "specified_fields",
        "cleared_fields", "inherited_fields", "evidence", "issues", "notes",
        "original_text", "clarification_questions", "clarification_history",
    })
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def requirement_digest(intent: OperatorIntent, index: int) -> str:
    payload = [intent_digest(intent), index,
               intent.scheduled_constraints[index].model_dump(mode="json")]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _proof_rows(plan: PlanCandidate) -> list[dict[str, Any]]:
    return [{"label": item.removeprefix("PASS ").removeprefix("FAIL "),
             "passed": item.startswith("PASS"), "kind": "hard"}
            for item in plan.constraint_proof]


def _scheduled_check(scenario, plan, intent, index, events, audit_minute, chain_verified):
    item = intent.scheduled_constraints[index]
    due = item.minute if item.minute is not None else intent.confirmed_minute
    digest = intent_digest(intent)
    key = requirement_digest(intent, index)
    target = item.resource_id or str(item.value or "已指定对象")
    row = {"id": f"scheduled_constraint_{index + 1}",
           "label": f"{item.kind}：{target}" + (f" · T+{due}" if due is not None else ""),
           "kind": "operator_hard" if item.hard else "operator_preference",
           "intent_version": intent.version, "intent_digest": digest,
           "requirement_digest": key, "resource_id": item.resource_id,
           "due_minute": due, "passed": None, "status": "unverified",
           "evidence_sequences": [], "reason": "缺少绑定本条指令的执行事件"}
    for event in events:
        if event.type in {"intent_superseded", "intent_revoked"} and chain_verified:
            if event.payload.get("intent_digest") == digest:
                row.update(status="superseded", reason="已明确撤销或被新确认版本替代")
                return row
    known = {resource.id for resource in [*scenario.runways, *scenario.gates]}
    if item.kind == "priority":
        known = {flight.id for flight in scenario.flights}
    if item.kind != "delay_cap" and item.resource_id not in known:
        row.update(status="rejected", passed=False, reason="对象不存在于当前场景")
        return row
    if due is not None and due > audit_minute:
        row.update(status="pending", reason="尚未到要求执行时间")
        return row
    if plan.evidence_basis.get("operator_intent_digest") != digest:
        row["reason"] = "方案没有绑定当前确认的指令版本"
        return row
    allowed = {
        "close_resource": {"runway_closed", "gate_outage", "operator_constraint_executed"},
        "reopen_resource": {"runway_reopened", "gate_reopened", "operator_constraint_executed"},
        "priority": {"operator_constraint_executed"},
        "delay_cap": {"operator_constraint_executed"},
    }
    for event in events:
        payload = event.payload
        if (chain_verified and event.type in allowed[item.kind]
                and payload.get("intent_digest") == digest
                and payload.get("requirement_digest") == key
                and payload.get("resource_id") == item.resource_id
                and payload.get("constraint_kind") == item.kind
                and payload.get("executed") is True
                and due is not None and abs(event.sim_minute - due) < 1e-9):
            row.update(status="passed", passed=True, reason="版本、对象、时刻及执行事件一致",
                       evidence_sequences=[event.sequence])
            return row
    return row


def build_outcome_audit(
    scenario: ScenarioSpec, plan: PlanCandidate, intent: OperatorIntent | None = None,
    *, stage: str = "final", baseline: PlanCandidate | None = None,
    execution_events: list[ExecutionEvent] | None = None,
    event_chain_verified: bool = False, audit_minute: float | None = None,
) -> dict[str, Any]:
    """Forecasts remain forecasts even after a playback reaches recovered."""
    flights = {flight.id: flight for flight in scenario.flights}
    scoped_ids = sorted({item.flight_id for item in plan.assignments if item.flight_id in flights})
    total = sum(max(0, f.connections) for f in scenario.flights)
    scope = sum(max(0, flights[fid].connections) for fid in scoped_ids)
    raw = plan.report.raw_metrics
    required = {"mean_delay_min", "missed_connections", "remaining_conflicts"}
    precise = (plan.report.method_version != "legacy" and plan.report.runs > 0
               and required.issubset(raw))
    missed_display = min(scope, max(0, int(plan.report.missed_connections)))
    missed = min(float(scope), max(0.0, raw["missed_connections"])) if precise else None
    rate = (scope - missed) / scope if scope and missed is not None else None
    shield = plan.risk_shield or {}
    tail = min(scope, max(0, int(shield.get("connection_passengers_exposed", 0) or 0)))
    tail_known = "connection_passengers_exposed" in shield
    tail_rate = tail / scope if scope and tail_known else None
    fuel = (int(shield["fuel_escalations"] or 0) if "fuel_escalations" in shield else None)
    queues = raw["remaining_conflicts"] if precise else None
    delay = raw["mean_delay_min"] if precise else None
    proof = _proof_rows(plan)
    hard_passed = sum(row["passed"] for row in proof)
    hard_known = bool(proof)
    hard_ok = hard_known and all(row["passed"] for row in proof) and plan.feasible
    confirmed = intent is not None and not intent.needs_confirmation
    rows: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    def add(row_id, label, layer, actual, target, comparison, unit, source):
        passed = (None if actual is None else
                  actual <= target if comparison == "<=" else
                  actual >= target if comparison == ">=" else actual == target)
        # actual/target retain full precision; UI formats them independently.
        row = {"id": row_id, "label": label, "layer": layer, "actual": actual,
               "target": target, "comparison": comparison, "unit": unit,
               "source": source, "passed": passed,
               "status": "unverified" if passed is None else "passed" if passed else "failed"}
        rows.append(row)
        return row

    add("hard_safety", "航空运行硬安全规则", "safety",
        hard_passed if hard_known else None, len(proof), "=", f"/{len(proof)}", "system_safety")
    connection_floor = (intent.minimum_connection_rate if confirmed and
                        intent.minimum_connection_rate is not None else DEFAULT_CONNECTION_RATE_FLOOR)
    tail_ceiling = (intent.maximum_tail_exposure_rate if confirmed and
                    intent.maximum_tail_exposure_rate is not None else DEFAULT_TAIL_EXPOSURE_RATE_CEILING)
    connection = add("connection_rate", "预计按时转机率", "service",
                     rate * 100 if rate is not None else None, connection_floor * 100, ">=", "%",
                     "operator" if confirmed and intent.minimum_connection_rate is not None else "demo_default")
    # Compare ratios, not percent conversions (floating-point scaling can change boundary bits).
    connection["passed"] = rate >= connection_floor if rate is not None else None
    connection["status"] = "unverified" if rate is None else "passed" if connection["passed"] else "failed"
    tail_row = add("tail_exposure", "尾部观察名单占本轮比例", "risk_budget",
                   tail_rate * 100 if tail_rate is not None else None, tail_ceiling * 100, "<=", "%",
                   "operator" if confirmed and intent.maximum_tail_exposure_rate is not None else "demo_default")
    tail_row["passed"] = tail_rate <= tail_ceiling if tail_rate is not None else None
    tail_row["status"] = "unverified" if tail_rate is None else "passed" if tail_row["passed"] else "failed"
    add("fuel_guard", "燃油风险越界", "safety", fuel, 0, "=", " 架", "system_safety")
    add("resource_queues", "平均资源排队触发", "service", queues,
        DEFAULT_RESOURCE_QUEUE_CEILING, "<=", " 次", "demo_default")
    if confirmed:
        pairs = []
        if intent.average_delay_cap is not None:
            pairs.append(("average_delay_cap", add("mean_delay",
                f"仿真平均延误不超过 {intent.average_delay_cap:g} 分钟", "service",
                delay, intent.average_delay_cap, "<=", "min", "operator")))
        if intent.minimum_connection_rate is not None:
            pairs.append(("minimum_connection_rate", connection))
        if intent.maximum_missed_connections is not None:
            pairs.append(("maximum_missed_connections", add("missed_connections",
                "平均预计错失转机", "service", missed, intent.maximum_missed_connections,
                "<=", " pax", "operator")))
        if intent.maximum_tail_exposure_rate is not None:
            pairs.append(("maximum_tail_exposure_rate", tail_row))
        checks = [{"id": key, "label": row["label"], "actual": row["actual"],
                   "limit": row["target"], "comparison": row["comparison"], "unit": row["unit"],
                   "passed": row["passed"], "status": row["status"], "kind": "operator_target"}
                  for key, row in pairs]
        checks.extend(_scheduled_check(scenario, plan, intent, index, execution_events or [],
                      scenario.current_minute if audit_minute is None else audit_minute, event_chain_verified)
                      for index in range(len(intent.scheduled_constraints)))

    safety_failed = not plan.feasible or any(r["passed"] is False for r in rows if r["layer"] == "safety")
    unknown = any(r["passed"] is None for r in rows)
    target_failed = any(r["passed"] is False for r in rows if r["layer"] != "safety")
    has_watch = bool(missed_display or tail or plan.report.remaining_conflicts)
    contract_status = ("failed" if safety_failed else "unverified" if unknown else
                       "target_missed" if target_failed else "met_with_watch" if has_watch else "met")
    check_unknown = any(r["passed"] is None and r["status"] != "superseded" for r in checks)
    check_failed = any(r["passed"] is False for r in checks)
    blocks = bool(shield.get("blocks_unattended_execution", False))
    status = ("failed" if safety_failed else "human_required" if unknown or check_unknown or blocks
              else "passed_with_watch" if target_failed or check_failed or has_watch
              or shield.get("status") in {"watch", "escalate"} else "passed")
    digest = intent_digest(intent)
    binding_ok = digest is None or plan.evidence_basis.get("operator_intent_digest") == digest
    eligible = hard_ok and precise and scope > 0 and binding_ok and not (
        unknown or blocks or check_unknown or check_failed or target_failed)
    comparison = None
    if baseline is not None:
        compatible = (set(scoped_ids) == {a.flight_id for a in baseline.assignments}
                      and precise and baseline.report.method_version == plan.report.method_version
                      and required.issubset(baseline.report.raw_metrics))
        comparison = {
            "comparable": compatible, "baseline_plan_id": baseline.id, "final_plan_id": plan.id,
            "reason": "same scope and method" if compatible else "scope or simulation version/precision changed",
            "mean_delay_delta_min": (raw["mean_delay_min"] - baseline.report.raw_metrics["mean_delay_min"]
                                     if compatible else None),
            "p90_delay_delta_min": (plan.report.p90_delay_min - baseline.report.p90_delay_min
                                    if compatible else None),
            "missed_connections_delta": (missed - baseline.report.raw_metrics["missed_connections"]
                                         if compatible else None),
            "remaining_conflicts_delta": (queues - baseline.report.raw_metrics["remaining_conflicts"]
                                          if compatible else None),
            "scope_changed": {"baseline_flights": len(baseline.assignments), "final_flights": len(scoped_ids)},
        }
    return {
        "method": "evidence-bound scoped forecast audit v2", "stage": stage, "status": status,
        "metric_kind": "forecast", "plan_id": plan.id, "plan_title": plan.title,
        "simulation_method_version": plan.report.method_version,
        "intent_digest": digest, "intent_version": intent.version if confirmed else None,
        "audit_minute": scenario.current_minute if audit_minute is None else audit_minute,
        "automatic_execution_eligible": eligible,
        "evidence": {"precision": "unrounded" if precise else "legacy_rounded",
                     "passenger_scope": "available" if scope else "empty",
                     "hard_proof": "available" if hard_known else "missing",
                     "intent_binding": "current" if binding_ok else "missing_or_stale",
                     "event_chain_verified": event_chain_verified},
        "hard_constraints": {"passed": hard_passed, "total": len(proof),
                             "all_passed": hard_ok, "rows": proof},
        "operator_checks": checks,
        "outcome_contract": {
            "method": "probabilistic operations contract", "status": contract_status,
            "source": "operator_plus_defaults" if confirmed else "demo_default",
            "passed": sum(r["passed"] is True for r in rows), "total": len(rows), "rows": rows,
            "summary": ("硬安全失败，方案不可执行。" if safety_failed else
                        "证据或精确统计缺失，不能声称全部达标。" if unknown else
                        "硬安全通过，但部分服务或风险目标未达标。" if target_failed else
                        "声明目标达标；随机风险仍需监控。" if has_watch else "声明目标均达标。"),
            "semantics": "安全、服务目标和证据分开判定；使用未舍入值。尾部观察与平均错失重叠，禁止相加。"},
        "passengers": {
            "scenario_total": total, "decision_scope": scope, "out_of_scope": max(0, total - scope),
            "predicted_on_time": scope - missed_display, "predicted_missed": missed_display,
            "expected_on_time_unrounded": scope - missed if missed is not None else None,
            "expected_missed_unrounded": missed, "predicted_on_time_rate": rate,
            "tail_risk_exposed": tail, "tail_risk_exposure_rate": tail_rate},
        "operations": {
            "flights_in_scope": len(scoped_ids), "fuel_breaches": fuel,
            "remaining_queue_conflicts": plan.report.remaining_conflicts,
            "mean_queue_triggers_unrounded": queues, "mean_delay_min": plan.report.mean_delay_min,
            "p90_delay_min": plan.report.p90_delay_min, "recovery_minute": plan.report.recovery_minute,
            "risk_shield": str(shield.get("status", "unknown"))},
        "comparison": comparison,
        "scope_note": ("固定种子 Monte Carlo 预测，不是实际旅客到达记录；recovered 只表示演练执行结束。"
                       "人数展示舍入，验收使用未舍入均值；范围外旅客不计作按时。"),
    }
