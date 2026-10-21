from __future__ import annotations

import asyncio
from dataclasses import replace
import pytest

from stormshift_v2.config import settings
from stormshift_v2.schemas import ObservationRequest, PlanProfile, PlanRequest, SessionCreate
from stormshift_v2.service import SessionService
from stormshift_v2.store import SessionStore
from stormshift_v2.stream import StreamManager


def make_service(tmp_path):
    return SessionService(
        config=replace(settings, data_dir=tmp_path, cache_dir=tmp_path / "cache", llm_mode="off", voice_mode="off"),
        store=SessionStore(tmp_path / "receipt.db"), streams=StreamManager(),
    )


def test_observation_keeps_review_context_after_replan_and_reload(tmp_path):
    async def run():
        service = make_service(tmp_path)
        state = await service.create(SessionCreate(seed=2026))
        state = await service.plan(state.id, PlanRequest(profiles=[PlanProfile.balanced]))
        old_plan = state.plans[0]
        old_gate = state.decision_lab["approval_gates"][old_plan.id]
        assert old_gate["status"] == "needs_evidence"
        before_minute = state.scenario.current_minute
        observed = await service.acquire_observation(state.id, ObservationRequest(
            query_id="weather_reopen_nowcast", source_plan_id=old_plan.id,
        ))
        receipt = observed.data_snapshot["active_observations"][-1]
        assert receipt["review_context"]["plan_id"] == old_plan.id
        assert receipt["review_context"]["profile"] == "balanced"
        assert receipt["review_context"]["gate_status"] == "needs_evidence"
        assert receipt["review_context"]["required_evidence"] == old_gate["required_queries"]
        assert receipt["before"]["reopen_minute"] == 24
        assert receipt["observed"]["reopen_minute"] == 23
        assert receipt["synthetic"] is True
        assert observed.execution is None
        replanned = await service.plan(state.id, PlanRequest(profiles=[PlanProfile.balanced], replan=True))
        restored = make_service(tmp_path).require(state.id)
        assert restored.data_snapshot["active_observations"][-1] == receipt
        assert restored.plans[0].id != old_plan.id
        assert restored.plans[0].profile == old_plan.profile
        assert restored.execution is None
        assert restored.scenario.current_minute == before_minute
        event = next(e for e in service.replay(state.id) if e.type == "active_observation_acquired")
        assert event.payload["review_context"] == receipt["review_context"]
        assert replanned.decision_lab["approval_gates"][replanned.plans[0].id]["can_human_approve"]
        assert not any(e.type == "plan_approved" for e in service.replay(state.id))
    asyncio.run(run())


def test_unknown_reviewed_candidate_is_rejected_without_mutation(tmp_path):
    async def run():
        service = make_service(tmp_path)
        state = await service.create(SessionCreate(seed=2026))
        state = await service.plan(state.id, PlanRequest(profiles=[PlanProfile.balanced]))
        before = state.model_dump(mode="json")
        history = [e.model_dump(mode="json") for e in service.replay(state.id)]
        with pytest.raises(ValueError, match="候选已失效"):
            await service.acquire_observation(state.id, ObservationRequest(
                query_id="weather_reopen_nowcast", source_plan_id="not-current",
            ))
        assert service.require(state.id).model_dump(mode="json") == before
        assert [e.model_dump(mode="json") for e in service.replay(state.id)] == history
    asyncio.run(run())
