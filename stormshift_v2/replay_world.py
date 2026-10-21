"""Versioned visual evidence captured when an event is recorded.

The snapshot is display-only: it is hashed with the event payload, never an
input to approval or execution. Old events are not backfilled from final state.
"""
from copy import deepcopy
from .schemas import SessionState

WORLD_VERSION = "airport-world-v1"


def capture_world(state: SessionState, minute: float) -> dict:
    if minute != state.scenario.current_minute:
        return {"version": WORLD_VERSION, "status": "unavailable",
                "reason": "event_time_differs_from_observed_state",
                "session_id": state.id, "minute": minute}
    cursor = state.execution
    return {
        "version": WORLD_VERSION, "status": "captured",
        "session_id": state.id, "minute": minute,
        "world": {
            "id": state.id, "phase": state.phase.value,
            "scenario": {**state.scenario.model_dump(mode="json"), "events": []},
            "selected_plan_id": state.selected_plan_id,
            "plans": [{"id": plan.id,
                       "assignments": [row.model_dump(mode="json") for row in plan.assignments]}
                      for plan in state.plans],
            "execution": None if cursor is None else {
                "connection": cursor.connection.model_dump(mode="json") if cursor.connection else None,
                "scope_flight_ids": list(cursor.scope_flight_ids),
                "committed_operations": [row.model_dump(mode="json") for row in cursor.committed_operations],
            },
            "decision_lab": {"recourse": deepcopy(state.decision_lab.get("recourse"))},
        },
    }


def with_world_evidence(state: SessionState, payload: dict, minute: float) -> dict:
    # A caller cannot supply/override historical evidence or mutate buffered logs later.
    return {**deepcopy(payload), "world_snapshot": capture_world(state, minute)}
