import asyncio
from dataclasses import replace
from threading import Event

import pytest
from fastapi.testclient import TestClient

from stormshift_v2 import branch_preview, simulation
from stormshift_v2.branch_preview import BranchPreviewRequest, build_branch_preview, snapshot_digest
from stormshift_v2.config import settings
from stormshift_v2.execution import commit_landing
from stormshift_v2.optimizer import solve_candidate
from stormshift_v2.scenarios import sandbox_scenario
from stormshift_v2.schemas import (
    DataMode, ExecutionCursor, Mode, OperatorIntent, PlanProfile,
    ResourceReservation, SessionState,
)
from stormshift_v2.service import SessionService
from stormshift_v2.store import SessionStore
from stormshift_v2.stream import StreamManager


def fixture(tmp_path):
    scenario = sandbox_scenario(seed=2032, flight_count=4)
    scenario.weather.severity = 1
    for flight in scenario.flights:
        flight.fuel_margin_min = 160
    plans = []
    for profile in [PlanProfile.balanced, PlanProfile.passenger, PlanProfile.green]:
        plan, failure = solve_candidate(scenario, profile)
        assert plan is not None and failure is None
        plans.append(plan)
    state = SessionState(mode=Mode.sandbox, data_mode=DataMode.synthetic,
                         scenario=scenario, plans=plans)
    store = SessionStore(tmp_path / "preview.db")
    service = SessionService(config=replace(settings, llm_mode="off", data_dir=tmp_path),
                             store=store, streams=StreamManager())
    store.save_session(state)
    return service, store.get_session(state.id)


def request(state):
    return BranchPreviewRequest(plan_ids=[p.id for p in state.plans],
        expected_sequence=state.sequence,
        expected_updated_at=state.model_dump(mode="json")["updated_at"])


def test_persisted_read_only_cached_futures_and_common_worlds(tmp_path):
    service, state = fixture(tmp_path)
    before = service.store.get_session(state.id).model_dump(mode="json")
    events = service.store.replay(state.id)
    result = asyncio.run(service.preview_branches(state.id, request(state)))
    assert result["status"] == "fresh"
    assert result["runs"] == 50 and result["sample_world_index"] == 0
    assert len(result["branches"]) == 3
    assert all(b["status"] == "evaluated" for b in result["branches"])
    assert all(b["scope_flight_ids"] == result["scope_flight_ids"] for b in result["branches"])
    assert service.store.get_session(state.id).model_dump(mode="json") == before
    assert service.store.replay(state.id) == events
    assert len(service.store.list_sessions()) == 1
    again = asyncio.run(service.preview_branches(state.id, request(state)))
    assert again == result
    assert service.get_branch_preview(state.id, result["id"]) == result
    reordered = request(state)
    reordered.plan_ids.reverse()
    assert asyncio.run(service.preview_branches(state.id, reordered)) == result
    assert snapshot_digest(state) == result["source_digest"]


def test_mutating_returned_preview_cannot_mutate_live_state(tmp_path):
    service, state = fixture(tmp_path)
    result = asyncio.run(service.preview_branches(state.id, request(state)))
    result["branches"][0]["sample"][0]["gate"] = "WRONG"
    assert service.get_branch_preview(state.id, result["id"])["branches"][0]["sample"][0]["gate"] != "WRONG"
    assert all(a.gate != "WRONG" for p in service.require(state.id).plans for a in p.assignments)


def test_replanning_keeps_committed_prefix_and_does_not_recount_passengers(tmp_path):
    _, state = fixture(tmp_path)
    plan = state.plans[0]
    first = min(plan.assignments, key=lambda a: a.landing_minute)
    state.scenario.current_minute = first.landing_minute
    operation = commit_landing(state.scenario, first, plan_id=plan.id, revision=1)
    state.execution = ExecutionCursor(active_plan_id=plan.id, minute=first.landing_minute,
        committed_operations=[operation])
    result = build_branch_preview(state, [plan.id])
    assert result["branches"][0]["status"] == "evaluated"
    assert result["committed_operations"] == [operation.model_dump(mode="json")]
    assert first.flight_id in result["excluded_flight_ids"]
    assert first.flight_id not in result["scope_flight_ids"]
    assert result["scope_connection_passengers"] == sum(
        f.connections for f in state.scenario.flights if f.id != first.flight_id)
    assert all(row["flight_id"] != first.flight_id for row in result["branches"][0]["sample"])
    for row in result["branches"][0]["sample"]:
        for fixed in state.scenario.committed_reservations:
            if fixed.kind == "gate" and row["gate"] == fixed.resource_id:
                assert row["gate_out_minute"] <= fixed.start_minute or row["gate_in_minute"] >= fixed.end_minute
            if fixed.kind == "runway" and row["runway"] == fixed.resource_id:
                assert row["runway_release_minute"] <= fixed.start_minute or row["landing_minute"] >= fixed.end_minute
    state.scenario.committed_reservations = []
    with pytest.raises(ValueError, match="Incomplete committed"):
        build_branch_preview(state, [plan.id])


def test_unsafe_or_incomplete_candidate_has_no_fabricated_metrics(tmp_path):
    _, state = fixture(tmp_path)
    state.plans[0].assignments.pop()
    state.plans[1].assignments[0].gate = "A99"
    result = build_branch_preview(state, [p.id for p in state.plans])
    invalid = [b for b in result["branches"] if b["status"] == "invalid"]
    assert len(invalid) == 2
    assert all(b["report"] is None and not b["sample"] and b["failures"] for b in invalid)


def test_stale_request_and_pending_draft_are_rejected(tmp_path):
    service, state = fixture(tmp_path)
    old = request(state)
    state.scenario.weather.severity = 4
    service.store.save_session(state)
    with pytest.raises(ValueError, match="Snapshot changed"):
        asyncio.run(service.preview_branches(state.id, old))
    state = service.require(state.id)
    state.intent = OperatorIntent(raw_text="保护旅客", summary="待确认", needs_confirmation=True)
    service.store.save_session(state)
    with pytest.raises(ValueError, match="pending intent"):
        asyncio.run(service.preview_branches(state.id, request(service.require(state.id))))


def test_source_change_during_background_simulation_does_not_overwrite_it(tmp_path, monkeypatch):
    service, state = fixture(tmp_path)
    entered, resume = Event(), Event()
    original = branch_preview.build_branch_preview

    def blocked(*args):
        entered.set()
        assert resume.wait(10)
        return original(*args)

    monkeypatch.setattr(branch_preview, "build_branch_preview", blocked)
    async def exercise():
        task = asyncio.create_task(service.preview_branches(state.id, request(state)))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            # Acquiring this lock proves CPU simulation does not hold the live lock.
            async def change_live():
                async with service._lock(state.id):
                    changed = service.require(state.id)
                    changed.scenario.weather.severity = 5
                    changed.sequence += 1
                    service.store.save_session(changed)
            await asyncio.wait_for(change_live(), timeout=2)
        finally:
            resume.set()
        return await task

    result = asyncio.run(exercise())
    assert result["status"] == "stale"
    assert service.require(state.id).scenario.weather.severity == 5
    assert service.require(state.id).sequence == state.sequence + 1
    assert service.get_branch_preview(state.id, result["id"])["status"] == "stale"
    assert service.store.replay(state.id) == []


def test_http_validation_and_preview_expiry(tmp_path, monkeypatch):
    from stormshift_v2 import api
    service, state = fixture(tmp_path)
    monkeypatch.setattr(api, "service", service)
    client = TestClient(api.app)
    url = f"/api/v2/sessions/{state.id}/previews"
    response = client.post(url, json=request(state).model_dump())
    assert response.status_code == 200, response.text
    key = response.json()["id"]
    assert client.get(url + "/" + key).status_code == 200
    assert client.get(url + "/missing").status_code == 404
    payload = request(state).model_dump()
    payload["plan_ids"] = ["unknown"]
    assert client.post(url, json=payload).status_code == 409
    payload["plan_ids"] = [state.plans[0].id] * 4
    assert client.post(url, json=payload).status_code == 422
    assert client.get("/api/v2/sessions/missing/previews/x").status_code == 404


def test_calendar_gap_exact_boundaries_and_multiple_reservations():
    gap = simulation._reservation_start
    rows = [(10, 20), (30, 40)]
    assert gap(5, 5, rows) == 5
    assert gap(5, 6, rows) == 20
    assert gap(20, 11, rows) == 40
    assert gap(40, 22, rows) == 40


def test_queue_delay_rechecks_future_committed_gate_window(tmp_path, monkeypatch):
    _, state = fixture(tmp_path)
    scenario, plan = state.scenario, state.plans[0]
    # Two arriving flights share a gate. A occupies [0,22); B initially fits
    # before [30,50), but after A's queue it must wait until 50, not occupy [22,44).
    a, b = plan.assignments[:2]
    fixed_id = next(f.id for f in scenario.flights if f.id not in {a.flight_id, b.flight_id})
    plan.assignments = [a, b]
    a.landing_minute = b.landing_minute = 0
    a.taxi_minutes = b.taxi_minutes = 0
    b.gate = a.gate
    a.runway, b.runway = [r.id for r in scenario.runways[:2]]
    for r in scenario.runways:
        r.separation_min = 1
    for g in scenario.gates:
        g.available_minute = 0
    scenario.committed_reservations = [ResourceReservation(
        id="fixed", kind="gate", resource_id=a.gate, flight_id=fixed_id,
        start_minute=30, end_minute=50, committed_minute=0)]
    world = simulation.WorldRealization(0, 0, {r.id: 0 for r in scenario.runways},
        {f.id: simulation.FlightShocks(0, 0, 0, 0) for f in scenario.flights})
    monkeypatch.setattr(simulation, "realize_world", lambda *args: world)
    run = simulation._single_run(scenario, plan, simulation.build_passenger_graph(scenario),
                                 0, respect_committed=True)
    assert sorted(run.gate_in_times.values()) == [1, 50]
    assert sorted(run.gate_out_times.values()) == [23, 72]


def test_no_reservation_preview_matches_existing_world_results(tmp_path):
    _, state = fixture(tmp_path)
    plan = state.plans[0]
    old = simulation.simulate_candidate(state.scenario, plan, runs=10)
    new = simulation.simulate_candidate(state.scenario, plan, runs=10, respect_committed=True)
    assert new.raw_metrics == old.raw_metrics
    assert new.per_flight == old.per_flight
    assert new.method_version != old.method_version
