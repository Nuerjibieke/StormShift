"""Anytime-valid calibration monitor for the airport digital twin.

The monitor consumes only disjoint SimPy holdout residuals today. It never
presents those residuals as live airport telemetry. Each flight/milestone
coverage stream is converted into a Bernoulli e-process; a family-wise
correction turns the strongest stream into an anytime-valid system alarm.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .schemas import PlanCandidate

TARGET_COVERAGE = 0.90
ALTERNATIVE_COVERAGE = 0.75
LOCAL_WATCH_E_VALUE = 5.0
FAMILYWISE_STOP_E_VALUE = 20.0

MILESTONES = (
    ("eldt", "落地时刻", "ELDT"),
    ("eibt", "进位时刻", "EIBT"),
    ("tobt", "推出准备", "TOBT"),
)


def bernoulli_e_process(
    covered: Iterable[bool],
    *,
    target: float = TARGET_COVERAGE,
    alternative: float = ALTERNATIVE_COVERAGE,
) -> dict[str, float | int]:
    """Return a fixed-bet e-process for detecting conformal undercoverage.

    Under the null hypothesis 'P(covered) >= target' and an alternative
    below the target, this likelihood-ratio process is a non-negative
    supermartingale. Ville's inequality therefore supports optional stopping.
    """
    if not 0 < alternative < target < 1:
        raise ValueError(
            "coverage probabilities must satisfy 0 < alternative < target < 1"
        )
    values = [bool(value) for value in covered]
    e_value = 1.0
    max_e_value = 1.0
    successes = 0
    for success in values:
        if success:
            successes += 1
            e_value *= alternative / target
        else:
            e_value *= (1 - alternative) / (1 - target)
        max_e_value = max(max_e_value, e_value)
    samples = len(values)
    failures = samples - successes
    return {
        "samples": samples,
        "covered": successes,
        "exceedances": failures,
        "empirical_coverage": round(successes / samples, 4) if samples else 1.0,
        "e_value": round(e_value, 6),
        "max_e_value": round(max_e_value, 6),
    }


def assess_twin_trust(plan: PlanCandidate) -> dict[str, Any]:
    """Audit whether calibrated timing envelopes remain fit for automation."""
    streams: list[dict[str, Any]] = []
    for flight_id, metrics in plan.report.per_flight.items():
        samples = int(float(metrics.get("evaluation_samples", 0)))
        for key, label, code in MILESTONES:
            max_key = f"{key}_e_process_max"
            current_key = f"{key}_e_process"
            if samples <= 0 or max_key not in metrics or current_key not in metrics:
                continue
            empirical = float(metrics.get(f"{key}_empirical_coverage", 0.0))
            exceedances = int(float(metrics.get(f"{key}_exceedances", 0.0)))
            streams.append({
                "id": f"{flight_id}:{code}",
                "flight_id": flight_id,
                "milestone": code,
                "label": label,
                "samples": samples,
                "covered": max(0, samples - exceedances),
                "exceedances": max(0, exceedances),
                "empirical_coverage": round(empirical, 4),
                "e_value": round(float(metrics[current_key]), 4),
                "max_e_value": round(float(metrics[max_key]), 4),
            })

    if not streams:
        return {
            "method": "anytime-valid Bernoulli e-process with family-wise correction",
            "status": "unavailable",
            "automation_mode": "human_required",
            "blocks_unattended_execution": True,
            "target_coverage": TARGET_COVERAGE,
            "alternative_coverage": ALTERNATIVE_COVERAGE,
            "familywise_streams": 0,
            "familywise_e_value": 0.0,
            "local_max_e_value": 0.0,
            "local_watch_threshold": LOCAL_WATCH_E_VALUE,
            "stop_threshold": FAMILYWISE_STOP_E_VALUE,
            "overall_empirical_coverage": None,
            "samples_per_stream": 0,
            "strongest_stream": None,
            "rows": [],
            "summary": "没有独立留出残差，数字孪生可信度无法校验；无人值守执行需人工确认。",
            "source": "no holdout residual stream",
            "live_telemetry": False,
            "caveat": "缺少证据不等于模型安全；接入现场 A-CDM 后应改用真实里程碑残差持续更新。",
        }

    streams.sort(key=lambda item: (-item["max_e_value"], item["id"]))
    stream_count = len(streams)
    strongest = streams[0]
    local_max = float(strongest["max_e_value"])
    familywise_e = local_max / stream_count
    total_samples = sum(int(item["samples"]) for item in streams)
    total_covered = sum(int(item["covered"]) for item in streams)
    overall_coverage = total_covered / total_samples if total_samples else 1.0

    drift = familywise_e >= FAMILYWISE_STOP_E_VALUE
    local_watch = (
        local_max >= LOCAL_WATCH_E_VALUE
        or float(strongest["empirical_coverage"]) <= TARGET_COVERAGE - 0.10
    )
    for row in streams:
        adjusted = float(row["max_e_value"]) / stream_count
        if adjusted >= FAMILYWISE_STOP_E_VALUE:
            row["status"] = "drift"
        elif (
            float(row["max_e_value"]) >= LOCAL_WATCH_E_VALUE
            or float(row["empirical_coverage"]) <= TARGET_COVERAGE - 0.10
        ):
            row["status"] = "watch"
        else:
            row["status"] = "trusted"
        row["familywise_e_value"] = round(adjusted, 4)

    if drift:
        status = "drift"
        automation_mode = "human_required"
        summary = (
            f"{strongest['flight_id']} {strongest['milestone']} 的序贯证据触发全局漂移闸门："
            f"校正 e={familywise_e:.2f} ≥ {FAMILYWISE_STOP_E_VALUE:g}；"
            "停止无人值守执行并要求重校准。"
        )
    elif local_watch:
        status = "watch"
        automation_mode = "guarded"
        summary = (
            f"{stream_count} 条留出覆盖流持续监测；{strongest['flight_id']} "
            f"{strongest['milestone']} 覆盖率 {float(strongest['empirical_coverage']) * 100:.0f}%，"
            f"局部 e={local_max:.2f}，尚未越过全局安全停机阈值。"
        )
    else:
        status = "trusted"
        automation_mode = "guarded_autonomy"
        summary = (
            f"{stream_count} 条留出覆盖流未发现显著失配；整体覆盖率 "
            f"{overall_coverage * 100:.1f}%，继续在已授权包络内运行。"
        )

    return {
        "method": "anytime-valid Bernoulli e-process with family-wise correction",
        "status": status,
        "automation_mode": automation_mode,
        "blocks_unattended_execution": drift,
        "target_coverage": TARGET_COVERAGE,
        "alternative_coverage": ALTERNATIVE_COVERAGE,
        "familywise_streams": stream_count,
        "familywise_e_value": round(familywise_e, 4),
        "local_max_e_value": round(local_max, 4),
        "local_watch_threshold": LOCAL_WATCH_E_VALUE,
        "stop_threshold": FAMILYWISE_STOP_E_VALUE,
        "overall_empirical_coverage": round(overall_coverage, 4),
        "samples_per_stream": min(int(item["samples"]) for item in streams),
        "strongest_stream": strongest["id"],
        "rows": streams,
        "summary": summary,
        "source": "disjoint SimPy holdout residual streams",
        "live_telemetry": False,
        "guarantee": "Ville inequality + family-wise union correction",
        "caveat": (
            "当前证据来自可复现的独立留出仿真，不是现场遥测；"
            "接入 A-CDM 后可用真实 ELDT/EIBT/TOBT 残差原位更新同一 e-process。"
        ),
    }
