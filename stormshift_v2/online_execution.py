"""Online execution orchestration shared by the service and API tests."""

from __future__ import annotations

from typing import Any

from .execution import (
    ExecutionSafetyViolation,
    advance_fuel_clock,
    commit_landing,
    next_execution_minute,
    remaining_scope_complete,
)
from .connection_journey import connection_complete, holds_departure_gate, process_connection
from .policy import evaluate_policy_event
from .resource_calendar import (
    effective_turnaround_buffers,
    validate_resource_calendar,
)
from .schemas import (
    EventSpec,
    EventType,
    ExecutionAdvanceRequest,
    ExecutionEvent,
    FlightState,
    Phase,
    PlanCandidate,
    ResourceStatus,
    SessionState,
)
from .trusted_state import (
    build_trusted_operational_state,
)


def guided_secondary_event(
    state: SessionState,
    selected: PlanCandidate,
) -> EventSpec | None:
    if state.mode.value != "guided":
        return None
    if any(
        event.type == EventType.ground_vehicle_failure
        for event in state.scenario.events
    ):
        return None
    failure_minute = state.scenario.current_minute + 9
    affected_gates = ["A3", "A4", "R1"]
    # The external shock must not depend on the candidate being evaluated.
    return EventSpec(
        id=f"guided-secondary:{state.id}:{failure_minute}",
        type=EventType.ground_vehicle_failure,
        minute=failure_minute,
        resource_id="TUG-3",
        severity=3,
        duration_min=14,
        payload={
            "zone": "Taxiway C",
            "affected_gates": affected_gates,
            "story_beat": "secondary_failure",
            "scheduled_by": "guided_story",
        },
    )


def _active_plan(state: SessionState) -> PlanCandidate | None:
    if state.execution is None:
        return None
    return next(
        (
            plan
            for plan in state.plans
            if plan.id == state.execution.active_plan_id
        ),
        None,
    )


def _pending_view(
    plan: PlanCandidate,
    state: SessionState,
) -> PlanCandidate:
    committed_ids = {
        operation.flight_id
        for operation in (
            state.execution.committed_operations
            if state.execution is not None
            else []
        )
    }
    pending = [
        item.model_copy(deep=True)
        for item in (
            state.execution.pending_assignments
            if state.execution is not None
            else plan.assignments
        )
        if item.flight_id not in committed_ids
    ]
    return plan.model_copy(update={"assignments": pending}, deep=True)


def _sync_after_replan(state: SessionState) -> None:
    cursor = state.execution
    if cursor is None or state.selected_plan_id is None:
        return
    plan = next(
        (item for item in state.plans if item.id == state.selected_plan_id),
        None,
    )
    if plan is None:
        return
    committed_ids = {
        operation.flight_id for operation in cursor.committed_operations
    }
    cursor.active_plan_id = plan.id
    cursor.pending_assignments = sorted(
        (
            item.model_copy(deep=True)
            for item in plan.assignments
            if item.flight_id not in committed_ids
            and item.landing_minute >= state.scenario.current_minute
        ),
        key=lambda item: (item.landing_minute, item.flight_id),
    )
    for assignment in cursor.pending_assignments:
        if assignment.flight_id not in cursor.scope_flight_ids:
            cursor.scope_flight_ids.append(assignment.flight_id)


def _remove_diverted(
    service: Any,
    state: SessionState,
    diverted_ids: list[str],
) -> None:
    cursor = state.execution
    if cursor is None or not diverted_ids:
        return
    diverted = set(diverted_ids)
    cursor.pending_assignments = [
        item
        for item in cursor.pending_assignments
        if item.flight_id not in diverted
    ]
    for flight_id in sorted(diverted):
        service._record(
            state,
            "flight_diverted",
            {
                "flight_id": flight_id,
                "reason": "fuel_deadline_reached_before_next_safe_milestone",
                "status": "safe_diversion",
            },
        )


def _process_resource_releases(
    service: Any,
    state: SessionState,
) -> None:
    cursor = state.execution
    if cursor is None:
        return
    minute = state.scenario.current_minute
    flight_by_id = {item.id: item for item in state.scenario.flights}
    gate_by_id = {item.id: item for item in state.scenario.gates}

    for operation in cursor.committed_operations:
        if (
            not operation.runway_released
            and operation.runway_release_minute <= minute
        ):
            operation.runway_released = True
            service._record(
                state,
                "runway_released",
                {
                    "flight_id": operation.flight_id,
                    "runway": operation.runway,
                    "plan_id": operation.plan_id,
                },
            )
        if operation.stage == "at_gate" and operation.gate_release_minute <= minute:
            operation.stage = "released"
            flight = flight_by_id.get(operation.flight_id)
            if flight is not None:
                flight.state = FlightState.turnaround_complete
            gate = gate_by_id.get(operation.gate)
            if gate is not None and gate.occupied_by == operation.flight_id and not holds_departure_gate(state, operation.flight_id):
                gate.status = ResourceStatus.available
                gate.occupied_by = None
                gate.available_minute = minute
            service._record(
                state,
                "gate_released",
                {
                    "flight_id": operation.flight_id,
                    "gate": operation.gate,
                    "plan_id": operation.plan_id,
                    "status": "retained_for_connection" if holds_departure_gate(state, operation.flight_id) else "turnaround_complete",
                },
            )

    for runway in state.scenario.runways:
        if (
            runway.status == ResourceStatus.closed
            and runway.reopen_minute is not None
            and runway.reopen_minute <= minute
        ):
            reason = runway.reason
            runway.status = ResourceStatus.open
            runway.reopen_minute = None
            runway.reason = None
            service._record(
                state,
                EventType.runway_reopened.value,
                {
                    "resource_id": runway.id,
                    "status": "open",
                    "reason": reason or "scheduled inspection complete",
                },
            )

    occupied_operation_gates = {
        operation.gate
        for operation in cursor.committed_operations
        if operation.stage == "at_gate"
    }
    for gate in state.scenario.gates:
        if (
            gate.status in {ResourceStatus.blocked, ResourceStatus.occupied}
            and gate.available_minute <= minute
            and gate.id not in occupied_operation_gates
        ):
            previous = gate.status.value
            gate.status = ResourceStatus.available
            gate.occupied_by = None
            service._record(
                state,
                "gate_recovered",
                {
                    "resource_id": gate.id,
                    "previous_status": previous,
                    "status": "available",
                },
            )


def _process_gate_arrivals(
    service: Any,
    state: SessionState,
) -> None:
    cursor = state.execution
    if cursor is None:
        return
    minute = state.scenario.current_minute
    flight_by_id = {item.id: item for item in state.scenario.flights}
    gate_by_id = {item.id: item for item in state.scenario.gates}
    for operation in cursor.committed_operations:
        if operation.stage != "taxiing" or operation.gate_start_minute > minute:
            continue
        gate = gate_by_id.get(operation.gate)
        if gate is None:
            cursor.status = "blocked"
            state.phase = Phase.infeasible
            service._record(
                state,
                "execution_blocked",
                {
                    "flight_id": operation.flight_id,
                    "reason": "committed gate no longer exists",
                },
            )
            return
        if (
            gate.status == ResourceStatus.blocked
            and gate.available_minute > minute
        ):
            cursor.status = "blocked"
            state.phase = Phase.infeasible
            service._record(
                state,
                "execution_blocked",
                {
                    "flight_id": operation.flight_id,
                    "gate": gate.id,
                    "reason": "committed aircraft reached a gate under active outage",
                },
            )
            return
        operation.stage = "at_gate"
        flight = flight_by_id.get(operation.flight_id)
        if flight is not None:
            flight.state = FlightState.at_gate
        gate.status = ResourceStatus.occupied
        gate.occupied_by = operation.flight_id
        gate.available_minute = operation.gate_release_minute
        service._record(
            state,
            "gate_arrived",
            {
                "flight_id": operation.flight_id,
                "gate": operation.gate,
                "plan_id": operation.plan_id,
                "gate_release_minute": operation.gate_release_minute,
                "status": "at_gate",
            },
        )


def _process_landings(
    service: Any,
    state: SessionState,
) -> None:
    cursor = state.execution
    if cursor is None:
        return
    minute = state.scenario.current_minute
    plan = _active_plan(state)
    if plan is None:
        cursor.status = "blocked"
        state.phase = Phase.infeasible
        service._record(
            state,
            "execution_blocked",
            {"reason": "active plan is missing"},
        )
        return
    buffers = effective_turnaround_buffers(plan)
    due = sorted(
        (
            item
            for item in cursor.pending_assignments
            if item.landing_minute <= minute
        ),
        key=lambda item: (item.landing_minute, item.flight_id),
    )
    for assignment in due:
        flight = next(
            (
                item
                for item in state.scenario.flights
                if item.id == assignment.flight_id
            ),
            None,
        )
        if flight is None or flight.state == FlightState.diverted:
            cursor.pending_assignments.remove(assignment)
            continue
        try:
            operation = commit_landing(
                state.scenario,
                assignment,
                plan_id=cursor.active_plan_id,
                revision=cursor.revision,
                turnaround_buffer_min=int(
                    buffers.get(assignment.flight_id, 0)
                ),
            )
        except ExecutionSafetyViolation as exc:
            cursor.status = "blocked"
            state.phase = Phase.infeasible
            service._record(
                state,
                "execution_blocked",
                {
                    "flight_id": assignment.flight_id,
                    "plan_id": cursor.active_plan_id,
                    "reason": str(exc),
                },
            )
            return
        cursor.pending_assignments.remove(assignment)
        cursor.committed_operations.append(operation)
        service._record(
            state,
            EventType.execution_tick.value,
            {
                "flight_id": assignment.flight_id,
                "runway": assignment.runway,
                "gate": assignment.gate,
                "status": "landing_committed",
                "plan_id": cursor.active_plan_id,
                "schedule_revision": cursor.revision,
                "runway_release_minute": operation.runway_release_minute,
                "gate_start_minute": operation.gate_start_minute,
                "gate_release_minute": operation.gate_release_minute,
            },
        )


async def _process_incident(
    service: Any,
    state: SessionState,
    event: EventSpec,
    *,
    stop_before_response: bool = False,
) -> str:
    cursor = state.execution
    assert cursor is not None
    previous_trusted = state.decision_lab.get(
        "trusted_state"
    ) or build_trusted_operational_state(
        state.scenario,
        state.data_mode,
    )
    policy_decision = evaluate_policy_event(
        state.decision_lab.get("policy"),
        event,
    )
    event.payload["policy_decision"] = policy_decision
    state.decision_lab["policy_runtime"] = {
        "event_id": event.id,
        **policy_decision,
    }
    service._apply_event(state, event)
    cursor.processed_event_ids.append(event.id)
    if event.type == EventType.medical_diversion:
        flight_id = event.resource_id or str(
            event.payload.get("flight_id", "MED901")
        )
        if flight_id not in cursor.scope_flight_ids:
            cursor.scope_flight_ids.append(flight_id)

    service._record(
        state,
        event.type.value,
        event.model_dump(mode="json"),
        event.minute,
    )
    service._invalidate_observations_for_event(state, event)
    trusted = build_trusted_operational_state(
        state.scenario,
        state.data_mode,
        previous=previous_trusted,
        event=event,
    )
    state.decision_lab["trusted_state"] = trusted
    service._record(
        state,
        "trusted_state_reconciled",
        {
            "trigger": event.id,
            "digest": trusted["digest"],
            "changed_facts": len(trusted["changed_facts"]),
            "invalidated_constraints": len(
                trusted["invalidated_constraints"]
            ),
            "summary": trusted["summary"],
        },
        event.minute,
    )

    if stop_before_response:
        cursor.status = "blocked"
        return "incident_preview_boundary"

    plan = _active_plan(state)
    if (
        plan is None
        or not policy_decision.get("matched")
        or not policy_decision.get("approved_envelope")
    ):
        if policy_decision.get("action") == "continue_and_monitor":
            state.phase = Phase.monitoring
            return "incident_observed"
        cursor.status = "blocked"
        state.phase = Phase.awaiting_approval
        service._record(
            state,
            "execution_blocked",
            {
                "trigger": event.id,
                "reason": "incident is outside the approved recourse envelope",
                "policy_decision": policy_decision,
            },
            event.minute,
        )
        return "incident_requires_approval"

    runtime_plan = _pending_view(plan, state)
    await service._automatic_policy_replan(
        state,
        runtime_plan,
        event,
        policy_decision,
        trusted,
    )
    if state.phase == Phase.monitoring and state.selected_plan_id is not None:
        _sync_after_replan(state)
        cursor.status = "running"
        return "incident_replanned"
    cursor.status = "blocked"
    return (
        "incident_requires_approval"
        if state.phase == Phase.awaiting_approval
        else "incident_replan_failed"
    )


def _execution_outcome(state: SessionState) -> dict[str, Any]:
    cursor = state.execution
    assert cursor is not None
    released = {
        item.flight_id
        for item in cursor.committed_operations
        if item.stage == "released"
    }
    state_by_id = {item.id: item.state for item in state.scenario.flights}
    diverted = {
        flight_id
        for flight_id in cursor.scope_flight_ids
        if state_by_id.get(flight_id) == FlightState.diverted
    }
    calendar_failures = validate_resource_calendar(
        state.scenario,
        state.scenario.committed_reservations,
    )
    return {
        "scope_flights": len(cursor.scope_flight_ids),
        "turnaround_complete": len(released),
        "safe_diversions": len(diverted),
        "pending": len(cursor.pending_assignments),
        "committed_operations": len(cursor.committed_operations),
        "hard_constraints_passed": not calendar_failures,
        "hard_constraint_failures": calendar_failures,
        "source": "committed execution ledger; not simulation forecast",
        "connecting_departure": cursor.connection.model_dump(mode="json") if cursor.connection else None,
    }


async def advance_state(
    service: Any,
    state: SessionState,
    request: ExecutionAdvanceRequest,
    *,
    stop_before_incident_response: bool = False,
) -> tuple[list[ExecutionEvent], int | None, str | None]:
    """Advance through committed milestones, stopping after any incident."""
    cursor = state.execution
    if cursor is None:
        raise ValueError("session has no approved execution cursor")
    target = request.target_minute
    stopped_reason: str | None = None
    cursor.status = "running"

    while cursor.status == "running":
        next_minute = next_execution_minute(state.scenario, cursor)
        if next_minute is None or next_minute > target:
            step_minute = target
        else:
            step_minute = next_minute
        if step_minute < state.scenario.current_minute:
            raise RuntimeError("execution milestone moved backwards")

        fuel_clock, diverted = advance_fuel_clock(
            state.scenario,
            step_minute,
        )
        cursor.minute = state.scenario.current_minute
        if fuel_clock is not None:
            service._record(
                state,
                "clock_advanced",
                {"fuel_clock": fuel_clock},
                step_minute,
            )
        _remove_diverted(service, state, diverted)
        _process_resource_releases(service, state)

        due_incidents = sorted(
            (
                event
                for event in cursor.scheduled_events
                if event.id not in cursor.processed_event_ids
                and event.minute <= state.scenario.current_minute
            ),
            key=lambda event: (event.minute, event.id),
        )
        if due_incidents:
            stopped_reason = await _process_incident(
                service,
                state,
                due_incidents[0],
                stop_before_response=stop_before_incident_response,
            )
            break

        _process_gate_arrivals(service, state)
        if cursor.status == "blocked":
            stopped_reason = "execution_blocked"
            break
        _process_landings(service, state)
        if cursor.status == "blocked":
            stopped_reason = "execution_blocked"
            break

        process_connection(service, state)
        if cursor.status == "blocked":
            stopped_reason = "connection_blocked"
            break

        if remaining_scope_complete(state.scenario, cursor) and connection_complete(state):
            cursor.status = "completed"
            state.phase = Phase.recovered
            outcome = _execution_outcome(state)
            state.decision_lab["execution_outcome"] = outcome
            service._record(
                state,
                EventType.recovered.value,
                {
                    "selected_plan_id": state.selected_plan_id,
                    "status": "operations_stable",
                    "execution_outcome": outcome,
                },
            )
            stopped_reason = "recovered"
            break

        if next_minute is None or next_minute > target:
            state.phase = Phase.monitoring
            break
        if state.scenario.current_minute >= target:
            state.phase = Phase.monitoring
            break

    cursor.minute = state.scenario.current_minute
    cursor.last_stopped_reason = stopped_reason
    next_minute = next_execution_minute(state.scenario, cursor)
    return list(service._event_buffers[state.id]), next_minute, stopped_reason
