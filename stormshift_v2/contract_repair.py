"""Elastic repair for unmet probabilistic operations contracts.

The module never relaxes runway, separation, gate, fuel, or medical invariants.
It compares already solver-valid candidates and, only for service/risk targets,
computes the smallest transparent amendment that would describe the selected plan.
"""

from __future__ import annotations

from typing import Any

from .outcome_audit import build_outcome_audit
from .schemas import OperatorIntent, PlanCandidate, ScenarioSpec


def _violation(row: dict[str, Any]) -> float:
    actual = float(row["actual"]) if row["actual"] is not None else 0.0
    target = float(row["target"])
    comparison = str(row["comparison"])
    if comparison == "<=":
        gap = max(0.0, actual - target)
    elif comparison == ">=":
        gap = max(0.0, target - actual)
    else:
        gap = abs(actual - target)
    return gap / max(1.0, abs(target))


def _assignment_changes(left: PlanCandidate, right: PlanCandidate) -> int:
    left_map = {
        item.flight_id: (item.runway, item.gate, item.landing_minute)
        for item in left.assignments
    }
    right_map = {
        item.flight_id: (item.runway, item.gate, item.landing_minute)
        for item in right.assignments
    }
    return sum(
        left_map.get(flight_id) != right_map.get(flight_id)
        for flight_id in set(left_map) | set(right_map)
    )


def _target_fragment(row: dict[str, Any]) -> str:
    label = str(row["label"])
    actual = float(row["actual"])
    rendered = f"{actual:g}"
    unit = str(row.get("unit", "")).strip()
    bound = "下限" if row["comparison"] == ">=" else "上限"
    return f"将“{label}”{bound}调整为 {rendered}{unit}"


def _dominates(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_vector = (
        int(left["failed_targets"]),
        float(left["normalized_violation"]),
        int(left["schedule_changes"]),
    )
    right_vector = (
        int(right["failed_targets"]),
        float(right["normalized_violation"]),
        int(right["schedule_changes"]),
    )
    return all(a <= b for a, b in zip(left_vector, right_vector)) and any(
        a < b for a, b in zip(left_vector, right_vector)
    )


def analyze_contract_repair(
    scenario: ScenarioSpec,
    selected: PlanCandidate,
    candidates: list[PlanCandidate],
    intent: OperatorIntent | None = None,
) -> dict[str, Any]:
    """Return a minimum-relaxation frontier without weakening hard safety."""
    unique = {candidate.id: candidate for candidate in [selected, *candidates]}
    alternatives: list[dict[str, Any]] = []
    contracts: dict[str, dict[str, Any]] = {}
    for candidate in unique.values():
        audit = build_outcome_audit(
            scenario,
            candidate,
            intent,
            stage="contract_repair",
        )
        contract = audit["outcome_contract"]
        contracts[candidate.id] = contract
        failed_rows = [
            row for row in contract["rows"]
            if row["layer"] != "safety" and row["passed"] is False
        ]
        hard_safe = candidate.feasible and all(
            row["passed"] is True for row in contract["rows"] if row["layer"] == "safety"
        )
        unknown_ids = [row["id"] for row in contract["rows"] if row["passed"] is None]
        comparable_scope = ({item.flight_id for item in candidate.assignments}
                            == {item.flight_id for item in selected.assignments})
        evidence_complete = not unknown_ids and comparable_scope
        alternatives.append({
            "plan_id": candidate.id,
            "title": candidate.title,
            "profile": candidate.profile.value,
            "selected": candidate.id == selected.id,
            "hard_safe": hard_safe,
            "evidence_complete": evidence_complete,
            "comparable_scope": comparable_scope,
            "unknown_target_ids": unknown_ids,
            "contract_status": contract["status"],
            "passed": contract["passed"],
            "total": contract["total"],
            "failed_targets": len(failed_rows),
            "failed_labels": [str(row["label"]) for row in failed_rows],
            "normalized_violation": round(sum(_violation(row) for row in failed_rows), 4),
            "schedule_changes": _assignment_changes(selected, candidate),
            "mean_delay_min": candidate.report.mean_delay_min,
            "missed_connections": candidate.report.missed_connections,
            "tail_exposed": int(
                candidate.risk_shield.get("connection_passengers_exposed", 0) or 0
            ),
            "score": candidate.score,
        })

    for row in alternatives:
        row["frontier"] = row["hard_safe"] and row["evidence_complete"] and not any(
            other["plan_id"] != row["plan_id"] and other["hard_safe"]
            and other["evidence_complete"] and _dominates(other, row)
            for other in alternatives
        )
    alternatives.sort(key=lambda row: (
        not row["hard_safe"],
        not row["evidence_complete"],
        row["failed_targets"],
        row["normalized_violation"],
        row["schedule_changes"],
        row["score"],
        row["plan_id"],
    ))

    selected_contract = contracts[selected.id]
    selected_row = next(row for row in alternatives if row["plan_id"] == selected.id)
    selected_failed = [
        row for row in selected_contract["rows"]
        if row["layer"] != "safety" and row["passed"] is False
    ]
    selected_hard_safe = all(
        row["passed"] for row in selected_contract["rows"] if row["layer"] == "safety"
    )
    safe_alternatives = [row for row in alternatives
                         if row["hard_safe"] and row["evidence_complete"]]
    best = safe_alternatives[0] if safe_alternatives else selected_row
    best_improves = (
        (best["failed_targets"], best["normalized_violation"])
        < (selected_row["failed_targets"], selected_row["normalized_violation"])
    )

    repairs = [{
        "target_id": str(row["id"]),
        "label": str(row["label"]),
        "layer": str(row["layer"]),
        "source": str(row["source"]),
        "comparison": str(row["comparison"]),
        "from_target": row["target"],
        "to_target": row["actual"],
        "delta": round(abs(float(row["actual"]) - float(row["target"])), 2),
        "unit": str(row.get("unit", "")),
        "authority": "operator_confirmation" if row["source"] == "operator" else "demo_policy",
        "fragment": _target_fragment(row),
    } for row in selected_failed]

    explicit_safety_failure = not selected.feasible or any(
        row["passed"] is False for row in selected_contract["rows"] if row["layer"] == "safety"
    )
    if explicit_safety_failure:
        repairs = []
        status = "safety_block"
        recommendation = (
            "硬安全规则未通过；禁止目标协商或自动放宽。应重新求解、停止执行或转人工处置。"
        )
    elif not selected_row["evidence_complete"] or not selected_hard_safe:
        status = "evidence_required"
        repairs = []
        recommendation = "缺少可核验的完整指标或安全证据；先补齐同范围仿真证据，不宣称达标、不建议放宽目标。"
    elif not selected_failed:
        status = "contract_met"
        recommendation = "当前方案已满足全部安全规则和运营目标，无需修改契约。"
    elif best["plan_id"] != selected.id and best_improves:
        status = "switch_plan"
        recommendation = (
            f"候选“{best['title']}”可将未达标目标从 "
            f"{selected_row['failed_targets']} 项降至 {best['failed_targets']} 项；"
            f"建议人工确认切换，不修改硬安全规则。"
        )
    else:
        status = "minimal_amendment"
        recommendation = (
            f"现有安全候选均无法满足全部目标；当前方案需要 "
            f"{len(repairs)} 项最小目标修订，或保持原目标并启动深度重规划。"
        )

    return {
        "method": "elastic-constraint repair + lexicographic candidate frontier",
        "status": status,
        "selected_plan_id": selected.id,
        "recommended_plan_id": best["plan_id"] if status == "switch_plan" else selected.id,
        "hard_safety_relaxations": 0,
        "unmet_target_ids": [str(row["id"]) for row in selected_failed],
        "unknown_target_ids": selected_row["unknown_target_ids"],
        "repairs": repairs,
        "alternatives": alternatives,
        "suggested_command": "；".join(item["fragment"] for item in repairs),
        "recommendation": recommendation,
        "tradeoff": (
            "接受最小修订只会使契约与当前预测一致，不会改善真实运行表现；"
            "若目标不可妥协，应保留原阈值并重新求解。"
        ),
        "proof": (
            "修复集合只包含 service/risk_budget 行；safety 行被排除，"
            "跑道、间隔、机位、燃油和医疗优先规则不可放宽。"
        ),
    }

