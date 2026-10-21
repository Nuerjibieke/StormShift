"""A-CDM-compatible milestone ledger with explicit solver data lineage.

This module does not claim to implement an operational A-CDM platform. It turns
the exercise's arrival and turnaround decisions into a compact, auditable
contract: what entered CP-SAT, what it produced, what was derived, and what is
intentionally outside the model.
"""

from __future__ import annotations

from .optimizer import TURNAROUND_MINUTES
from .schemas import PlanCandidate, ScenarioSpec


def _conformal_interval(
    point: int,
    radius: float | None,
    coverage: float,
    samples: int,
) -> dict[str, object] | None:
    if radius is None:
        return None
    return {
        "lower_minute": float(point),
        "upper_minute": round(point + radius, 1),
        "radius_min": round(radius, 2),
        "coverage": round(coverage, 2),
        "calibration_samples": samples,
        "method": "one-sided scenario-calibrated split conformal",
    }


def _milestone(
    code: str,
    label: str,
    label_en: str,
    minute: int | None,
    status: str,
    owner: str,
    source: str,
    role: str,
    confidence: float,
    note: str,
    uncertainty: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "code": code,
        "label": label,
        "label_en": label_en,
        "minute": minute,
        "status": status,
        "owner": owner,
        "source": source,
        "role": role,
        "confidence": round(confidence, 2),
        "note": note,
        "uncertainty": uncertainty,
    }


def build_acdm_ledger(
    scenario: ScenarioSpec,
    plan: PlanCandidate,
    *,
    committed: bool = False,
) -> dict[str, object]:
    """Build a per-flight milestone ledger for one solver candidate."""
    flights = {flight.id: flight for flight in scenario.flights}
    severity = scenario.weather.severity
    prediction_confidence = max(0.48, 0.94 - severity * 0.08)
    records: list[dict[str, object]] = []
    calibration_radii: list[float] = []
    metric_rows = list(plan.report.per_flight.values())
    calibration_samples = min(
        (int(float(item.get("calibration_samples", 0))) for item in metric_rows),
        default=0,
    )
    evaluation_samples = min(
        (int(float(item.get("evaluation_samples", 0))) for item in metric_rows),
        default=0,
    )
    empirical_values = [
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
        sum(empirical_values) / len(empirical_values)
        if empirical_values
        else 0.0
    )
    calibration_health = (
        "healthy"
        if calibration_samples >= 10
        and evaluation_samples >= 10
        and empirical_coverage >= .75
        else "degraded"
    )

    for assignment in sorted(plan.assignments, key=lambda item: item.landing_minute):
        flight = flights[assignment.flight_id]
        eibt = assignment.landing_minute + assignment.taxi_minutes
        provisional_tobt = eibt + TURNAROUND_MINUTES
        plan_status = "committed_target" if committed else "predicted"
        metrics = plan.report.per_flight.get(flight.id, {})
        coverage = float(metrics.get("conformal_coverage", .90))
        samples = int(float(metrics.get("calibration_samples", plan.report.runs)))
        eldt_interval = _conformal_interval(
            assignment.landing_minute, metrics.get("eldt_q90_radius"), coverage, samples
        )
        eibt_interval = _conformal_interval(
            eibt, metrics.get("eibt_q90_radius"), coverage, samples
        )
        tobt_interval = _conformal_interval(
            provisional_tobt, metrics.get("tobt_q90_radius"), coverage, samples
        )
        calibration_radii.extend(
            float(interval["radius_min"])
            for interval in (eldt_interval, eibt_interval, tobt_interval)
            if interval is not None
        )
        records.append({
            "flight_id": flight.id,
            "aircraft_type": flight.aircraft_type,
            "current_state": flight.state.value,
            "planned_gate": flight.planned_gate,
            "assigned_gate": assignment.gate,
            "assigned_runway": assignment.runway,
            "gate_changed": assignment.gate != flight.planned_gate,
            "delay_min": max(0, assignment.landing_minute - flight.scheduled_arrival),
            "turnaround_assumption_min": TURNAROUND_MINUTES,
            "milestones": [
                _milestone(
                    "STA", "计划到达", "Scheduled arrival", flight.scheduled_arrival,
                    "published", "Airline Schedule", "Scenario / imported schedule",
                    "solver_input", 1.0,
                    "CP-SAT 的最早落地时间基线；合成模式下来自固定种子场景。",
                ),
                _milestone(
                    "ELDT", "预计落地", "Estimated landing", assignment.landing_minute,
                    plan_status, "CP-SAT Decision Engine", "CP-SAT selected plan",
                    "solver_output", prediction_confidence,
                    "满足跑道关闭、间隔、燃油截止和优先级后的安全落地时隙。",
                    uncertainty=eldt_interval,
                ),
                _milestone(
                    "EIBT", "预计进位", "Estimated in-block", eibt,
                    plan_status, "Airport Operations", "ELDT + taxi estimate",
                    "derived", prediction_confidence - 0.04,
                    "由 CP-SAT 落地时隙和滑行时间推导，并非独立 LLM 预测。",
                    uncertainty=eibt_interval,
                ),
                _milestone(
                    "TOBT", "目标撤轮挡", "Target off-block", provisional_tobt,
                    "exercise_target", "Airline / Ground Handler",
                    f"EIBT + {TURNAROUND_MINUTES} min exercise turnaround",
                    "derived", prediction_confidence - 0.12,
                    "演练中的临时周转目标；未接入航司真实 TOBT 更新链路。",
                    uncertainty=tobt_interval,
                ),
                _milestone(
                    "TSAT", "目标启动许可", "Target start-up approval", None,
                    "out_of_scope", "ATC / Departure Sequencer", "PDS not connected",
                    "external_missing", 0.0,
                    "当前模型不包含离港排序，因此不生成或伪造 TSAT。",
                ),
            ],
        })

    return {
        "method": "A-CDM-compatible milestone and data-lineage ledger",
        "scope": "arrival recovery + provisional turnaround",
        "plan_id": plan.id,
        "plan_title": plan.title,
        "status": "committed" if committed else "candidate",
        "generated_minute": scenario.current_minute,
        "flight_count": len(records),
        "full_acdm": False,
        "departure_sequencer_connected": False,
        "uncertainty_calibration": {
            "method": "held-out one-sided scenario-calibrated split conformal",
            "target_coverage": .90,
            "calibration_samples": calibration_samples,
            "evaluation_samples": evaluation_samples,
            "empirical_coverage": round(empirical_coverage, 4),
            "calibration_health": calibration_health,
            "average_radius_min": round(
                sum(calibration_radii) / len(calibration_radii), 2
            ) if calibration_radii else 0.0,
            "source": "Disjoint SimPy calibration and holdout residual banks",
            "guarantee_scope": "in-simulator exchangeability only",
            "upgrade_path": "observed A-CDM residuals + Adaptive Conformal Inference",
        },
        "data_quality": {
            "weather_source": scenario.weather.source,
            "lineage_coverage": 1.0,
            "authoritative_milestones": 1,
            "solver_outputs": 1,
            "derived_milestones": 2,
            "missing_external_milestones": 1,
        },
        "solver_contract": {
            "reads": [
                "STA / scheduled-arrival baseline",
                "runway closure and separation",
                "gate availability and taxi time",
                "fuel deadline and medical priority",
            ],
            "writes": ["runway", "ELDT / landing slot", "gate", "hold time"],
            "derives": ["EIBT", "provisional TOBT"],
            "never_writes": ["TSAT", "weather observation", "hard safety policy"],
        },
        "caveat": (
            "演练账本，不是机场运行级 A-CDM。TOBT 为演练周转假设；"
            "90% 时间包络仅对当前模拟场景族校准；"
            "TSAT 需接入离港排序/放行系统后才可生成。"
        ),
        "records": records,
    }


def build_acdm_ledgers(
    scenario: ScenarioSpec,
    plans: list[PlanCandidate],
    *,
    committed_plan_id: str | None = None,
) -> dict[str, dict[str, object]]:
    """Build candidate-specific ledgers so UI comparison never mixes plans."""
    return {
        plan.id: build_acdm_ledger(
            scenario,
            plan,
            committed=plan.id == committed_plan_id,
        )
        for plan in plans
    }
