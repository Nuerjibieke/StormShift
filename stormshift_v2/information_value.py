"""Decision-aware active evidence acquisition for airport recovery.

This module does not schedule aircraft. It estimates which uncertain observation
would most reduce the regret of the current CP-SAT plan, using the same shared
stress worlds already exposed in Decision Lab.
"""

from __future__ import annotations

from typing import Any

from .schemas import PlanCandidate, ScenarioSpec


_WORLD_PRIORS = {
    "expected": 0.60,
    "weather_tail": 0.25,
    "gate_shock": 0.15,
}


def _critical_gate(scenario: ScenarioSpec, plan: PlanCandidate) -> str | None:
    flights = {flight.id: flight for flight in scenario.flights}
    assignment = max(
        plan.assignments,
        key=lambda item: flights.get(item.flight_id).connections
        if flights.get(item.flight_id) else 0,
        default=None,
    )
    return assignment.gate if assignment else None


def recommend_observation(
    scenario: ScenarioSpec,
    plan: PlanCandidate,
    robustness: dict[str, Any],
    regret: dict[str, Any],
    resolved_query_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Rank sensor/data requests by a transparent one-step VoI proxy.

    The expected-regret term is expressed in the regret model's normalized loss
    units. A separate safety relevance bonus only ranks which observation should
    be requested first; it never changes the CP-SAT objective or safety proof.
    """
    resolved = set(resolved_query_ids or [])
    candidate_row = next(
        (
            row
            for row in regret.get("candidates", [])
            if row.get("id") == plan.id
        ),
        {},
    )
    regret_by_world = {
        row.get("world_id"): row
        for row in candidate_row.get("by_world", [])
    }
    regret_worlds = {
        row.get("id"): row
        for row in regret.get("worlds", [])
    }
    robustness_worlds = {
        row.get("id"): row
        for row in robustness.get("worlds", [])
    }
    closed_runway = next(
        (
            runway
            for runway in scenario.runways
            if runway.reopen_minute is not None
        ),
        None,
    )
    critical_gate = _critical_gate(scenario, plan)
    catalog = [
        {
            "id": "weather_reopen_nowcast",
            "world_id": "weather_tail",
            "label": "确认跑道开放时间",
            "label_en": "Confirm runway reopening time",
            "target": closed_runway.id if closed_runway else "weather",
            "source": "Weather Agent → nowcast / runway inspection",
            "reveals": "跑道开放时刻与湿跑道间隔",
            "reveals_en": "Runway reopening time and wet-runway separation",
            "action_label": "获取最新跑道观测并重算",
            "action_label_en": "Acquire runway observation and re-solve",
        },
        {
            "id": "critical_gate_readiness",
            "world_id": "gate_shock",
            "label": "确认关键机位服务链",
            "label_en": "Confirm critical gate readiness",
            "target": critical_gate or "critical gate",
            "source": "Gate & Ground Agent → AODB / ground-resource ping",
            "reveals": "机位、拖车与廊桥的可用窗口",
            "reveals_en": "Gate, tug and bridge availability window",
            "action_label": "获取地服状态并重算",
            "action_label_en": "Acquire ground status and re-solve",
        },
    ]

    queries: list[dict[str, Any]] = []
    for item in catalog:
        world_id = str(item["world_id"])
        world_regret = regret_by_world.get(world_id, {})
        regret_value = float(world_regret.get("regret", 0.0))
        prior = float(_WORLD_PRIORS[world_id])
        expected_reduction = prior * regret_value
        stress = robustness_worlds.get(world_id, {})
        status = str(stress.get("status", "unknown"))
        safety_relevant = status in {"replan", "manual"}
        watch_relevant = status == "watch"
        priority = (
            expected_reduction
            + (12.0 if safety_relevant else 4.0 if watch_relevant else 0.0)
            + prior
        )
        best_ids = set(regret_worlds.get(world_id, {}).get("best_plan_ids", []))
        decision_sensitive = bool(
            regret_value > 1e-6
            or (best_ids and plan.id not in best_ids)
            or safety_relevant
        )
        queries.append({
            **item,
            "prior": prior,
            "regret_if_unobserved": round(regret_value, 2),
            "expected_regret_reduction": round(expected_reduction, 2),
            "priority_score": round(priority, 2),
            "stress_status": status,
            "safety_relevant": safety_relevant,
            "decision_sensitive": decision_sensitive,
            "resolved": item["id"] in resolved,
        })

    unresolved = [item for item in queries if not item["resolved"]]
    unresolved.sort(key=lambda item: (
        -float(item["priority_score"]),
        -float(item["expected_regret_reduction"]),
        str(item["id"]),
    ))
    recommendation = unresolved[0] if unresolved and unresolved[0]["priority_score"] > 0 else None
    baseline_expected_regret = sum(
        float(_WORLD_PRIORS.get(str(row.get("world_id")), 0.0))
        * float(row.get("regret", 0.0))
        for row in candidate_row.get("by_world", [])
    )
    if recommendation is None:
        summary = "现有证据足以支持当前选择；暂不建议追加观测。"
        summary_en = "Current evidence is sufficient; no additional observation is recommended."
        status = "sufficient"
    else:
        if recommendation["safety_relevant"]:
            summary = (
                f"先{recommendation['label']}最有价值：它直接决定当前计划是否仍在"
                "受保护运行包络内；价值来自避免越界执行，而不只是改善软目标。"
            )
            summary_en = (
                f"The highest-value next observation is to {str(recommendation['label_en']).lower()}; "
                "it determines whether the plan remains inside its protected operating "
                "envelope, rather than merely improving a soft objective."
            )
        else:
            summary = (
                f"先{recommendation['label']}最有价值：预计可消除 "
                f"{recommendation['expected_regret_reduction']:.2f} 个加权后悔单位。"
            )
            summary_en = (
                f"The highest-value next observation is to {str(recommendation['label_en']).lower()}; "
                f"it can remove {recommendation['expected_regret_reduction']:.2f} "
                "weighted regret units."
            )
        status = "observe_then_optimize"

    return {
        "method": "myopic value-of-perfect-information proxy over shared stress worlds",
        "status": status,
        "source_plan_id": plan.id,
        "recommended_query_id": recommendation["id"] if recommendation else None,
        "baseline_expected_regret": round(baseline_expected_regret, 2),
        "assumed_world_priors": dict(_WORLD_PRIORS),
        "summary": summary,
        "summary_en": summary_en,
        "queries": queries,
        "resolved_query_ids": sorted(resolved),
        "caveat": (
            "这是用于演练排序的单步信息价值代理；情景先验会明确展示，"
            "当前观测来自可复现的实验传感器，不冒充现场 AODB/跑道检查接口；"
            "它只更新输入，所有航班动作仍必须由 CP-SAT 重新生成。"
        ),
    }

