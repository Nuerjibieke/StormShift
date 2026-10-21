"""Deterministic online execution primitives for approved recovery plans."""

from __future__ import annotations

from collections.abc import Iterable

from .optimizer import validate_assignments
from .resource_calendar import assignment_reservations
from .schemas import (
    CommittedOperation,
    EventSpec,
    ExecutionCursor,
    FlightAssignment,
    FlightState,
    PlanCandidate,
    ScenarioSpec,
)


ACTIVE_FLIGHT_STATES = {
    FlightState.inbound,
    FlightState.approach,
    FlightState.holding,
}


class ExecutionSafetyViolation(ValueError):
    """Raised before an unsafe assignment can become an operational fact."""


def create_execution_cursor(
    scenario: ScenarioSpec,
    plan: PlanCandidate,
    *,
    scheduled_events: Iterable[EventSpec] = (),
) -> ExecutionCursor:
    pending = sorted(
        (item.model_copy(deep=True) for item in plan.assignments),
        key=lambda item: (item.landing_minute, item.flight_id),
    )
    queued = sorted(
        (
            item.model_copy(deep=True)
            for item in scheduled_events
            if item.minute >= scenario.current_minute
        ),
        key=lambda item: (item.minute, item.id),
    )
    return ExecutionCursor(
        active_plan_id=plan.id,
        minute=scenario.current_minute,
        status="ready",
        scope_flight_ids=[item.flight_id for item in pending],
        pending_assignments=pending,
        scheduled_events=queued,
    )


def advance_fuel_clock(
    scenario: ScenarioSpec,
    to_minute: int,
) -> tuple[dict[str, object] | None, list[str]]:
    """Move the authoritative clock without moving absolute fuel deadlines."""
    if to_minute < scenario.current_minute:
        raise ValueError("simulation time cannot move backwards")
    elapsed = to_minute - scenario.current_minute
    if elapsed == 0:
        return None, []

    from_minute = scenario.current_minute
    updates: list[dict[str, int | str]] = []
    diverted: list[str] = []
    for flight in scenario.flights:
        if flight.state not in ACTIVE_FLIGHT_STATES:
            continue
        before = flight.fuel_margin_min
        after = max(0, before - elapsed)
        flight.fuel_margin_min = after
        expired = before < elapsed
        if expired:
            flight.state = FlightState.diverted
            diverted.append(flight.id)
        updates.append({
            "flight_id": flight.id,
            "before_min": before,
            "after_min": after,
            "absolute_deadline_min": from_minute + before,
            "status": "diverted" if expired else "preserved",
        })
    scenario.current_minute = to_minute
    return {
        "from_minute": from_minute,
        "to_minute": to_minute,
        "elapsed_min": elapsed,
        "updated_flights": len(updates),
        "minimum_remaining_min": min(
            (int(item["after_min"]) for item in updates),
            default=None,
        ),
        "critical_flight_ids": [
            str(item["flight_id"])
            for item in updates
            if int(item["after_min"]) <= 8
        ],
        "expired_flight_ids": diverted,
        "deadline_invariant": "deadlines preserved; overdue flights diverted",
        "updates": updates,
    }, diverted


def commit_landing(
    scenario: ScenarioSpec,
    assignment: FlightAssignment,
    *,
    plan_id: str,
    revision: int,
    turnaround_buffer_min: int = 0,
) -> CommittedOperation:
    """Validate and commit one landing plus its future gate reservation."""
    proof = validate_assignments(
        scenario,
        [assignment],
        require_complete=False,
        turnaround_buffer_by_flight={
            assignment.flight_id: max(0, turnaround_buffer_min)
        },
    )
    failures = [item for item in proof if item.startswith("FAIL")]
    if failures:
        raise ExecutionSafetyViolation("; ".join(failures))

    reservations = assignment_reservations(
        scenario,
        [assignment],
        source_plan_id=plan_id,
        revision=revision,
        committed_minute=scenario.current_minute,
        turnaround_buffer_by_flight={
            assignment.flight_id: max(0, turnaround_buffer_min)
        },
    )
    scenario.committed_reservations.extend(reservations)
    runway = next(item for item in scenario.runways if item.id == assignment.runway)
    gate = next(item for item in scenario.gates if item.id == assignment.gate)
    gate_start = assignment.landing_minute + gate.taxi_minutes
    gate_release = (
        next(
            item.end_minute
            for item in reservations
            if item.kind == "gate"
        )
    )
    flight = next(
        item for item in scenario.flights if item.id == assignment.flight_id
    )
    flight.state = FlightState.taxiing
    return CommittedOperation(
        flight_id=assignment.flight_id,
        plan_id=plan_id,
        revision=revision,
        landing_minute=assignment.landing_minute,
        runway=assignment.runway,
        runway_release_minute=(
            assignment.landing_minute + runway.separation_min
        ),
        gate=assignment.gate,
        gate_start_minute=gate_start,
        gate_release_minute=gate_release,
        stage="taxiing",
    )


def next_execution_minute(
    scenario: ScenarioSpec,
    cursor: ExecutionCursor,
) -> int | None:
    """Return the next not-yet-committed operational milestone."""
    current = scenario.current_minute
    candidates: list[int] = []

    candidates.extend(
        item.landing_minute
        for item in cursor.pending_assignments
        if item.landing_minute >= current
    )
    candidates.extend(
        item.minute
        for item in cursor.scheduled_events
        if item.id not in cursor.processed_event_ids
        and item.minute >= current
    )
    for runway in scenario.runways:
        if (
            runway.status.value == "closed"
            and runway.reopen_minute is not None
            and runway.reopen_minute >= current
        ):
            candidates.append(runway.reopen_minute)
    # Committed operations own their release times. A planner's stale availability
    # hint must never schedule the same occupied stand over and over.
    managed_gates = {
        operation.gate for operation in cursor.committed_operations
        if operation.stage == "at_gate"
    }
    connection = cursor.connection
    if connection and connection.gate and connection.stage in {"turnaround", "boarding", "pushback"}:
        managed_gates.add(connection.gate)
    for gate in scenario.gates:
        if (
            gate.id not in managed_gates
            and gate.status.value in {"blocked", "occupied"}
            and gate.available_minute >= current
        ):
            candidates.append(gate.available_minute)
    for operation in cursor.committed_operations:
        if (
            not operation.runway_released
            and operation.runway_release_minute >= current
        ):
            candidates.append(operation.runway_release_minute)
        if (
            operation.stage == "taxiing"
            and operation.gate_start_minute >= current
        ):
            candidates.append(operation.gate_start_minute)
        if (
            operation.stage == "at_gate"
            and operation.gate_release_minute >= current
        ):
            candidates.append(operation.gate_release_minute)
    if cursor.connection and cursor.connection.next_minute is not None and cursor.connection.next_minute >= current:
        candidates.append(cursor.connection.next_minute)
    return min(candidates) if candidates else None


def remaining_scope_complete(
    scenario: ScenarioSpec,
    cursor: ExecutionCursor,
) -> bool:
    if cursor.pending_assignments:
        return False
    operation_by_flight = {
        item.flight_id: item for item in cursor.committed_operations
    }
    state_by_flight = {item.id: item.state for item in scenario.flights}
    return all(
        (
            flight_id in operation_by_flight
            and operation_by_flight[flight_id].stage == "released"
        )
        or state_by_flight.get(flight_id) == FlightState.diverted
        for flight_id in cursor.scope_flight_ids
    )
