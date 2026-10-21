import json
import sqlite3

from stormshift_v2.schemas import DataMode, Mode, SessionState
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.store import GENESIS_HASH, SessionStore


def make_store(tmp_path):
    store = SessionStore(tmp_path / "integrity.db")
    state = SessionState(
        mode=Mode.guided,
        data_mode=DataMode.synthetic,
        scenario=storm_scenario(),
    )
    store.save_session(state)
    return store, state


def test_events_are_linked_and_verifiable(tmp_path):
    store, state = make_store(tmp_path)

    first = store.append_event(state.id, 0, "session_created", {"seed": 2026})
    second = store.append_event(state.id, 1, "storm", {"severity": 4})
    third = store.append_event(state.id, 2, "plan_generated", {"plans": ["p1"]})

    assert first.prev_hash == GENESIS_HASH
    assert len(first.event_hash) == 64
    assert second.prev_hash == first.event_hash
    assert third.prev_hash == second.event_hash

    proof = store.verify_event_chain(state.id)
    assert proof["status"] == "verified"
    assert proof["valid"] is True
    assert proof["event_count"] == 3
    assert proof["first_invalid_sequence"] is None
    assert proof["head_hash"] == third.event_hash
    assert all(link["valid"] for link in proof["links"])

    replay = store.replay(state.id)
    assert replay[2].event_hash == third.event_hash
    assert replay[2].prev_hash == second.event_hash


def test_payload_tampering_breaks_the_chain_at_exact_sequence(tmp_path):
    store, state = make_store(tmp_path)
    store.append_event(state.id, 0, "session_created", {"seed": 2026})
    store.append_event(state.id, 1, "plan_approved", {"plan_id": "p1"})
    store.append_event(state.id, 2, "execution_tick", {"flight_id": "TS101"})

    with sqlite3.connect(store.path) as db:
        db.execute(
            """
            UPDATE events SET payload_json=?
            WHERE session_id=? AND sequence=2
            """,
            (json.dumps({"plan_id": "tampered"}), state.id),
        )

    proof = store.verify_event_chain(state.id)
    assert proof["status"] == "broken"
    assert proof["valid"] is False
    assert proof["first_invalid_sequence"] == 2
    assert [link["valid"] for link in proof["links"]] == [True, False, True]


def test_legacy_unhashed_rows_are_backfilled_once(tmp_path):
    store, state = make_store(tmp_path)
    store.append_event(state.id, 0, "session_created", {"seed": 2026})
    store.append_event(state.id, 1, "operator_command", {"text": "protect passengers"})

    with sqlite3.connect(store.path) as db:
        db.execute(
            "UPDATE events SET prev_hash=NULL, event_hash=NULL WHERE session_id=?",
            (state.id,),
        )

    migrated = SessionStore(store.path)
    proof = migrated.verify_event_chain(state.id)
    assert proof["status"] == "verified"
    assert proof["event_count"] == 2
    assert proof["head_hash"] != GENESIS_HASH

