from dataclasses import replace

from stormshift_v2.config import settings
from stormshift_v2.replay_world import capture_world, with_world_evidence
from stormshift_v2.schemas import DataMode, Mode, SessionState, ResourceStatus
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.service import SessionService
from stormshift_v2.store import SessionStore
from stormshift_v2.stream import StreamManager


def fixture(tmp_path):
    state = SessionState(mode=Mode.guided, data_mode=DataMode.synthetic, scenario=storm_scenario())
    store = SessionStore(tmp_path / "sessions.db")
    store.save_session(state)
    service = SessionService(config=replace(settings, data_dir=tmp_path, llm_mode="off"),
                             store=store, streams=StreamManager())
    return service, store, state


def test_snapshot_is_historical_and_covered_by_event_integrity(tmp_path):
    service, store, state = fixture(tmp_path)
    first = service._record(state, "session_created", {})
    state.scenario.current_minute = 24
    state.scenario.runways[1].status = ResourceStatus.open
    state.scenario.weather.severity = 1
    second = service._record(state, "runway_reopened", {"resource_id": "07R"})
    restored = store.replay(state.id)
    assert restored[0].payload["world_snapshot"]["world"]["scenario"]["runways"][1]["status"] == "closed"
    assert restored[0].payload["world_snapshot"]["world"]["scenario"]["weather"]["severity"] == 4
    assert restored[1].payload["world_snapshot"]["world"]["scenario"]["runways"][1]["status"] == "open"
    assert restored[1].payload["world_snapshot"]["world"]["scenario"]["weather"]["severity"] == 1
    assert first.sequence < second.sequence
    assert store.verify_event_chain(state.id)["status"] == "verified"


def test_buffered_capture_cannot_alias_state_or_caller_payload(tmp_path):
    service, _, state = fixture(tmp_path)
    service._event_buffers[state.id] = []
    payload = {"nested": {"value": 1}, "world_snapshot": {"version": "forged"}}
    event = service._record(state, "ground_vehicle_failure", payload)
    payload["nested"]["value"] = 2
    state.scenario.weather.severity = 0
    assert event.payload["nested"]["value"] == 1
    assert event.payload["world_snapshot"]["version"] == "airport-world-v1"
    assert event.payload["world_snapshot"]["world"]["scenario"]["weather"]["severity"] == 4


def test_missing_historical_evidence_is_not_backfilled(tmp_path):
    service, store, state = fixture(tmp_path)
    legacy = store.append_event(state.id, 0, "session_created", {})
    state.sequence = legacy.sequence
    service._record(state, "recovered", {})
    assert "world_snapshot" not in store.replay(state.id)[0].payload
    snapshot = capture_world(state, 10)
    assert snapshot["status"] == "unavailable"
    assert "world" not in snapshot


def test_same_minute_captures_do_not_collapse_different_event_states(tmp_path):
    service, store, state = fixture(tmp_path)
    before = service._record(state, "ground_vehicle_failure", {})
    state.scenario.gates[2].status = ResourceStatus.blocked
    after = service._record(state, "gate_outage", {})
    assert before.sim_minute == after.sim_minute
    assert before.sequence < after.sequence
    assert before.payload["world_snapshot"]["world"]["scenario"]["gates"][2]["status"] != "blocked"
    assert after.payload["world_snapshot"]["world"]["scenario"]["gates"][2]["status"] == "blocked"
    assert store.replay(state.id)[0].payload == before.payload


def test_world_evidence_excludes_future_events_and_heavy_reports(tmp_path):
    _, _, state = fixture(tmp_path)
    state.decision_lab["secret_future_analysis"] = {"candidate": "not-yet-revealed"}
    payload = with_world_evidence(state, {}, 0)
    world = payload["world_snapshot"]["world"]
    assert world["scenario"]["events"] == []
    assert "secret_future_analysis" not in world["decision_lab"]
    assert world["execution"] is None
    assert set(world) == {"id", "phase", "scenario", "selected_plan_id", "plans", "execution", "decision_lab"}
