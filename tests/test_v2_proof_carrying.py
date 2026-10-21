import asyncio
import copy
from dataclasses import replace

import pytest

from stormshift_v2.config import settings
from stormshift_v2.optimizer import solve_candidate
from stormshift_v2.proof_carrying import (
    issue_plan_certificate,
    verify_plan_certificate,
)
from stormshift_v2.risk_shield import assess_conformal_risk
from stormshift_v2.schemas import (
    DataMode,
    Mode,
    PlanProfile,
    PlanRequest,
    SessionCreate,
)
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.service import SessionService, UnsafePlan
from stormshift_v2.simulation import simulate_candidate
from stormshift_v2.store import SessionStore
from stormshift_v2.stream import StreamManager
from stormshift_v2.trusted_state import (
    build_trusted_operational_state,
    plan_evidence_basis,
)


def _certified_plan():
    scenario = storm_scenario()
    trusted = build_trusted_operational_state(scenario, DataMode.synthetic)
    plan, failure = solve_candidate(scenario, PlanProfile.balanced)
    assert failure is None and plan is not None
    plan.report = simulate_candidate(scenario, plan, runs=12)
    plan.risk_shield = assess_conformal_risk(scenario, plan)
    plan.evidence_basis = plan_evidence_basis(trusted)
    certificate = issue_plan_certificate(plan, trusted, issued_sequence=7)
    return plan, trusted, certificate


def test_fresh_proof_carrying_plan_verifies_all_bindings():
    plan, trusted, certificate = _certified_plan()

    verification = verify_plan_certificate(certificate, plan, trusted)

    assert certificate["version"] == "stormshift-proof-carrying-plan/v1"
    assert certificate["obligation_count"] == 5
    assert certificate["passed_obligations"] == 5
    assert certificate["failed_obligations"] == 0
    assert certificate["status"] in {
        "ready_for_approval",
        "human_review_required",
    }
    assert verification["valid"] is True
    assert verification["status"] == "verified"
    assert all(item["passed"] for item in verification["checks"])


def test_modified_assignment_is_rejected_by_executable_binding():
    plan, trusted, certificate = _certified_plan()
    mutated = plan.model_copy(deep=True)
    original = mutated.assignments[0]
    mutated.assignments[0] = original.model_copy(update={
        "landing_minute": original.landing_minute + 1,
    })

    verification = verify_plan_certificate(certificate, mutated, trusted)

    assert verification["valid"] is False
    assert "executable_binding" in verification["failed_checks"]
    assert "certificate_integrity" not in verification["failed_checks"]


def test_changed_trusted_snapshot_is_rejected_as_stale_evidence():
    plan, trusted, certificate = _certified_plan()
    changed = copy.deepcopy(trusted)
    changed["digest"] = "f" * 64

    verification = verify_plan_certificate(certificate, plan, changed)

    assert verification["valid"] is False
    assert "trusted_input_binding" in verification["failed_checks"]
    assert "evidence_bundle_binding" in verification["failed_checks"]


def test_tampered_certificate_fails_integrity_check():
    plan, trusted, certificate = _certified_plan()
    tampered = copy.deepcopy(certificate)
    tampered["obligations"][0]["evidence"] = "tampered after issuance"

    verification = verify_plan_certificate(tampered, plan, trusted)

    assert verification["valid"] is False
    assert "certificate_integrity" in verification["failed_checks"]


def test_service_issues_certificate_and_refuses_tampered_plan(tmp_path):
    service = SessionService(
        config=replace(settings, data_dir=tmp_path, llm_mode="off"),
        store=SessionStore(tmp_path / "proof-carrying.db"),
        streams=StreamManager(),
    )

    async def exercise():
        state = await service.create(SessionCreate(
            mode=Mode.guided,
            scenario_id="storm",
            data_mode=DataMode.synthetic,
            seed=2070,
        ))
        planned = await service.plan(
            state.id,
            PlanRequest(profiles=[PlanProfile.balanced]),
        )

        certificates = planned.decision_lab["plan_certificates"]
        assert set(certificates) == {plan.id for plan in planned.plans}
        certificate = certificates[planned.plans[0].id]
        assert certificate["failed_obligations"] == 0
        assert any(
            trace.agent == "Proof-Carrying Plan Verifier"
            for trace in planned.traces
        )

        assignment = planned.plans[0].assignments[0]
        planned.plans[0].assignments[0] = assignment.model_copy(update={
            "landing_minute": assignment.landing_minute + 1,
        })
        service.store.save_session(planned)
        with pytest.raises(UnsafePlan, match="携证计划校验失败"):
            await service.approve(planned.id, planned.plans[0].id)

        rejected = [
            event for event in service.store.replay(planned.id)
            if event.type == "plan_certificate_rejected"
        ]
        assert len(rejected) == 1
        assert "executable_binding" in rejected[0].payload["failed_checks"]
        assert rejected[0].payload["switch_executed"] is False

    asyncio.run(exercise())
