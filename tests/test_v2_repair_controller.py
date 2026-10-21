import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from stormshift_v2 import branch_rehearsal as rehearsal, repair_controller as controller, llm
from stormshift_v2.branch_preview import BranchPreviewRequest, build_branch_preview, preview_id
from stormshift_v2.config import settings
from stormshift_v2.online_execution import advance_state
from stormshift_v2.schemas import EventSpec, EventType, ExecutionAdvanceRequest
from test_v2_branch_preview import fixture, request
from test_v2_branch_rehearsal import outage

OFF = replace(settings, llm_mode="off")
ON = replace(settings, llm_mode="on")


def packet_fixture(tmp_path):
    _, state = fixture(tmp_path)
    plan = state.plans[0]
    event = outage(state, plan)
    branch = rehearsal._snapshot_for_plan(state, plan, [event])
    port = rehearsal._MemoryExecutionPort(branch)
    asyncio.run(advance_state(port, branch, ExecutionAdvanceRequest(
        target_minute=480, expected_revision=1, request_id="setup"),
        stop_before_incident_response=True))
    failures = [line for line in rehearsal._proof(branch) if line.startswith("FAIL")]
    return state, plan, event, controller.feedback_packet(branch, plan, failures, [])


@pytest.mark.parametrize("change", [
    {"source_digest": "0" * 64},
    {"evidence_ids": ["invented"]},
    {"evidence_ids": ["scope"]},
    {"tool": "preserve_assignments", "preserve_flight_ids": ["NOT-A-FLIGHT"]},
    {"tool": "replan_remaining", "preserve_flight_ids": ["NOT-A-FLIGHT"]},
    {"safety_override": True},
    {"tool": "open_runway"},
])
def test_invalid_or_ungrounded_model_proposals_cannot_execute(tmp_path, change, monkeypatch):
    _, _, _, packet = packet_fixture(tmp_path)
    payload = controller.deterministic_proposal(packet).model_dump()
    payload.update(change)
    async def fake(*args):
        return payload
    monkeypatch.setattr(llm, "_structured_completion", fake)
    proposal, source = asyncio.run(controller.propose_repair(packet, ON))
    assert source["source"] == "deterministic_fallback"
    assert source["model_calls"] == 1
    assert proposal == controller.deterministic_proposal(packet)
    controller.validate_proposal(proposal, packet)


def test_model_timeout_and_disabled_modes_are_distinguished(tmp_path, monkeypatch):
    _, _, _, packet = packet_fixture(tmp_path)
    calls = []
    async def fail(*args):
        calls.append(1)
        raise httpx.ReadTimeout("unavailable")
    monkeypatch.setattr(llm, "_structured_completion", fail)
    _, disabled = asyncio.run(controller.propose_repair(packet, OFF))
    assert calls == [] and disabled["model_calls"] == 0
    _, timeout = asyncio.run(controller.propose_repair(packet, ON))
    assert len(calls) == 1 and timeout["fallback_reason"] == "model_timeout"


def test_valid_model_choice_actually_controls_solver_not_just_explanation(tmp_path, monkeypatch):
    state, plan, event, packet = packet_fixture(tmp_path)
    seen = []
    async def choose(*args):
        facts = json.loads(args[4])
        seen.append(facts)
        return controller.RepairProposal(
            source_digest=facts["source_digest"], tool="replan_remaining",
            preserve_flight_ids=[], evidence_ids=["failure:0"], reason_code="constraint_failure")
    monkeypatch.setattr(llm, "_structured_completion", choose)
    before = state.model_dump(mode="json")
    result = rehearsal.rehearse_candidate(state, plan, [event], feedback=True, config=ON)
    assert len(seen) == 1
    assert "scheduled_events" not in seen[0]
    assert result["proposal_trace"][0]["source"] == "fast_model"
    assert result["continuation"]["repairs"][0]["proposal"]["tool"] == "replan_remaining"
    assert result["continuation"]["status"] == "completed"
    assert state.model_dump(mode="json") == before


def test_failed_solve_is_fed_back_before_second_model_choice(tmp_path, monkeypatch):
    state, plan, event, _ = packet_fixture(tmp_path)
    calls, packets = [], []
    real = rehearsal.solve_candidate
    def first_fails(*args, **kwargs):
        calls.append(kwargs)
        return (None, None) if len(calls) == 1 else real(*args, **kwargs)
    async def choose(*args):
        facts = json.loads(args[4])
        packets.append(facts)
        return controller.RepairProposal(
            source_digest=facts["source_digest"], tool="replan_remaining",
            preserve_flight_ids=[],
            evidence_ids=["attempt:0"] if len(packets) > 1 else ["failure:0"],
            reason_code="retry_after_infeasible" if len(packets) > 1 else "constraint_failure")
    monkeypatch.setattr(rehearsal, "solve_candidate", first_fails)
    monkeypatch.setattr(llm, "_structured_completion", choose)
    result = rehearsal.rehearse_candidate(state, plan, [event], feedback=True, config=ON)
    assert len(calls) == len(packets) == result["model_calls"] == 2
    assert packets[1]["evidence"]["attempt:0"]["status"] == "infeasible"
    assert packets[1]["remaining_solver_calls"] == 1
    assert result["continuation"]["status"] == "completed"


def test_operator_escalation_does_not_solve_or_approve(tmp_path, monkeypatch):
    state, plan, event, _ = packet_fixture(tmp_path)
    async def choose(*args):
        facts = json.loads(args[4])
        return controller.RepairProposal(
            source_digest=facts["source_digest"], tool="request_operator", preserve_flight_ids=[],
            evidence_ids=["failure:0"], reason_code="human_review")
    monkeypatch.setattr(llm, "_structured_completion", choose)
    result = rehearsal.rehearse_candidate(state, plan, [event], feedback=True, config=ON)
    assert result["solver_calls"] == 0 and result["model_calls"] == 1
    assert result["continuation"]["stopped_reason"] == "agent_requests_operator"
    assert result["continuation"]["status"] == "needs_decision"
    assert result["live_approval"] is False


def test_fallback_preserves_only_individually_valid_pending_assignments(tmp_path):
    _, state = fixture(tmp_path)
    plan = state.plans[0]
    event = EventSpec(id="gate-break", type=EventType.gate_outage, minute=0,
                      resource_id=plan.assignments[0].gate, duration_min=60)
    result = rehearsal.rehearse_candidate(state, plan, [event], feedback=True, config=OFF)
    assert result["model_calls"] == 0
    trace = result["proposal_trace"][0]
    assert trace["source"] == "deterministic_fallback"
    assert trace["proposal"]["preserve_flight_ids"]
    for row in result["continuation"]["repairs"]:
        if row["status"] != "nominally_valid_not_approved":
            continue
        before = {a["flight_id"]: a for a in row["before_assignments"]}
        after = {a["flight_id"]: a for a in row["after_assignments"]}
        for fid in row["proposal"]["preserve_flight_ids"]:
            assert before[fid] == after[fid]


def test_comparison_is_opt_in_separate_cached_and_read_only(tmp_path):
    service, state = fixture(tmp_path)
    event = outage(state, state.plans[0])
    state.data_snapshot["scheduled_events"] = [event.model_dump(mode="json")]
    service.store.save_session(state)
    state = service.require(state.id)
    before = state.model_dump(mode="json")
    base = request(state)
    base.plan_ids = [state.plans[0].id]
    normal = asyncio.run(service.preview_branches(state.id, base))
    assert "feedback_rehearsal" not in normal["branches"][0]
    comparison_request = base.model_copy(update={"feedback": True})
    result = asyncio.run(service.preview_branches(state.id, comparison_request))
    branch = result["branches"][0]
    assert result["id"] != normal["id"] and result["feedback_requested"]
    assert branch["rehearsal"]["scheduled_events"] == branch["feedback_rehearsal"]["scheduled_events"]
    assert branch["report"] == normal["branches"][0]["report"]
    assert branch["repair_comparison"]["feedback"]["model_calls"] == 0
    assert service.require(state.id).model_dump(mode="json") == before
    assert service.store.replay(state.id) == []
    assert service.get_branch_preview(state.id, result["id"]) == result
    assert asyncio.run(service.preview_branches(state.id, comparison_request)) == result


def test_http_requires_single_candidate_and_checks_stale_source(tmp_path, monkeypatch):
    from stormshift_v2 import api
    service, state = fixture(tmp_path)
    monkeypatch.setattr(api, "service", service)
    client = TestClient(api.app)
    payload = request(state).model_dump()
    payload["feedback"] = True
    url = f"/api/v2/sessions/{state.id}/previews"
    assert client.post(url, json=payload).status_code == 422
    payload["plan_ids"] = [state.plans[0].id]
    state.sequence += 1
    service.store.save_session(state)
    assert client.post(url, json=payload).status_code == 409


def test_failed_branch_cannot_inherit_old_success(tmp_path, monkeypatch):
    state, plan, event, _ = packet_fixture(tmp_path)
    state.decision_lab["execution_outcome"] = {"turnaround_complete": 99, "hard_constraints_passed": True}
    monkeypatch.setattr(rehearsal, "solve_candidate", lambda *args, **kwargs: (None, None))
    result = rehearsal.rehearse_candidate(state, plan, [event], feedback=True, config=OFF)
    assert result["continuation"]["status"] == "needs_decision"
    assert result["continuation"]["outcome"] is None
    assert result["solver_calls"] == 2
    assert state.decision_lab["execution_outcome"]["turnaround_complete"] == 99


def test_buffer_requirements_survive_repair_and_execution(tmp_path):
    _, state = fixture(tmp_path)
    plan = state.plans[0]
    buffers = {row.flight_id: 3 for row in plan.assignments}
    plan.risk_shield = {"adaptive_envelope": {"applies_to_candidate": True,
                                            "turnaround_buffer_by_flight": buffers}}
    event = outage(state, plan)
    result = rehearsal.rehearse_candidate(state, plan, [event], feedback=True, config=OFF)
    assert result["continuation"]["status"] == "completed"
    for op in result["continuation"]["committed_operations"]:
        assert op["gate_release_minute"] - op["gate_start_minute"] == 25
