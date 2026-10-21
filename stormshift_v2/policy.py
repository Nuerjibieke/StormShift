"""Compile auditable event-condition-action policies around CP-SAT plans."""

from __future__ import annotations

import math
from typing import Any

from .schemas import EventSpec, PlanCandidate, ScenarioSpec, new_id


def compile_contingency_policy(
    scenario: ScenarioSpec,
    plan: PlanCandidate,
    robustness: dict[str, Any],
    regret: dict[str, Any],
) -> dict[str, Any]:
    """Build a small runtime-assurance shield for one approved schedule."""
    closed = next(
        (runway for runway in scenario.runways if runway.reopen_minute is not None),
        None,
    )
    weather_trigger = (
        int(closed.reopen_minute or scenario.current_minute)
        + max(4, math.ceil(scenario.weather.closure_std_min))
        if closed
        else scenario.current_minute + 20
    )
    flight_by_id = {flight.id: flight for flight in scenario.flights}
    critical_assignment = max(
        plan.assignments,
        key=lambda item: (
            flight_by_id.get(item.flight_id).connections
            if flight_by_id.get(item.flight_id)
            else 0
        ),
        default=None,
    )
    critical_gate = critical_assignment.gate if critical_assignment else None
    assigned_gates = sorted({item.gate for item in plan.assignments})
    min_fuel = min(
        (flight.fuel_margin_min for flight in scenario.flights),
        default=30,
    )
    robust_plan_id = regret.get("recommended_id")
    stress_worlds = {
        str(item.get("id")): item
        for item in robustness.get("worlds", [])
    }

    def prepared_response(world_id: str | None) -> dict[str, Any]:
        world = stress_worlds.get(str(world_id)) if world_id else None
        prepared = dict((world or {}).get("prepared_response", {}))
        if not prepared:
            return {
                "status": "solve_on_trigger",
                "world_id": world_id,
                "assignment_count": 0,
                "assignments": [],
                "hard_constraints_passed": False,
                "source": "no matching pre-solved stress world",
            }
        prepared["source"] = "pre-solved stress world; CP-SAT hint only"
        return prepared
    branches = [
        {
            "id": "storm_overrun",
            "label": "雷暴超时",
            "label_en": "Storm overrun",
            "trigger_types": ["storm", "runway_closed"],
            "resource_ids": [closed.id] if closed else [],
            "condition": (
                f"天气等级 ≥4，或 {closed.id if closed else '受限跑道'} "
                f"在 T+{weather_trigger} 后仍未开放"
            ),
            "condition_en": (
                f"Weather severity ≥4, or {closed.id if closed else 'the constrained runway'} "
                f"remains closed after T+{weather_trigger}"
            ),
            "after_minute": weather_trigger,
            "min_severity": 4,
            "action": "global_replan",
            "scope": "全部未落地航班",
            "scope_en": "All airborne flights",
            "solver": "CP-SAT rolling horizon",
            "reason": "容量假设改变，需要重建跑道与机位联合时序",
            "prepared_response": prepared_response("weather_tail"),
        },
        {
            "id": "critical_gate_shock",
            "label": "关键机位/地服失效",
            "label_en": "Critical gate or ground failure",
            "trigger_types": ["gate_outage", "ground_vehicle_failure"],
            "resource_ids": assigned_gates,
            "condition": (
                f"{critical_gate or '关键机位'} 或其服务链不可用"
            ),
            "condition_en": (
                f"{critical_gate or 'The critical gate'} or its service chain becomes unavailable"
            ),
            "after_minute": scenario.current_minute,
            "min_severity": 1,
            "action": "causal_local_repair",
            "scope": "仅因果依赖路径上的航班",
            "scope_en": "Flights on the causal dependency path only",
            "solver": "NetworkX scope + frozen-boundary CP-SAT",
            "reason": "先锁定未受影响航班，再开放最小变量集合",
            "prepared_response": prepared_response("gate_shock"),
        },
        {
            "id": "fuel_or_medical_guard",
            "label": "燃油/医疗优先闸门",
            "label_en": "Fuel or medical priority guard",
            "trigger_types": ["fuel_alert", "medical_diversion"],
            "resource_ids": [],
            "condition": f"出现燃油告警、医疗备降，或最小燃油余量接近 {min_fuel} 分钟",
            "condition_en": (
                f"A fuel alert or medical diversion occurs; current minimum fuel margin is {min_fuel} min"
            ),
            "after_minute": scenario.current_minute,
            "min_severity": 1,
            "action": "priority_replan",
            "scope": "优先航班及受其挤占的资源链",
            "scope_en": "Priority flight and displaced resource chain",
            "solver": "CP-SAT hard-priority replan",
            "reason": "燃油截止与医疗优先属于不可覆盖的硬约束",
            "prepared_response": prepared_response(None),
        },
    ]
    prepared_branches = sum(
        branch["prepared_response"]["status"] == "ready"
        for branch in branches
    )
    robust_title = next(
        (
            str(candidate.get("title"))
            for candidate in regret.get("candidates", [])
            if candidate.get("id") == robust_plan_id
        ),
        None,
    )
    return {
        "id": new_id("policy"),
        "method": "scenario-tree + event-condition-action + runtime-assurance",
        "source_plan_id": plan.id,
        "source_plan_title": plan.title,
        "robust_plan_id": robust_plan_id,
        "robust_plan_title": robust_title,
        "approved": False,
        "authorization": "approval-required",
        "default_action": "continue_and_monitor",
        "branch_count": len(branches),
        "branches": branches,
        "evidence": {
            "stress_worlds": robustness.get("total_worlds", 0),
            "prepared_branches": prepared_branches,
            "regret_method": regret.get("method", "not_available"),
            "hard_safety_gate": "mandatory",
        },
        "summary": (
            f"围绕{plan.title}生成 {len(branches)} 条可审计应急分支；"
            f"{prepared_branches} 条已有压力世界预案；命中后只作为 warm-start，"
            "当前动作仍由 CP-SAT 在硬约束下重新求解"
        ),
        "summary_en": (
            f"Compiled {len(branches)} auditable contingency branches around "
            f"{plan.title}; {prepared_branches} have pre-solved stress responses. "
            "A match uses them only as warm starts for a fresh, safety-constrained "
            "CP-SAT solve."
        ),
    }


def evaluate_policy_event(
    policy: dict[str, Any] | None,
    event: EventSpec,
) -> dict[str, Any]:
    """Evaluate one observed event against a compiled policy."""
    if not policy:
        return {
            "matched": False,
            "branch_id": None,
            "action": "manual_assessment",
            "scope": "未建立策略",
            "reason": "当前方案没有已编译的事件触发策略",
            "safety_gate": "required",
        }

    event_type = event.type.value
    event_resources = {event.resource_id} if event.resource_id else set()
    for key in ("affected_gates", "affected_runways"):
        event_resources.update(str(value) for value in event.payload.get(key, []))
    for branch in policy.get("branches", []):
        if event_type not in branch.get("trigger_types", []):
            continue
        resources = set(branch.get("resource_ids", []))
        if resources and not resources.intersection(event_resources):
            # A severe storm is airport-wide even when the event omits a runway id.
            if event_type != "storm":
                continue
        min_severity = int(branch.get("min_severity", 1))
        after_minute = int(branch.get("after_minute", 0))
        if (
            event_type == "storm"
            and event.severity < min_severity
            and event.minute < after_minute
        ):
            continue
        matched_resource = next(iter(resources.intersection(event_resources)), None)
        return {
            "matched": True,
            "policy_id": policy.get("id"),
            "branch_id": branch["id"],
            "branch_label": branch["label"],
            "action": branch["action"],
            "scope": branch["scope"],
            "solver": branch["solver"],
            "prepared_response": branch.get("prepared_response"),
            "matched_resource": matched_resource or event.resource_id,
            "reason": (
                f"事件 {event_type} 命中“{branch['label']}”：{branch['reason']}"
            ),
            "safety_gate": "mandatory",
            "approved_envelope": bool(policy.get("approved")),
        }
    return {
        "matched": False,
        "policy_id": policy.get("id"),
        "branch_id": None,
        "branch_label": "继续监控",
        "action": policy.get("default_action", "continue_and_monitor"),
        "scope": "原批准方案",
        "solver": "none",
        "matched_resource": event.resource_id,
        "reason": "事件未改变当前计划的受保护假设，继续执行并监控",
        "safety_gate": "mandatory",
        "approved_envelope": bool(policy.get("approved")),
    }

