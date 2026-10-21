from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

import stormshift_v2.api as api_module
from stormshift_v2.config import settings
from stormshift_v2.execution import create_execution_cursor
from stormshift_v2.optimizer import solve_candidate
from stormshift_v2.resource_calendar import validate_resource_calendar
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.schemas import (
    DataMode,
    EventRequest,
    EventSpec,
    EventType,
    ExecutionAdvanceRequest,
    ExecutionEvent,
    Mode,
    ObservationRequest,
    Phase,
    PlanProfile,
    PlanRequest,
    SessionCreate,
    SessionState,
)
from stormshift_v2.service import SessionService, StaleExecution
from stormshift_v2.store import SessionStore
from stormshift_v2.stream import StreamManager


def _service(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    service = SessionService(
        config=replace(settings, data_dir=tmp_path),
        store=store,
        streams=StreamManager(),
    )
    return service, store


def _operation_identity(operation):
    return (
        operation.flight_id,
        operation.plan_id,
        operation.revision,
        operation.landing_minute,
        operation.runway,
        operation.runway_release_minute,
        operation.gate,
        operation.gate_start_minute,
        operation.gate_release_minute,
    )

async def _resolve_required_evidence(service, state, profiles):
    for _ in range(3):
        selected = state.plans[0]
        gate = state.decision_lab.get("approval_gates", {}).get(selected.id, {})
        if gate.get("status") != "needs_evidence":
            return state
        required = gate.get("required_query_ids", [])
        assert required, gate
        await service.acquire_observation(
            state.id,
            ObservationRequest(query_id=required[0]),
        )
        state = await service.plan(
            state.id,
            PlanRequest(profiles=profiles, replan=True),
        )
    raise AssertionError("approval gate did not converge after required evidence")


def test_guided_execution_commits_prefix_replans_and_recovers(tmp_path):
    async def run():
        service, store = _service(tmp_path)
        created = await service.create(SessionCreate(seed=2026))
        planned = await service.plan(
            created.id,
            PlanRequest(profiles=[PlanProfile.balanced]),
        )
        planned = await _resolve_required_evidence(service, planned, [PlanProfile.balanced])
        original = planned.plans[0]
        approved = await service.approve(created.id, original.id)

        assert approved.session.phase == Phase.executing
        assert approved.session.execution is not None
        assert approved.session.scenario.current_minute == 0
        assert not any(
            event.type in {
                EventType.execution_tick.value,
                EventType.recovered.value,
                EventType.ground_vehicle_failure.value,
            }
            for event in approved.execution_events
        )
        assert approved.session.execution.pending_assignments
        assert approved.session.execution.scheduled_events[0].minute == 9
        approval_event_count = len(store.replay(created.id))
        duplicate_approval = await service.approve(created.id, original.id)
        assert duplicate_approval.execution_events == []
        assert duplicate_approval.session.execution.revision == 1
        assert len(store.replay(created.id)) == approval_event_count

        revision = approved.session.execution.revision
        first = await service.advance_execution(
            created.id,
            ExecutionAdvanceRequest(
                target_minute=7,
                expected_revision=revision,
                request_id="advance-t7",
            ),
        )
        assert first.session.scenario.current_minute == 7
        assert first.session.phase == Phase.monitoring
        assert len(first.session.execution.committed_operations) == 2
        prefix = {
            item.flight_id: _operation_identity(item)
            for item in first.session.execution.committed_operations
        }
        assert {
            event.payload["flight_id"]
            for event in first.execution_events
            if event.type == EventType.execution_tick.value
        } == set(prefix)

        event_count = len(store.replay(created.id))
        duplicate = await service.advance_execution(
            created.id,
            ExecutionAdvanceRequest(
                target_minute=7,
                expected_revision=revision,
                request_id="advance-t7",
            ),
        )
        assert [event.sequence for event in duplicate.execution_events] == [
            event.sequence for event in first.execution_events
        ]
        assert len(store.replay(created.id)) == event_count

        with pytest.raises(StaleExecution):
            await service.advance_execution(
                created.id,
                ExecutionAdvanceRequest(
                    target_minute=8,
                    expected_revision=revision,
                    request_id="stale-revision",
                ),
            )

        incident = await service.advance_execution(
            created.id,
            ExecutionAdvanceRequest(
                target_minute=9,
                expected_revision=first.revision,
                request_id="advance-t9",
            ),
        )
        assert incident.session.scenario.current_minute == 9
        assert incident.stopped_reason in {
            "incident_replanned",
            "incident_requires_approval",
        }
        incident_types = [event.type for event in incident.execution_events]
        assert EventType.ground_vehicle_failure.value in incident_types
        assert "trusted_state_reconciled" in incident_types
        assert any("replan" in value for value in incident_types)
        assert {
            item.flight_id: _operation_identity(item)
            for item in incident.session.execution.committed_operations
        } == prefix
        replan_event = next(
            event
            for event in incident.execution_events
            if event.type.startswith("automatic_policy_replan")
        )
        action_diff = replan_event.payload["action_diff"]
        assert action_diff == incident.session.decision_lab["action_diff"]
        assert action_diff["scope"] == "uncommitted future assignments only"
        assert sum(
            action_diff[key]
            for key in ("changed", "unchanged", "added", "removed")
        ) == len(action_diff["rows"])
        assert set(prefix).isdisjoint(
            row["flight_id"] for row in action_diff["rows"]
        )

        state = incident.session
        if state.phase == Phase.awaiting_approval:
            assert state.execution.status == "blocked"
            assert state.selected_plan_id is None
            state = await _resolve_required_evidence(service, state, [PlanProfile.balanced])
            reapproved = await service.approve(state.id, state.plans[0].id)
            state = reapproved.session
            assert state.phase == Phase.executing
            assert state.execution.status == "running"
            assert {
                item.flight_id: _operation_identity(item)
                for item in state.execution.committed_operations
            } == prefix

        finish = await service.advance_execution(
            state.id,
            ExecutionAdvanceRequest(
                target_minute=100,
                expected_revision=state.execution.revision,
                request_id="advance-finish",
            ),
        )
        assert finish.session.phase == Phase.recovered
        assert finish.session.execution.status == "completed"
        assert finish.stopped_reason == "recovered"
        assert finish.next_event_minute is None
        assert not validate_resource_calendar(
            finish.session.scenario,
            finish.session.scenario.committed_reservations,
        )
        outcome = finish.session.decision_lab["execution_outcome"]
        assert outcome["hard_constraints_passed"] is True
        assert outcome["pending"] == 0
        assert (
            outcome["turnaround_complete"]
            + outcome["safe_diversions"]
            == outcome["scope_flights"]
        )

        replay = store.replay(state.id)
        assert [
            (event.sim_minute, event.sequence) for event in replay
        ] == sorted(
            (event.sim_minute, event.sequence) for event in replay
        )
        ticks = [
            event.payload["flight_id"]
            for event in replay
            if event.type == EventType.execution_tick.value
        ]
        assert len(ticks) == len(set(ticks))
        assert set(ticks) == set(
            finish.session.execution.scope_flight_ids
        )
        assert store.verify_event_chain(state.id)["status"] == "verified"

    asyncio.run(run())


def test_future_event_is_queued_idempotently_and_past_is_rejected(tmp_path):
    async def run():
        service, store = _service(tmp_path)
        scenario = storm_scenario(seed=2080)
        scenario.current_minute = 3
        plan, failure = solve_candidate(
            scenario,
            PlanProfile.balanced,
            timeout_seconds=1,
        )
        assert failure is None and plan is not None
        state = SessionState(
            mode=Mode.scenarios,
            data_mode=DataMode.synthetic,
            scenario=scenario,
            phase=Phase.executing,
            plans=[plan],
            selected_plan_id=plan.id,
            execution=create_execution_cursor(scenario, plan),
        )
        state.execution.status = "running"
        store.save_session(state)
        store.save_plan(state.id, plan, approved=True)

        event = EventSpec(
            id="evt-future-gate",
            type=EventType.gate_outage,
            minute=8,
            resource_id="A4",
            severity=3,
            duration_min=10,
        )
        request = EventRequest(
            event=event,
            expected_revision=1,
            request_id="inject-one",
        )
        queued = await service.inject_event(state.id, request)
        assert queued.scenario.current_minute == 3
        assert next(
            gate for gate in queued.scenario.gates if gate.id == "A4"
        ).status != "blocked"
        assert [item.id for item in queued.execution.scheduled_events] == [
            event.id
        ]
        assert queued.execution.revision == 2
        count = len(store.replay(state.id))

        duplicate = await service.inject_event(state.id, request)
        assert duplicate.execution.revision == 2
        assert len(store.replay(state.id)) == count

        with pytest.raises(StaleExecution):
            await service.inject_event(
                state.id,
                EventRequest(
                    event=EventSpec(
                        type=EventType.storm,
                        minute=9,
                        severity=5,
                    ),
                    expected_revision=1,
                    request_id="old-version",
                ),
            )
        with pytest.raises(StaleExecution):
            await service.inject_event(
                state.id,
                EventRequest(
                    event=EventSpec(
                        type=EventType.storm,
                        minute=2,
                        severity=5,
                    ),
                    expected_revision=2,
                    request_id="past-event",
                ),
            )

    asyncio.run(run())


def test_execution_batch_rolls_back_events_and_state_together(tmp_path):
    store = SessionStore(tmp_path / "atomic.db")
    state = SessionState(
        mode=Mode.guided,
        data_mode=DataMode.synthetic,
        scenario=storm_scenario(seed=2081),
    )
    store.save_session(state)
    events = [
        ExecutionEvent(
            session_id=state.id,
            sim_minute=0,
            type="valid",
            payload={"value": 1},
        ),
        ExecutionEvent(
            session_id=state.id,
            sim_minute=0,
            type="invalid",
            payload={"not_json": object()},
        ),
    ]

    with pytest.raises(TypeError):
        store.commit_execution_batch(
            state,
            events,
            expected_previous_sequence=0,
        )

    assert store.replay(state.id) == []
    assert store.get_session(state.id).sequence == 0


def test_advance_api_returns_200_then_409_for_stale_revision(
    tmp_path,
    monkeypatch,
):
    async def run():
        service, store = _service(tmp_path)
        scenario = storm_scenario(seed=2082)
        plan, failure = solve_candidate(
            scenario,
            PlanProfile.balanced,
            timeout_seconds=1,
        )
        assert failure is None and plan is not None
        state = SessionState(
            mode=Mode.scenarios,
            data_mode=DataMode.synthetic,
            scenario=scenario,
            phase=Phase.executing,
            plans=[plan],
            selected_plan_id=plan.id,
            execution=create_execution_cursor(scenario, plan),
        )
        state.execution.status = "running"
        store.save_session(state)
        monkeypatch.setattr(api_module, "service", service)

        transport = httpx.ASGITransport(app=api_module.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            first = await client.post(
                f"/api/v2/sessions/{state.id}/execution/advance",
                json={
                    "target_minute": 0,
                    "expected_revision": 1,
                    "request_id": "api-first",
                },
            )
            assert first.status_code == 200
            assert first.json()["revision"] == 2

            stale = await client.post(
                f"/api/v2/sessions/{state.id}/execution/advance",
                json={
                    "target_minute": 1,
                    "expected_revision": 1,
                    "request_id": "api-stale",
                },
            )
            assert stale.status_code == 409
            assert "当前 2" in stale.json()["detail"]

    asyncio.run(run())
