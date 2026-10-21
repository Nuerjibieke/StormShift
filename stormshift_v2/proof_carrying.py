"""Proof-carrying certificates that bind trusted evidence to executable schedules.

Inspired by proof-carrying code, this module detects stale inputs, modified
assignments, and missing proof obligations before approval or automatic switch.
It does not claim that a schedule is formally safe in the real world.
"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from .schemas import PlanCandidate


CERTIFICATE_VERSION = "stormshift-proof-carrying-plan/v1"


def _digest(payload: dict[str, Any]) -> str:
    return sha256(json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def _plan_payload(plan: PlanCandidate) -> dict[str, Any]:
    return {
        "plan_id": plan.id,
        "profile": plan.profile.value,
        "assignments": [
            item.model_dump(mode="json")
            for item in sorted(
                plan.assignments,
                key=lambda row: (row.landing_minute, row.flight_id),
            )
        ],
        "feasible": plan.feasible,
        "constraint_proof": list(plan.constraint_proof),
        "simulation_report": plan.report.model_dump(mode="json"),
        "risk_shield": plan.risk_shield,
        "evidence_basis": plan.evidence_basis,
    }


def plan_fingerprint(plan: PlanCandidate) -> str:
    """Hash only safety-relevant plan artifacts, never LLM explanatory prose."""
    return _digest(_plan_payload(plan))


def _evidence_payload(
    plan: PlanCandidate,
    trusted_state: dict[str, Any],
) -> dict[str, Any]:
    argumentation = trusted_state.get("argumentation", {})
    return {
        "trusted_state_digest": trusted_state.get("digest"),
        "trusted_state_status": trusted_state.get("status"),
        "argumentation": {
            "method": argumentation.get("method"),
            "status": argumentation.get("status"),
            "conflict_free": argumentation.get("conflict_free"),
            "accepted_ids": sorted(argumentation.get("accepted_ids", [])),
            "rejected_ids": sorted(argumentation.get("rejected_ids", [])),
            "undecided_ids": sorted(argumentation.get("undecided_ids", [])),
        },
        "plan_evidence_basis": plan.evidence_basis,
    }


def evidence_fingerprint(
    plan: PlanCandidate,
    trusted_state: dict[str, Any],
) -> str:
    return _digest(_evidence_payload(plan, trusted_state))


def _obligation(
    obligation_id: str,
    label: str,
    satisfied: bool,
    evidence: str,
    *,
    status: str | None = None,
) -> dict[str, Any]:
    return {
        "id": obligation_id,
        "label": label,
        "mandatory": True,
        "status": status or ("pass" if satisfied else "fail"),
        "satisfied": satisfied,
        "evidence": evidence,
    }


def _hash_payload(certificate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in certificate.items()
        if key not in {"certificate_id", "certificate_hash"}
    }


def issue_plan_certificate(
    plan: PlanCandidate,
    trusted_state: dict[str, Any],
    *,
    issued_sequence: int = 0,
) -> dict[str, Any]:
    """Issue a deterministic proof bundle for one solver-generated plan."""
    argumentation = trusted_state.get("argumentation", {})
    proof_ok = bool(
        plan.feasible
        and plan.constraint_proof
        and not any(str(row).startswith("FAIL") for row in plan.constraint_proof)
    )
    simulation_ok = int(plan.report.runs) > 0
    shield_ok = bool(
        plan.risk_shield
        and plan.risk_shield.get("status") in {"clear", "watch", "escalate"}
    )
    human_review = bool(plan.risk_shield.get("blocks_unattended_execution", False))
    grounded = bool(
        trusted_state.get("status") == "ready"
        and argumentation.get("status") == "resolved"
        and argumentation.get("conflict_free") is True
        and not argumentation.get("undecided_ids")
    )
    basis_ok = (
        plan.evidence_basis.get("trusted_state_digest")
        == trusted_state.get("digest")
    )
    obligations = [
        _obligation(
            "trusted_evidence_grounded",
            "可信证据形成无冲突 grounded extension",
            grounded,
            (
                f"{len(argumentation.get('accepted_ids', []))} accepted · "
                f"{len(argumentation.get('rejected_ids', []))} rejected · "
                f"{len(argumentation.get('undecided_ids', []))} undecided"
            ),
        ),
        _obligation(
            "solver_input_bound",
            "候选绑定唯一可信输入快照",
            basis_ok,
            str(trusted_state.get("digest", ""))[:16],
        ),
        _obligation(
            "independent_constraint_proof",
            "独立验证器未发现硬约束失败",
            proof_ok,
            f"{len(plan.constraint_proof)} proof statements",
        ),
        _obligation(
            "simulation_evidence_present",
            "候选包含固定种子仿真证据",
            simulation_ok,
            f"{plan.report.runs} Monte Carlo runs",
        ),
        _obligation(
            "uncertainty_guard_evaluated",
            "不确定性运行闸门已经评估",
            shield_ok,
            (
                f"{plan.risk_shield.get('status', 'missing')} · "
                f"{plan.risk_shield.get('automation_mode', 'unknown')}"
            ),
            status=(
                "human_review"
                if shield_ok and human_review
                else "pass"
                if shield_ok
                else "fail"
            ),
        ),
    ]
    failures = sum(not row["satisfied"] for row in obligations)
    certificate: dict[str, Any] = {
        "version": CERTIFICATE_VERSION,
        "plan_id": plan.id,
        "profile": plan.profile.value,
        "issued_sequence": int(issued_sequence),
        "input_digest": trusted_state.get("digest"),
        "executable_fingerprint": plan_fingerprint(plan),
        "evidence_fingerprint": evidence_fingerprint(plan, trusted_state),
        "status": (
            "blocked"
            if failures
            else "human_review_required"
            if human_review
            else "ready_for_approval"
        ),
        "execution_mode": (
            "human_approval_required" if human_review else "guarded_execution"
        ),
        "obligations": obligations,
        "obligation_count": len(obligations),
        "passed_obligations": len(obligations) - failures,
        "failed_obligations": failures,
        "scope": "evidence-to-action integrity for this exercise session",
        "caveat": (
            "SHA-256 detects mutation only when its hash is independently retained. "
            "This is not a digital signature, real-world safety proof, or ATC authorization."
        ),
    }
    certificate_hash = _digest(_hash_payload(certificate))
    certificate["certificate_id"] = f"pcp_{certificate_hash[:12]}"
    certificate["certificate_hash"] = certificate_hash
    return certificate


def verify_plan_certificate(
    certificate: dict[str, Any] | None,
    plan: PlanCandidate,
    trusted_state: dict[str, Any],
) -> dict[str, Any]:
    """Verify certificate integrity, executable binding, and evidence freshness."""
    certificate = certificate or {}
    stored_hash = str(certificate.get("certificate_hash", ""))
    expected_hash = _digest(_hash_payload(certificate)) if certificate else ""
    checks = [
        {
            "id": "certificate_integrity",
            "passed": bool(stored_hash and stored_hash == expected_hash),
            "evidence": stored_hash[:16] if stored_hash else "missing certificate",
        },
        {
            "id": "plan_identity",
            "passed": certificate.get("plan_id") == plan.id,
            "evidence": f"{certificate.get('plan_id')} → {plan.id}",
        },
        {
            "id": "executable_binding",
            "passed": certificate.get("executable_fingerprint") == plan_fingerprint(plan),
            "evidence": str(certificate.get("executable_fingerprint", ""))[:16],
        },
        {
            "id": "trusted_input_binding",
            "passed": certificate.get("input_digest") == trusted_state.get("digest"),
            "evidence": (
                f"{str(certificate.get('input_digest', ''))[:12]} → "
                f"{str(trusted_state.get('digest', ''))[:12]}"
            ),
        },
        {
            "id": "evidence_bundle_binding",
            "passed": (
                certificate.get("evidence_fingerprint")
                == evidence_fingerprint(plan, trusted_state)
            ),
            "evidence": str(certificate.get("evidence_fingerprint", ""))[:16],
        },
        {
            "id": "proof_obligations_satisfied",
            "passed": bool(
                certificate.get("obligations")
                and all(
                    bool(row.get("satisfied"))
                    for row in certificate.get("obligations", [])
                    if row.get("mandatory", True)
                )
            ),
            "evidence": (
                f"{certificate.get('passed_obligations', 0)}/"
                f"{certificate.get('obligation_count', 0)}"
            ),
        },
    ]
    failed_checks = [row["id"] for row in checks if not row["passed"]]
    valid = not failed_checks
    return {
        "method": "canonical JSON + SHA-256 proof-carrying schedule verification",
        "status": "verified" if valid else "rejected",
        "valid": valid,
        "certificate_id": certificate.get("certificate_id"),
        "certificate_hash": stored_hash,
        "plan_id": plan.id,
        "checks": checks,
        "failed_checks": failed_checks,
        "summary": (
            f"携证计划 {certificate.get('certificate_id')} 通过 {len(checks)}/{len(checks)} 项绑定检查"
            if valid
            else f"携证计划拒绝执行：{', '.join(failed_checks)}"
        ),
        "caveat": certificate.get(
            "caveat",
            "Missing proof-carrying plan certificate.",
        ),
    }
