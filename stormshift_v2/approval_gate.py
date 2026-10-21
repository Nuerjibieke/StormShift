"""Per-candidate evidence and human-approval gates.

The gate does not schedule aircraft.  It binds a solver candidate to the
trusted-state digest and turns cross-agent contract findings into an
enforceable approval decision.
"""

from __future__ import annotations

from typing import Any

from .schemas import PlanCandidate


APPROVABLE_STATUSES = {"ready", "human_required"}


def build_candidate_approval_gates(
    plans: list[PlanCandidate],
    arbitration_by_plan: dict[str, dict[str, Any]],
    trusted_state: dict[str, Any],
    resolved_query_ids: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    trusted_digest = str(trusted_state.get("digest", ""))
    resolved = sorted(set(resolved_query_ids or []))
    gates: dict[str, dict[str, Any]] = {}

    for plan in plans:
        audit = arbitration_by_plan.get(plan.id, {})
        audit_status = str(audit.get("status", "blocked"))
        required_checks = [
            item
            for item in audit.get("checks", [])
            if item.get("status") == "needs_evidence"
        ]
        required_queries = [
            {
                "query_id": str(item["query_id"]),
                "producer": str(item.get("producer", "")),
                "consumer": str(item.get("consumer", "")),
                "contract": str(item.get("contract", "")),
                "evidence": str(item.get("evidence", "")),
                "consequence": str(item.get("consequence", "")),
            }
            for item in required_checks
            if item.get("query_id")
        ]
        hard_blocks = [
            str(item.get("consequence") or item.get("evidence") or item.get("id"))
            for item in audit.get("checks", [])
            if item.get("hard_block")
        ]
        plan_digest = str(plan.evidence_basis.get("trusted_state_digest", ""))
        binding_valid = bool(
            trusted_digest
            and plan_digest
            and trusted_digest == plan_digest
        )

        if not plan.feasible or hard_blocks or audit_status == "blocked":
            status = "blocked"
        elif not binding_valid:
            status = "blocked"
            hard_blocks.append("计划证据摘要与当前可信运行状态不一致")
        elif required_queries or audit_status == "needs_evidence":
            status = "needs_evidence"
        elif audit_status == "human_required":
            status = "human_required"
        else:
            status = "ready"

        can_approve = status in APPROVABLE_STATUSES
        if status == "blocked":
            summary = "候选存在硬冲突或证据绑定失效，禁止批准。"
        elif status == "needs_evidence":
            summary = (
                f"批准前必须补齐 {len(required_queries)} 项安全证据并重新求解。"
            )
        elif status == "human_required":
            summary = "硬约束与证据通过；不确定性风险需要操作员明确承担。"
        else:
            summary = "候选证据、硬约束和 Agent 接口契约全部通过，可人工批准。"

        gates[plan.id] = {
            "schema_version": 1,
            "plan_id": plan.id,
            "status": status,
            "can_human_approve": can_approve,
            "next_action": (
                "stop_and_repair"
                if status == "blocked"
                else "observe_then_replan"
                if status == "needs_evidence"
                else "human_acknowledge_risk"
                if status == "human_required"
                else "human_approve"
            ),
            "required_query_ids": [
                item["query_id"] for item in required_queries
            ],
            "required_queries": required_queries,
            "hard_block_reasons": hard_blocks,
            "resolved_query_ids": resolved,
            "risk_disclosure": str(
                plan.risk_shield.get(
                    "summary",
                    "没有额外不确定性风险披露",
                )
            ),
            "evidence_binding": {
                "trusted_state_digest": trusted_digest,
                "plan_evidence_digest": plan_digest,
                "valid": binding_valid,
            },
            "arbitration_status": audit_status,
            "arbitration_summary": str(audit.get("summary", "")),
            "summary": summary,
            "human_approved": False,
        }

    return gates


def prioritize_required_evidence(
    information: dict[str, Any],
    gate: dict[str, Any] | None,
) -> dict[str, Any]:
    result = dict(information)
    required = list((gate or {}).get("required_query_ids", []))
    queries = [dict(item) for item in result.get("queries", [])]
    for query in queries:
        query["mandatory_for_selected_plan"] = query.get("id") in required
    result["queries"] = queries
    if required:
        query_id = required[0]
        result["status"] = "observe_then_optimize"
        result["recommended_query_id"] = query_id
        result["recommendation_basis"] = "approval_gate_required_evidence"
        result["summary"] = (
            f"安全审批闸门要求先完成 {query_id}，观测后必须重新求解。"
        )
        result["summary_en"] = (
            f"The approval gate requires {query_id} before re-solving and approval."
        )
    else:
        result["recommendation_basis"] = (
            result.get("recommendation_basis")
            or "optional_value_of_information"
        )
    return result


def require_human_approvable(
    gate: dict[str, Any] | None,
    plan_id: str,
) -> dict[str, Any]:
    if gate is None:
        raise ValueError(f"候选 {plan_id} 没有可验证的审批闸门")
    if gate.get("status") == "needs_evidence":
        required = ", ".join(gate.get("required_query_ids", [])) or "未知证据"
        raise ValueError(f"缺少必需安全证据：{required}；请先观测并重新求解")
    if gate.get("status") == "blocked":
        reasons = "；".join(gate.get("hard_block_reasons", []))
        raise ValueError(f"候选被安全闸门阻断：{reasons or '硬约束或证据绑定失败'}")
    if not gate.get("can_human_approve"):
        raise ValueError(f"候选 {plan_id} 当前不可批准")
    return gate

