"""Shift-aware safety envelope for solver-level adaptive robustification.

The module turns calibration-health evidence into explicit CP-SAT bounds. It
never emits aircraft actions and deliberately distinguishes statistical
coverage from an extra engineering reserve.
"""

from __future__ import annotations

import math
from typing import Any

from .risk_shield import conformal_landing_limits
from .schemas import PlanCandidate, ScenarioSpec

ADAPTIVE_COVERAGE = .95
MAX_ENGINEERING_RESERVE_MIN = 4


def build_adaptive_envelope(
    scenario: ScenarioSpec,
    calibration_plan: PlanCandidate,
    twin_trust: dict[str, Any],
) -> dict[str, Any]:
    """Compile a selective, auditable response to sequential undercoverage."""
    trust_status = str(twin_trust.get("status", "unavailable"))
    common = {
        "method": "anytime-valid shift monitor -> 95% conformal bound -> CP-SAT",
        "trigger_status": trust_status,
        "base_plan_id": calibration_plan.id,
        "base_coverage": .90,
        "adaptive_coverage": ADAPTIVE_COVERAGE,
        "action_source": "OR-Tools CP-SAT only",
        "live_telemetry": bool(twin_trust.get("live_telemetry", False)),
        "guarantee_scope": "current simulator scenario family only",
        "caveat": (
            "95% 包络来自独立校准样本；额外分钟数是保守工程余量，不宣称统计保证。"
            "现场部署需用真实 ELDT 残差在线更新。"
        ),
    }
    if trust_status == "drift":
        return {
            **common,
            "status": "abstain",
            "solver_allowed": False,
            "engineering_reserve_min": 0,
            "landing_limits": {},
            "summary": "全局漂移闸门已触发：不从失配模型生成自动动作，转人工并等待重新校准。",
        }
    if trust_status == "unavailable":
        return {
            **common,
            "status": "evidence_required",
            "solver_allowed": False,
            "engineering_reserve_min": 0,
            "landing_limits": {},
            "summary": "缺少独立留出证据：保持人工审批，不用未知误差收紧或放宽调度。",
        }
    if trust_status != "watch":
        return {
            **common,
            "status": "not_required",
            "solver_allowed": False,
            "engineering_reserve_min": 0,
            "landing_limits": {},
            "summary": "序贯覆盖证据稳定，当前 90% conformal 求解边界无需升级。",
        }

    watch_rows = [
        row
        for row in twin_trust.get("rows", [])
        if row.get("status") in {"watch", "drift"}
    ]
    local_e = max(
        [1.0, *[float(row.get("max_e_value", 1.0)) for row in watch_rows]]
    )
    observed = min(
        [
            float(row.get("empirical_coverage", ADAPTIVE_COVERAGE))
            for row in watch_rows
        ],
        default=float(
            twin_trust.get("overall_empirical_coverage", ADAPTIVE_COVERAGE)
            or ADAPTIVE_COVERAGE
        ),
    )
    target = float(twin_trust.get("target_coverage", .90))
    shortfall = max(0.0, target - observed)
    engineering_reserve = min(
        MAX_ENGINEERING_RESERVE_MIN,
        max(1, math.ceil(math.log2(local_e) / 2 + shortfall * 20)),
    )
    protected_milestones = sorted(
        {str(row.get("milestone")) for row in watch_rows if row.get("milestone")}
    )
    landing_coverage = (
        ADAPTIVE_COVERAGE if "ELDT" in protected_milestones else .90
    )
    raw_limits = conformal_landing_limits(
        scenario,
        calibration_plan,
        coverage=landing_coverage,
    )
    adjusted_limits = {
        flight_id: (
            minute - engineering_reserve
            if landing_coverage == ADAPTIVE_COVERAGE
            else minute
        )
        for flight_id, minute in raw_limits.items()
    }
    turnaround_buffers: dict[str, int] = {}
    for row in watch_rows:
        if row.get("milestone") not in {"EIBT", "TOBT"}:
            continue
        flight_id = str(row.get("flight_id", ""))
        metrics = calibration_plan.report.per_flight.get(flight_id, {})
        q95_radius = max(
            float(metrics.get("eibt_q95_radius", 0.0)),
            float(metrics.get("tobt_q95_radius", 0.0)),
        )
        turnaround_buffers[flight_id] = max(
            turnaround_buffers.get(flight_id, 0),
            math.ceil(q95_radius) + engineering_reserve,
        )
    strongest_row = max(
        watch_rows,
        key=lambda row: float(row.get("max_e_value", 0.0)),
        default={},
    )
    strongest = str(
        strongest_row.get("id")
        or twin_trust.get("strongest_stream")
        or "unknown"
    )
    controls: list[str] = []
    if landing_coverage == ADAPTIVE_COVERAGE:
        controls.append("ELDT 使用 95% 上界收紧燃油截止")
    if turnaround_buffers:
        controls.append(
            f"{len(turnaround_buffers)} 架航班加入 95% 机位占用缓冲"
        )
    return {
        **common,
        "status": "ready",
        "solver_allowed": True,
        "engineering_reserve_min": engineering_reserve,
        "landing_coverage": landing_coverage,
        "landing_limits": adjusted_limits,
        "turnaround_buffer_by_flight": turnaround_buffers,
        "protected_milestones": protected_milestones,
        "strongest_stream": strongest,
        "local_max_e_value": round(local_e, 4),
        "observed_coverage": round(observed, 4),
        "summary": (
            f"{strongest} 触发局部关注：{'；'.join(controls)}；"
            f"另加 {engineering_reserve} 分钟工程余量，并交回 CP-SAT 重新排序。"
        ),
    }
