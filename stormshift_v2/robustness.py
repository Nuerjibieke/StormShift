"""Adaptive stress horizon for auditable, recourse-aware plan evaluation."""

from __future__ import annotations

import math
from typing import Any, Callable

from .optimizer import solve_candidate, validate_assignments
from .schemas import (
    OperatorIntent,
    PlanCandidate,
    ResourceStatus,
    ScenarioSpec,
    SimulationReport,
)
from .simulation import simulate_candidate


def _weather_tail(scenario: ScenarioSpec, plan: PlanCandidate) -> tuple[ScenarioSpec, str, str]:
    stressed = scenario.model_copy(deep=True)
    closed = next(
        (runway for runway in stressed.runways if runway.reopen_minute is not None),
        stressed.runways[-1],
    )
    extra = max(6, math.ceil(stressed.weather.closure_std_min * 1.3))
    closed.status = ResourceStatus.closed
    closed.reopen_minute = max(
        stressed.current_minute + extra,
        int(closed.reopen_minute or stressed.current_minute) + extra,
    )
    closed.reason = "雷暴尾部情景"
    stressed.weather.severity = min(5, stressed.weather.severity + 1)
    stressed.weather.closure_mean_min = max(
        stressed.weather.closure_mean_min,
        float(closed.reopen_minute),
    )
    for runway in stressed.runways:
        runway.separation_min = min(10, runway.separation_min + 1)
    return (
        stressed,
        f"{closed.id} 晚 {extra} 分钟开放，湿跑道间隔增加 1 分钟",
        f"{closed.id} reopens {extra} minutes late; wet-runway separation adds 1 minute",
    )


def _gate_shock(scenario: ScenarioSpec, plan: PlanCandidate) -> tuple[ScenarioSpec, str, str]:
    stressed = scenario.model_copy(deep=True)
    flight_by_id = {flight.id: flight for flight in stressed.flights}
    target_assignment = max(
        plan.assignments,
        key=lambda assignment: flight_by_id.get(assignment.flight_id).connections
        if flight_by_id.get(assignment.flight_id) else 0,
    )
    gate = next(item for item in stressed.gates if item.id == target_assignment.gate)
    gate.status = ResourceStatus.blocked
    blocked_until = min(
        170,
        max(
            stressed.current_minute + 55,
            target_assignment.landing_minute + target_assignment.taxi_minutes + 35,
        ),
    )
    gate.available_minute = max(gate.available_minute, blocked_until)
    return (
        stressed,
        f"{gate.id} 在关键转机波次失效至 T+{blocked_until}",
        f"{gate.id} fails through T+{blocked_until} during the critical connection bank",
    )


def _report_row(report: SimulationReport) -> dict[str, float | int]:
    return {
        "mean_delay": report.mean_delay_min,
        "p90_delay": report.p90_delay_min,
        "cvar_delay": report.cvar_delay_min,
        "missed": report.missed_connections,
        "fuel_risk": report.fuel_risk_probability,
        "conflicts": report.remaining_conflicts,
        "emissions": report.emissions_index,
    }


def _normalized_loss(
    row: dict[str, Any],
    minima: dict[str, float],
    maxima: dict[str, float],
    weights: dict[str, float],
) -> float:
    """Return a 0-100 soft loss plus a hard-safety penalty."""
    metric_keys = {
        "delay": "cvar_delay",
        "connections": "missed",
        "fuel_risk": "fuel_risk",
        "conflicts": "conflicts",
        "emissions": "emissions",
    }
    loss = 0.0
    for objective, metric in metric_keys.items():
        span = maxima[metric] - minima[metric]
        normalized = (
            0.0
            if span <= 1e-9
            else (float(row[metric]) - minima[metric]) / span
        )
        loss += weights[objective] * normalized
    # Hard-constraint violations must never win through a good soft score.
    if not row["fixed_safe"]:
        loss += 1.0
    return round(loss * 100, 2)


def evaluate_candidate_regret(
    scenario: ScenarioSpec,
    candidates: list[PlanCandidate],
    intent: OperatorIntent | None = None,
    runs: int = 8,
) -> dict[str, Any]:
    """Rank CP-SAT candidates by minimax regret in shared stress worlds.

    Agents define auditable futures; they never generate executable flight actions.
    """
    if not candidates:
        return {
            "method": "shared-stress-worlds + minimax-regret",
            "runs_per_world": runs,
            "world_count": 0,
            "nominal_id": None,
            "recommended_id": None,
            "recommendation_changed": False,
            "summary": "没有候选方案可执行跨情景比较",
            "summary_en": "No candidates are available for cross-world comparison.",
            "worlds": [],
            "candidates": [],
        }

    reference = candidates[0]
    expected = scenario.model_copy(deep=True)
    weather, weather_change, weather_change_en = _weather_tail(scenario, reference)
    gate, gate_change, gate_change_en = _gate_shock(scenario, reference)
    world_specs = [
        (
            "expected",
            "预期运行",
            "Expected operations",
            "当前天气与资源预测",
            "Current weather and resource forecast",
            expected,
        ),
        (
            "weather_tail",
            "雷暴尾部",
            "Storm tail",
            weather_change,
            weather_change_en,
            weather,
        ),
        (
            "gate_shock",
            "关键机位失效",
            "Critical gate shock",
            gate_change,
            gate_change_en,
            gate,
        ),
    ]
    if intent is None:
        weights = {
            "delay": .30,
            "connections": .30,
            "fuel_risk": .20,
            "conflicts": .10,
            "emissions": .10,
        }
    else:
        weights = intent.weights.normalized().model_dump()

    rows_by_candidate: dict[str, list[dict[str, Any]]] = {
        item.id: [] for item in candidates
    }
    worlds: list[dict[str, Any]] = []
    for world_id, label, label_en, change, change_en, stressed in world_specs:
        world_rows: list[dict[str, Any]] = []
        for candidate in candidates:
            proof = validate_assignments(stressed, candidate.assignments, intent)
            fixed_safe = not any(item.startswith("FAIL") for item in proof)
            report = simulate_candidate(stressed, candidate, runs=runs)
            world_rows.append({
                "candidate_id": candidate.id,
                "fixed_safe": fixed_safe,
                **_report_row(report),
            })

        metric_keys = (
            "cvar_delay",
            "missed",
            "fuel_risk",
            "conflicts",
            "emissions",
        )
        minima = {
            key: min(float(row[key]) for row in world_rows)
            for key in metric_keys
        }
        maxima = {
            key: max(float(row[key]) for row in world_rows)
            for key in metric_keys
        }
        for row in world_rows:
            row["loss"] = _normalized_loss(row, minima, maxima, weights)
        best_loss = min(float(row["loss"]) for row in world_rows)
        best_ids = [
            row["candidate_id"]
            for row in world_rows
            if float(row["loss"]) == best_loss
        ]
        for row in world_rows:
            result = {
                "world_id": world_id,
                "world_label": label,
                "fixed_safe": row["fixed_safe"],
                "loss": row["loss"],
                "regret": round(float(row["loss"]) - best_loss, 2),
                "metrics": {
                    key: row[key]
                    for key in (
                        "p90_delay",
                        "cvar_delay",
                        "missed",
                        "fuel_risk",
                        "conflicts",
                        "emissions",
                    )
                },
            }
            rows_by_candidate[str(row["candidate_id"])].append(result)
        worlds.append({
            "id": world_id,
            "label": label,
            "label_en": label_en,
            "change": change,
            "change_en": change_en,
            "best_plan_ids": best_ids,
        })

    candidate_rows: list[dict[str, Any]] = []
    for candidate in candidates:
        per_world = rows_by_candidate[candidate.id]
        worst = max(
            per_world,
            key=lambda row: (float(row["regret"]), str(row["world_id"])),
        )
        candidate_rows.append({
            "id": candidate.id,
            "title": candidate.title,
            "profile": candidate.profile.value,
            "max_regret": float(worst["regret"]),
            "mean_regret": round(
                sum(float(row["regret"]) for row in per_world) / len(per_world),
                2,
            ),
            "worst_world_id": worst["world_id"],
            "worst_world_label": worst["world_label"],
            "safe_worlds": sum(bool(row["fixed_safe"]) for row in per_world),
            "total_worlds": len(per_world),
            "by_world": per_world,
        })
    candidate_rows.sort(key=lambda row: (
        float(row["max_regret"]),
        float(row["mean_regret"]),
        0 if row["id"] == reference.id else 1,
        str(row["id"]),
    ))
    for rank, row in enumerate(candidate_rows, start=1):
        row["robust_rank"] = rank

    robust = candidate_rows[0]
    changed = robust["id"] != reference.id
    if changed:
        summary = (
            f"当前预测推荐{reference.title}；跨 3 个共同未来世界后，"
            f"{robust['title']}的最坏后悔值最低（{robust['max_regret']:.1f}）"
        )
        summary_en = (
            f"The nominal recommendation is {reference.title}; across three "
            f"shared futures, {robust['title']} has the lowest worst-case "
            f"regret ({robust['max_regret']:.1f})."
        )
    else:
        summary = (
            f"{reference.title}同时是当前预测膝点与跨情景稳健选择；"
            f"最坏后悔值 {robust['max_regret']:.1f}"
        )
        summary_en = (
            f"{reference.title} is both the nominal knee and robust choice; "
            f"worst-case regret is {robust['max_regret']:.1f}."
        )
    return {
        "method": "shared-stress-worlds + normalized-minimax-regret",
        "runs_per_world": runs,
        "world_count": len(worlds),
        "nominal_id": reference.id,
        "recommended_id": robust["id"],
        "recommendation_changed": changed,
        "summary": summary,
        "summary_en": summary_en,
        "worlds": worlds,
        "candidates": candidate_rows,
    }


def evaluate_adaptive_horizon(
    scenario: ScenarioSpec,
    plan: PlanCandidate,
    intent: OperatorIntent | None = None,
    runs: int = 16,
) -> dict[str, Any]:
    """Compare a fixed approved plan with safe CP-SAT recourse in plausible futures."""
    if not plan.assignments:
        metrics = _report_row(plan.report)
        return {
            "method": "scenario-stress + cp-sat-adaptive-recourse",
            "runs_per_stress_world": runs,
            "stable_worlds": 1,
            "watch_worlds": 0,
            "direct_worlds": 1,
            "replan_worlds": 0,
            "total_worlds": 1,
            "adaptive_value_min": 0.0,
            "adaptive_p90_delta": 0.0,
            "summary": "当前没有待恢复航班，计划在预期世界中稳定",
            "summary_en": "No flights require recovery; the plan is stable in the expected world.",
            "worlds": [{
                "id": "expected",
                "label": "预期运行",
                "label_en": "Expected operations",
                "change": "当前没有待恢复航班",
                "change_en": "No flights require recovery",
                "status": "stable",
                "fixed_safe": True,
                "adaptive_available": True,
                "fixed": metrics,
                "adaptive": metrics,
                "saved_p90": 0.0,
                "p90_delta": 0.0,
                "saved_missed": 0,
            }],
        }

    builders: list[
        tuple[str, str, str, Callable[[ScenarioSpec, PlanCandidate], tuple[ScenarioSpec, str, str]] | None]
    ] = [
        ("expected", "预期运行", "Expected operations", None),
        ("weather_tail", "雷暴尾部", "Storm tail", _weather_tail),
        ("gate_shock", "关键机位失效", "Critical gate shock", _gate_shock),
    ]
    worlds: list[dict[str, Any]] = []
    for world_id, label, label_en, builder in builders:
        if builder is None:
            stressed = scenario.model_copy(deep=True)
            change = "当前天气与资源预测"
            change_en = "Current weather and resource forecast"
            fixed_report = plan.report
            adaptive_report = plan.report
            fixed_safe = True
            adaptive_available = True
            adaptive = plan
        else:
            stressed, change, change_en = builder(scenario, plan)
            proof = validate_assignments(stressed, plan.assignments, intent)
            fixed_safe = not any(item.startswith("FAIL") for item in proof)
            fixed_report = simulate_candidate(stressed, plan, runs=runs)
            adaptive, _ = solve_candidate(stressed, plan.profile, intent)
            adaptive_available = adaptive is not None
            if adaptive is not None:
                adaptive_report = simulate_candidate(stressed, adaptive, runs=runs)
            else:
                adaptive_report = fixed_report

        adaptive_proof = (
            validate_assignments(stressed, adaptive.assignments, intent)
            if adaptive is not None
            else ["FAIL 压力世界没有可行应对方案"]
        )
        prepared_ready = (
            adaptive is not None
            and not any(item.startswith("FAIL") for item in adaptive_proof)
        )
        prepared_response = {
            "status": "ready" if prepared_ready else "unavailable",
            "world_id": world_id,
            "plan_id": f"{plan.id}:{world_id}:prepared" if adaptive is not None else None,
            "plan_title": adaptive.title if adaptive is not None else None,
            "profile": adaptive.profile.value if adaptive is not None else None,
            "assignment_count": len(adaptive.assignments) if adaptive is not None else 0,
            "assignments": (
                [item.model_dump(mode="json") for item in adaptive.assignments]
                if adaptive is not None
                else []
            ),
            "expected_p90_delay_min": adaptive_report.p90_delay_min,
            "expected_missed_connections": adaptive_report.missed_connections,
            "hard_constraints_passed": prepared_ready,
            "proof": adaptive_proof,
        }
        p90_delta = round(adaptive_report.p90_delay_min - fixed_report.p90_delay_min, 2)
        saved_p90 = round(max(0.0, -p90_delta), 2)
        saved_missed = max(0, fixed_report.missed_connections - adaptive_report.missed_connections)
        if not adaptive_available:
            status = "manual"
        elif not fixed_safe:
            status = "replan"
        elif (
            fixed_report.fuel_risk_probability > .08
            or fixed_report.p90_delay_min > plan.report.p90_delay_min * 1.25
        ):
            status = "watch"
        else:
            status = "stable"
        worlds.append({
            "id": world_id,
            "label": label,
            "label_en": label_en,
            "change": change,
            "change_en": change_en,
            "status": status,
            "fixed_safe": fixed_safe,
            "adaptive_available": adaptive_available,
            "fixed": _report_row(fixed_report),
            "adaptive": _report_row(adaptive_report),
            "saved_p90": saved_p90,
            "p90_delta": p90_delta,
            "saved_missed": saved_missed,
            "prepared_response": prepared_response,
        })

    stable_worlds = sum(row["status"] == "stable" for row in worlds)
    watch_worlds = sum(row["status"] == "watch" for row in worlds)
    direct_worlds = stable_worlds + watch_worlds
    replan_worlds = sum(row["status"] in {"replan", "manual"} for row in worlds)
    prepared_disturbance_worlds = sum(
        row["id"] != "expected" and row["prepared_response"]["status"] == "ready"
        for row in worlds
    )
    adaptive_value = round(
        sum(float(row["saved_p90"]) for row in worlds) / max(1, len(worlds)),
        2,
    )
    adaptive_p90_delta = round(
        sum(float(row["p90_delta"]) for row in worlds) / max(1, len(worlds)),
        2,
    )
    if adaptive_p90_delta <= 0:
        performance_zh = f"平均降低 P90 延误 {-adaptive_p90_delta:.1f} 分钟"
        performance_en = f"reducing P90 delay by {-adaptive_p90_delta:.1f} minutes on average"
    else:
        performance_zh = f"安全重规划的平均 P90 时间成本为 +{adaptive_p90_delta:.1f} 分钟"
        performance_en = f"with an average P90 safety cost of +{adaptive_p90_delta:.1f} minutes"
    return {
        "method": "scenario-stress + cp-sat-adaptive-recourse",
        "runs_per_stress_world": runs,
        "stable_worlds": stable_worlds,
        "watch_worlds": watch_worlds,
        "direct_worlds": direct_worlds,
        "replan_worlds": replan_worlds,
        "total_worlds": len(worlds),
        "prepared_disturbance_worlds": prepared_disturbance_worlds,
        "disturbance_worlds": max(0, len(worlds) - 1),
        "adaptive_value_min": adaptive_value,
        "adaptive_p90_delta": adaptive_p90_delta,
        "summary": (
            f"原方案在 {direct_worlds}/{len(worlds)} 个未来世界可继续执行"
            f"（{watch_worlds} 个需重点监控）；{replan_worlds} 个世界触发安全重规划，"
            f"{prepared_disturbance_worlds}/{max(0, len(worlds) - 1)} 个扰动分支已预求解；"
            f"{performance_zh}"
        ),
        "summary_en": (
            f"The fixed plan remains executable in {direct_worlds}/{len(worlds)} futures "
            f"({watch_worlds} under watch); {replan_worlds} trigger safe replanning, "
            f"{prepared_disturbance_worlds}/{max(0, len(worlds) - 1)} disruption "
            "branches are pre-solved, "
            f"{performance_en}."
        ),
        "worlds": worlds,
    }

