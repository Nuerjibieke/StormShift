"""Time-indexed runway readiness contracts shared by planning and assurance.

The nominal inspection ETA is a scenario input. A resilient plan uses a
one-sided uncertainty buffer as an additional planning constraint; the
assurance layer exposes the difference instead of calling both values simply
"open".
"""

from __future__ import annotations

import math
from statistics import NormalDist

from .schemas import PlanCandidate, PlanProfile, ResourceStatus, ScenarioSpec

RUNWAY_READY_COVERAGE = 0.90
_Z90 = NormalDist().inv_cdf(RUNWAY_READY_COVERAGE)


def runway_point_ready_minute(scenario: ScenarioSpec, runway_id: str) -> int | None:
    runway = next((item for item in scenario.runways if item.id == runway_id), None)
    if runway is None:
        return None
    if runway.status == ResourceStatus.open:
        return int(scenario.current_minute)
    return int(runway.reopen_minute) if runway.reopen_minute is not None else None


def runway_p90_ready_minute(scenario: ScenarioSpec, runway_id: str) -> int | None:
    """Return a transparent Gaussian P90 readiness envelope for a closed runway."""
    runway = next((item for item in scenario.runways if item.id == runway_id), None)
    if runway is None:
        return None
    point = runway_point_ready_minute(scenario, runway_id)
    if point is None or runway.status == ResourceStatus.open:
        return point
    uncertainty = max(0.0, float(scenario.weather.closure_std_min))
    return max(point, int(math.ceil(point + _Z90 * uncertainty)))


def runway_planning_ready_minute(
    scenario: ScenarioSpec,
    runway_id: str,
    profile: PlanProfile,
) -> int | None:
    """Resilient plans reserve the runway only after its P90 readiness time."""
    if profile == PlanProfile.resilient:
        return runway_p90_ready_minute(scenario, runway_id)
    return runway_point_ready_minute(scenario, runway_id)


def assess_runway_time_contracts(
    scenario: ScenarioSpec,
    plan: PlanCandidate,
) -> dict[str, object]:
    """Classify planned runway uses as clear, conditional, or hard-blocked."""
    runway_by_id = {item.id: item for item in scenario.runways}
    rows: list[dict[str, object]] = []
    conditional = 0
    blocked = 0
    for assignment in plan.assignments:
        runway = runway_by_id.get(assignment.runway)
        if runway is None or runway.status == ResourceStatus.open:
            continue
        point = runway_point_ready_minute(scenario, runway.id)
        p90 = runway_p90_ready_minute(scenario, runway.id)
        if point is None or assignment.landing_minute < point:
            status = "blocked"
            blocked += 1
        elif p90 is not None and assignment.landing_minute < p90:
            status = "conditional"
            conditional += 1
        else:
            status = "clear"
        rows.append({
            "flight_id": assignment.flight_id,
            "runway": runway.id,
            "landing_minute": assignment.landing_minute,
            "point_ready_minute": point,
            "p90_ready_minute": p90,
            "status": status,
            "condition": (
                "conditional timing risk; approval gate determines evidence requirements; "
                "runtime runway availability validation still applies"
                if status == "conditional"
                else "hard runway closure violation"
                if status == "blocked"
                else "inside the P90 readiness envelope"
            ),
        })

    windows: list[dict[str, object]] = []
    for runway in scenario.runways:
        if runway.status == ResourceStatus.open:
            continue
        point = runway_point_ready_minute(scenario, runway.id)
        p90 = runway_p90_ready_minute(scenario, runway.id)
        uses = sorted(
            item.landing_minute
            for item in plan.assignments
            if item.runway == runway.id
        )
        first_use = uses[0] if uses else None
        window_status = (
            "unused"
            if first_use is None
            else "blocked"
            if point is None or first_use < point
            else "conditional"
            if p90 is not None and first_use < p90
            else "clear"
        )
        windows.append({
            "runway": runway.id,
            "point_ready_minute": point,
            "p90_ready_minute": p90,
            "first_planned_use_minute": first_use,
            "status": window_status,
        })

    if blocked:
        status = "blocked"
        summary = f"{blocked} 个动作早于跑道点预测开放时刻，必须停止并重新求解。"
    elif conditional:
        status = "conditional"
        summary = (
            f"{conditional} 个动作不早于点预测开放、但早于 P90 就绪；"
            "仍有未按预计开放的风险。是否需补证据由当前审批闸门判定；"
            "人工审批不替代执行时的跑道可用性复核（演练场景）。"
        )
    else:
        status = "clear"
        summary = "所有关闭跑道上的计划动作均不早于 P90 就绪时刻。"
    return {
        "method": "time-indexed point/P90 runway readiness contract",
        "coverage": RUNWAY_READY_COVERAGE,
        "status": status,
        "conditional_actions": conditional,
        "blocked_actions": blocked,
        "rows": rows,
        "windows": windows,
        "summary": summary,
        "meaning": (
            "红色表示当前时刻关闭；未来动作只有在其动作时刻达到对应许可线后才可释放。"
        ),
    }
