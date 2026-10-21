from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

import stormshift_v2.api as api_module
from stormshift_v2.config import settings
from stormshift_v2.schemas import (
    EventRequest,
    EventSpec,
    EventType,
    ObservationRequest,
    Phase,
    PlanProfile,
    PlanRequest,
    SessionCreate,
)
from stormshift_v2.service import SessionService
from stormshift_v2.store import SessionStore
from stormshift_v2.stream import StreamManager


def _service(tmp_path) -> SessionService:
    return SessionService(
        config=replace(settings, data_dir=tmp_path, llm_mode="off"),
        store=SessionStore(tmp_path / "approval-gate.db"),
        streams=StreamManager(),
    )


def test_missing_required_evidence_returns_409_without_execution(tmp_path, monkeypatch):
    async def exercise():
        service = _service(tmp_path)
        monkeypatch.setattr(api_module, "service", service)
        state = await service.create(SessionCreate(seed=2026))
        state = await service.plan(
            state.id,
            PlanRequest(profiles=[PlanProfile.balanced]),
        )
        selected = state.plans[0]
        gate = state.decision_lab["approval_gates"][selected.id]

        assert gate["status"] == "needs_evidence"
        assert gate["can_human_approve"] is False
        assert gate["required_query_ids"] == ["weather_reopen_nowcast"]

        transport = httpx.ASGITransport(app=api_module.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/v2/sessions/{state.id}/plans/{selected.id}/approve"
            )

        assert response.status_code == 409
        rejected = service.require(state.id)
        assert rejected.phase == Phase.awaiting_approval
        assert rejected.execution is None
        event_types = [event.type for event in service.replay(state.id)]
        assert "approval_gate_rejected" in event_types
        assert "plan_approved" not in event_types

        observed = await service.acquire_observation(
            state.id,
            ObservationRequest(query_id="weather_reopen_nowcast"),
        )
        assert observed.phase == Phase.incident
        replanned = await service.plan(
            state.id,
            PlanRequest(profiles=[PlanProfile.balanced], replan=True),
        )
        replacement = replanned.plans[0]
        refreshed_gate = replanned.decision_lab["approval_gates"][replacement.id]
        assert refreshed_gate["can_human_approve"] is True
        assert refreshed_gate["status"] in {"ready", "human_required"}

        approved = await service.approve(replanned.id, replacement.id)
        assert approved.session.phase == Phase.executing
        approved_gate = approved.session.decision_lab["approval_gates"][replacement.id]
        assert approved_gate["human_approved"] is True
        assert approved_gate["risk_acknowledged"] is True

    asyncio.run(exercise())


def test_new_operational_event_invalidates_bound_observation(tmp_path):
    async def exercise():
        service = _service(tmp_path)
        state = await service.create(SessionCreate(seed=2026))
        state = await service.plan(
            state.id,
            PlanRequest(profiles=[PlanProfile.balanced]),
        )
        state = await service.acquire_observation(
            state.id,
            ObservationRequest(query_id="weather_reopen_nowcast"),
        )
        assert "weather_reopen_nowcast" in state.data_snapshot["resolved_observation_ids"]
        assert state.data_snapshot["active_observations"][-1]["valid"] is True

        changed = await service.inject_event(
            state.id,
            EventRequest(
                event=EventSpec(
                    type=EventType.runway_closed,
                    minute=state.scenario.current_minute,
                    resource_id="07R",
                    severity=4,
                    duration_min=12,
                    payload={"source": "approval_gate_test"},
                )
            ),
        )

        assert "weather_reopen_nowcast" not in changed.data_snapshot["resolved_observation_ids"]
        observation = changed.data_snapshot["active_observations"][-1]
        assert observation["valid"] is False
        assert observation["invalidated_by_event_id"]
        invalidation = [
            event
            for event in service.replay(state.id)
            if event.type == "active_observation_invalidated"
        ]
        assert invalidation
        assert invalidation[-1].payload["invalidated_query_ids"] == [
            "weather_reopen_nowcast"
        ]

    asyncio.run(exercise())
