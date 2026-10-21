"""Conformal uncertainty guard around solver-generated airport recovery plans.

The shield never generates aircraft actions and is not a certified safety system.
It converts simulation-calibrated one-sided timing envelopes into explicit
operator evidence and an unattended-execution gate.
"""

from __future__ import annotations

import math

from .passenger import connection_buffer_minutes
from .runway_assurance import assess_runway_time_contracts
from .schemas import PlanCandidate, ScenarioSpec

FUEL_OPERATIONAL_RESERVE_MIN = 5
WATCH_SLACK_MIN = 5.0


def conformal_landing_limits(
    scenario: ScenarioSpec,
    calibration_plan: PlanCandidate,
    *,
    coverage: float = .90,
) -> dict[str, int]:
    """Return latest CP-SAT slots whose calibrated upper ELDT stays inside reserve."""
    if coverage not in {.90, .95}:
        raise ValueError("supported conformal coverage levels are 0.90 and 0.95")
    radius_key = "eldt_q95_radius" if coverage == .95 else "eldt_q90_radius"
    flights = {flight.id: flight for flight in scenario.flights}
    limits: dict[str, int] = {}
    for assignment in calibration_plan.assignments:
        flight = flights[assignment.flight_id]
        metrics = calibration_plan.report.per_flight.get(flight.id, {})
        radius = float(
            metrics.get(radius_key, metrics.get("eldt_q90_radius", 0.0))
        )
        reserve_deadline = (
            scenario.current_minute
            + flight.fuel_margin_min
            - FUEL_OPERATIONAL_RESERVE_MIN
        )
        limits[flight.id] = math.floor(reserve_deadline - radius)
    return limits


def assess_conformal_risk(
    scenario: ScenarioSpec,
    plan: PlanCandidate,
) -> dict[str, object]:
    """Assess held-out timing-tail exposure without changing the hard-safety proof."""
    flights = {flight.id: flight for flight in scenario.flights}
    gates = {gate.id: gate for gate in scenario.gates}
    metric_rows = list(plan.report.per_flight.values())
    calibration_samples = min(
        (int(float(item.get("calibration_samples", 0))) for item in metric_rows),
        default=0,
    )
    evaluation_samples = min(
        (int(float(item.get("evaluation_samples", 0))) for item in metric_rows),
        default=0,
    )
    coverage_values = [
        float(item[key])
        for item in metric_rows
        for key in (
            "eldt_empirical_coverage",
            "eibt_empirical_coverage",
            "tobt_empirical_coverage",
        )
        if key in item
    ]
    empirical_coverage = (
        sum(coverage_values) / len(coverage_values)
        if coverage_values
        else (1.0 if not plan.assignments else 0.0)
    )
    split_independent = bool(
        metric_rows
        and all(bool(item.get("split_independent", 0.0)) for item in metric_rows)
    )
    if not plan.assignments:
        calibration_health = "not_applicable"
    elif (
        split_independent
        and calibration_samples >= 10
        and evaluation_samples >= 10
        and empirical_coverage >= .75
    ):
        calibration_health = "healthy"
    else:
        calibration_health = "degraded"
    calibration_blocks = calibration_health == "degraded"

    runway_clearance = assess_runway_time_contracts(scenario, plan)
    runway_blocks = bool(
        runway_clearance["conditional_actions"]
        or runway_clearance["blocked_actions"]
    )
    rows: list[dict[str, object]] = []
    fuel_escalations = 0
    watch_count = 0
    connection_exposure = 0

    for assignment in plan.assignments:
        flight = flights[assignment.flight_id]
        metrics = plan.report.per_flight.get(flight.id, {})
        radius = float(metrics.get("eldt_q90_radius", 0.0))
        gate_radius = float(metrics.get("eibt_q90_radius", 0.0))
        upper_eldt = float(assignment.landing_minute) + radius
        reserve_deadline = float(
            scenario.current_minute
            + flight.fuel_margin_min
            - FUEL_OPERATIONAL_RESERVE_MIN
        )
        fuel_slack = reserve_deadline - upper_eldt
        fuel_probability = float(metrics.get("fuel_risk_probability", 0.0))
        if fuel_slack < 0 or fuel_probability >= .20:
            fuel_level = "escalate"
            fuel_escalations += 1
        elif fuel_slack < WATCH_SLACK_MIN or fuel_probability >= .05:
            fuel_level = "watch"
            watch_count += 1
        else:
            fuel_level = "clear"

        planned_gate = gates.get(flight.planned_gate, gates.get(assignment.gate))
        baseline_gate_in = (
            flight.scheduled_arrival
            + (planned_gate.taxi_minutes if planned_gate is not None else assignment.taxi_minutes)
        )
        upper_eibt = (
            float(assignment.landing_minute)
            + assignment.taxi_minutes
            + gate_radius
        )
        upper_gate_delay = max(0.0, upper_eibt - baseline_gate_in)
        transfer_buffer = float(connection_buffer_minutes(scenario.seed, flight.id))
        transfer_exposed = bool(
            flight.connections > 0 and upper_gate_delay > transfer_buffer
        )
        if transfer_exposed:
            connection_exposure += flight.connections
            if fuel_level == "clear":
                watch_count += 1

        rows.append({
            "flight_id": flight.id,
            "fuel_level": fuel_level,
            "eldt_point": assignment.landing_minute,
            "eldt_upper_90": round(upper_eldt, 1),
            "fuel_reserve_deadline": round(reserve_deadline, 1),
            "fuel_slack_min": round(fuel_slack, 1),
            "fuel_risk_probability": round(fuel_probability, 4),
            "transfer_passengers": flight.connections,
            "transfer_buffer_min": round(transfer_buffer, 1),
            "upper_gate_delay_min": round(upper_gate_delay, 1),
            "transfer_exposed": transfer_exposed,
        })

    if runway_blocks:
        status = "escalate"
        automation_mode = "human_required"
        summary = str(runway_clearance["summary"])
        if fuel_escalations:
            summary += f" 同时有 {fuel_escalations} 架航班越过燃油运行余量。"
        elif calibration_blocks:
            summary += " 同时时间包络校准退化。"
    elif fuel_escalations or calibration_blocks:
        status = "escalate"
        automation_mode = "human_required"
        if calibration_blocks and not fuel_escalations:
            summary = (
                "留出世界中的时间包络覆盖率不足或样本过少；"
                "风险模型选择性拒答，禁止无人值守执行。"
            )
        else:
            suffix = (
                "；同时检测到校准退化"
                if calibration_blocks
                else ""
            )
            summary = (
                f"{fuel_escalations} 架航班的 90% ELDT 上界越过燃油运行余量{suffix}；"
                "禁止无人值守执行，需人工确认、提前排序或备降。"
            )
    elif watch_count or connection_exposure:
        status = "watch"
        automation_mode = "guarded"
        summary = (
            f"燃油包络可执行；{connection_exposure} 名转机旅客处于尾部风险，"
            "执行期间保持事件触发监控。"
        )
    else:
        status = "clear"
        automation_mode = "guarded_autonomy"
        summary = "90% 时间包络未越过燃油运行余量或转机缓冲，可在已批准策略内受控执行。"

    return {
        "method": "held-out one-sided split-conformal timing guard",
        "status": status,
        "automation_mode": automation_mode,
        "blocks_unattended_execution": bool(
            fuel_escalations or calibration_blocks or runway_blocks
        ),
        "target_coverage": .90,
        "calibration_samples": calibration_samples,
        "evaluation_samples": evaluation_samples,
        "empirical_coverage": round(empirical_coverage, 4),
        "calibration_health": calibration_health,
        "split_independent": split_independent,
        "fuel_operational_reserve_min": FUEL_OPERATIONAL_RESERVE_MIN,
        "fuel_escalations": fuel_escalations,
        "watch_items": watch_count,
        "connection_passengers_exposed": connection_exposure,
        "runway_clearance": runway_clearance,
        "summary": summary,
        "rows": rows,
        "guarantee_scope": "current simulator scenario family only",
        "caveat": (
            "该 Shield 是运行风险证据，不是适航或 ATC 安全认证；"
            "真实部署必须用现场残差持续校准并由授权人员决策。"
        ),
    }

