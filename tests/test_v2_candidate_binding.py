from __future__ import annotations

import asyncio
import json
from dataclasses import replace

from stormshift_v2.config import settings
from stormshift_v2.schemas import (
    ExecutionAdvanceRequest, IntentRequest, ObservationRequest, Phase,
    PlanProfile, PlanRequest, SessionCreate,
)
from stormshift_v2.service import SessionService


def test_nonrecommended_repair_keeps_its_own_evidence_after_approval_and_reload(tmp_path):
    async def run():
        config = replace(settings, data_dir=tmp_path, cache_dir=tmp_path / "cache",
                         llm_mode="off", voice_mode="off")
        service = SessionService(config=config)
        state = await service.create(SessionCreate(seed=2026))
        await service.submit_intent(state.id, IntentRequest(text="优先保护转机旅客"))
        await service.confirm_intent(state.id, True)
        state = await service.plan(state.id, PlanRequest())
        original = next(p for p in state.plans if p.profile == PlanProfile.balanced)
        await service.acquire_observation(state.id, ObservationRequest(
            query_id="weather_reopen_nowcast", source_plan_id=original.id))
        state = await service.plan(state.id, PlanRequest(replan=True))
        original = next(p for p in state.plans if p.profile == PlanProfile.balanced)
        state = (await service.approve(state.id, original.id)).session
        first = await service.advance_execution(state.id, ExecutionAdvanceRequest(
            target_minute=7, expected_revision=state.execution.revision, request_id="binding-t7"))
        prefix = [(p.flight_id, p.runway, p.landing_minute, p.gate)
                  for p in first.session.execution.committed_operations]
        incident = await service.advance_execution(state.id, ExecutionAdvanceRequest(
            target_minute=9, expected_revision=first.revision, request_id="binding-t9"))
        state = incident.session
        assert state.phase == Phase.awaiting_approval
        assert len(prefix) == 2
        chosen = next(p for p in state.plans if p.profile == PlanProfile.operator)
        recommended = state.plans[0]
        assert chosen.id != recommended.id
        lab = state.decision_lab
        assert set(lab["action_diff_by_plan"]) == {p.id for p in state.plans}
        for plan in state.plans:
            diff = lab["action_diff_by_plan"][plan.id]
            after = {r["flight_id"]: r["after"] for r in diff["rows"]}
            assert diff["replacement_plan_id"] == plan.id
            assert diff["committed_count_at_replan"] == 2
            assert diff["replan_minute"] == 9
            assert set(after).isdisjoint(p[0] for p in prefix)
            assert all(after[a.flight_id] == a.model_dump(mode="json") for a in plan.assignments)
            assert lab["recourse_by_plan"][plan.id]["replacement_plan_id"] == plan.id
        expected_diff = lab["action_diff_by_plan"][chosen.id]
        expected_recourse = lab["recourse_by_plan"][chosen.id]
        assert expected_diff != lab["action_diff_by_plan"][recommended.id]
        assert lab["approval_gates"][chosen.id]["can_human_approve"]
        # Retain the originally recommended historical event; approval must not rewrite it.
        original_event = next(e for e in incident.execution_events
                              if e.type.startswith("automatic_policy_replan"))
        recommended_diff = original_event.payload["action_diff"]
        approval = await service.approve(state.id, chosen.id)
        approved = approval.session
        assert approved.decision_lab["action_diff"] == expected_diff
        assert approved.decision_lab["recourse"] == expected_recourse
        assert approved.decision_lab["agent_arbitration"] == lab["agent_arbitration_by_plan"][chosen.id]
        approved_event = next(e for e in approval.execution_events if e.type == "plan_approved")
        assert approved_event.payload["action_diff"] == expected_diff
        assert approved_event.payload["recourse"] == expected_recourse
        assert [(p.flight_id, p.runway, p.landing_minute, p.gate)
                for p in approved.execution.committed_operations] == prefix
        finish = await service.advance_execution(state.id, ExecutionAdvanceRequest(
            target_minute=100, expected_revision=approved.execution.revision, request_id="binding-finish"))
        assert finish.session.phase == Phase.recovered
        restored_service = SessionService(config=config)
        restored = restored_service.require(state.id)
        assert len(restored.execution.committed_operations) == 8
        assert restored.decision_lab["action_diff"] == expected_diff
        assert restored.decision_lab["action_diff"]["committed_count_at_replan"] == 2
        assert restored.decision_lab["recourse"] == expected_recourse
        historical = next(e for e in restored_service.replay(state.id)
                          if e.sequence == original_event.sequence)
        assert historical.payload["action_diff"] == recommended_diff
        print(json.dumps({"qa_data_dir":str(tmp_path),"session_id":state.id,
                          "approved_plan":chosen.id,"changed":expected_diff["changed"],
                          "unchanged":expected_diff["unchanged"]},ensure_ascii=False))
    asyncio.run(run())
