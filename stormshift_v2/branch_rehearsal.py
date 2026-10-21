"""Isolated nominal event rehearsal using the live execution kernel.

An opt-in bounded feedback controller can choose solver tools in a paired branch.
Neither branch executes the live approval policy or writes to the live session.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy

from .config import settings
from .repair_controller import FEEDBACK_VERSION, feedback_packet, propose_repair
from .execution import create_execution_cursor
from .intent_compiler import confirmed_intent
from .online_execution import advance_state, guided_secondary_event
from .optimizer import solve_candidate, validate_assignments
from .resource_calendar import effective_turnaround_buffers
from .schemas import EventSpec, ExecutionAdvanceRequest, ExecutionEvent, Phase, SessionState

REHEARSAL_VERSION = "live-kernel-event-rehearsal-v2"


class _MemoryExecutionPort:
    def __init__(self, state):
        self._event_buffers = {state.id: []}

    def _record(self, state, event_type, payload, sim_minute=None):
        event = ExecutionEvent(
            sequence=state.sequence + 1, session_id=state.id,
            sim_minute=state.scenario.current_minute if sim_minute is None else sim_minute,
            type=event_type, payload=deepcopy(payload), created_at=state.created_at,
        )
        state.sequence += 1
        self._event_buffers[state.id].append(event)
        return event

    def _apply_event(self, state, event):
        # Reuse the actual reducer; it touches only its supplied state.
        from .service import SessionService
        SessionService._apply_event(self, state, event)

    def _invalidate_observations_for_event(self, state, event):
        from .service import SessionService
        return SessionService._invalidate_observations_for_event(self, state, event)


def rehearsal_events(state, candidate):
    if state.execution is not None:
        return [event.model_copy(deep=True) for event in state.execution.scheduled_events]
    events = [EventSpec.model_validate(row) for row in state.data_snapshot.get("scheduled_events", [])]
    secondary = guided_secondary_event(state, candidate)
    if secondary and secondary.id not in {event.id for event in events}:
        events.append(secondary)
    return sorted(events, key=lambda event: (event.minute, event.id))


def _snapshot_for_plan(source, candidate, events):
    state = source.model_copy(deep=True)
    plan = candidate.model_copy(deep=True)
    fixed = {row.flight_id for row in state.scenario.committed_reservations}
    plan.assignments = [row for row in plan.assignments if row.flight_id not in fixed]
    state.plans = [plan]
    state.selected_plan_id = plan.id
    state.phase = Phase.executing
    state.decision_lab.pop("execution_outcome", None)
    if state.execution is None:
        state.execution = create_execution_cursor(state.scenario, plan, scheduled_events=events)
    else:
        cursor = state.execution
        cursor.active_plan_id = plan.id
        cursor.pending_assignments = plan.assignments
        cursor.scheduled_events = [event.model_copy(deep=True) for event in events]
        cursor.scope_flight_ids = sorted(set(cursor.scope_flight_ids) | fixed |
                                        {row.flight_id for row in plan.assignments})
        cursor.request_receipts = {}
    state.execution.status = "running"
    return state


def _proof(state):
    plan = next(p for p in state.plans if p.id == state.execution.active_plan_id)
    return validate_assignments(
        state.scenario, state.execution.pending_assignments, confirmed_intent(state),
        turnaround_buffer_by_flight=effective_turnaround_buffers(plan),
    )


def _export(state, port, reason, repairs, fixed_ids):
    cursor = state.execution
    committed = {row.flight_id for row in cursor.committed_operations}
    diverted = {row.id for row in state.scenario.flights if row.state.value == "diverted"}
    return {
        "status": "completed" if cursor.status == "completed" else "needs_decision",
        "minute": state.scenario.current_minute, "stopped_reason": reason,
        "events": [row.model_dump(mode="json") for row in port._event_buffers[state.id]],
        "committed_operations": [row.model_dump(mode="json") for row in cursor.committed_operations],
        "initial_committed_flight_ids": fixed_ids,
        "pending_flight_ids": sorted(set(cursor.scope_flight_ids) - committed - diverted),
        "flight_states": {row.id: row.state.value for row in state.scenario.flights},
        "repairs": deepcopy(repairs), "outcome": state.decision_lab.get("execution_outcome"),
        "processed_event_ids": list(cursor.processed_event_ids),
        "unprocessed_event_ids": [row.id for row in cursor.scheduled_events
                                  if row.id not in cursor.processed_event_ids],
        "nominal_constraint_proof": _proof(state),
    }


async def _rehearse(source, candidate, events, *, feedback=False, config=settings):
    state = _snapshot_for_plan(source, candidate, events)
    port = _MemoryExecutionPort(state)
    fixed_ids = sorted(row.flight_id for row in state.execution.committed_operations)
    repairs, baseline = [], None
    reason = None
    proposal_trace = []
    for step in range(32):
        _, _, reason = await advance_state(
            port, state, ExecutionAdvanceRequest(
                target_minute=480, expected_revision=state.execution.revision,
                request_id=f"preview-step-{step}"),
            stop_before_incident_response=True,
        )
        if state.execution.status == "completed" or reason != "incident_preview_boundary":
            break
        proof = _proof(state)
        failures = [line for line in proof if line.startswith("FAIL")]
        if baseline is None:
            baseline = _export(state, port, reason, repairs, fixed_ids)
            baseline["event_constraint_failures"] = failures
        if not failures:
            port._record(state, "preview_plan_still_valid", {
                "constraint_proof": proof, "approval": False,
            })
            continue
        if len(repairs) >= 2:
            reason = "repair_budget_exhausted"
            break
        accepted = False
        while len(repairs) < 2:
            active_plan = next(p for p in state.plans if p.id == state.execution.active_plan_id)
            buffers = effective_turnaround_buffers(active_plan)
            before = [row.model_dump(mode="json") for row in state.execution.pending_assignments]
            frozen = []
            proposal = None
            if feedback:
                packet = feedback_packet(state, active_plan, failures, repairs)
                proposal, provenance = await propose_repair(packet, config)
                proposal_trace.append({
                    **provenance, "minute": state.scenario.current_minute,
                    "proposal": proposal.model_dump(mode="json"),
                    "evidence": packet["evidence"],
                    "eligible_preserve_ids": packet["eligible_preserve_ids"],
                })
                if proposal.tool == "request_operator":
                    reason = "agent_requests_operator"
                    break
                frozen = [row for row in state.execution.pending_assignments
                          if row.flight_id in proposal.preserve_flight_ids]
            repaired, failure = solve_candidate(
                state.scenario, candidate.profile, confirmed_intent(state),
                frozen_assignments=frozen, turnaround_buffer_by_flight=buffers)
            record = {
                "minute": state.scenario.current_minute,
                "trigger": state.execution.processed_event_ids[-1],
                "before_failures": failures, "before_assignments": before,
                "method": "feedback_tool_selection" if feedback else "deterministic_cp_sat_baseline",
                "proposal": proposal.model_dump(mode="json") if proposal else {},
                "status": "infeasible", "after_assignments": [], "after_constraint_proof": [],
            }
            repairs.append(record)
            if repaired is None:
                # Generic diagnostics do not explain additional frozen-row locks.
                record["diagnostic"] = {
                    "optimizer_report": failure.model_dump(mode="json") if failure else None,
                    "optional_locks": [row.flight_id for row in frozen],
                    "lock_conflict_core": None,
                }
                reason = "repair_infeasible"
                if feedback:
                    continue
                break
            repaired.id = f"{candidate.id}:hypothetical-repair:{len(repairs)}"
            if buffers:
                repaired.risk_shield = {**(repaired.risk_shield or {}),
                    "adaptive_envelope": {"applies_to_candidate": True,
                                          "turnaround_buffer_by_flight": buffers}}
            checked = validate_assignments(
                state.scenario, repaired.assignments, confirmed_intent(state),
                turnaround_buffer_by_flight=buffers,
            )
            by_id = {row.flight_id: row for row in repaired.assignments}
            for fixed in frozen:
                if by_id.get(fixed.flight_id) != fixed:
                    checked.append("FAIL 修正未遵守保留安排: " + fixed.flight_id)
            record["after_constraint_proof"] = checked
            if not repaired.feasible or any(line.startswith("FAIL") for line in checked):
                record["status"] = "rejected"
                reason = "repair_invalid"
                if feedback:
                    continue
                break
            record["status"] = "nominally_valid_not_approved"
            record["after_assignments"] = [row.model_dump(mode="json") for row in repaired.assignments]
            state.plans.append(repaired)
            state.selected_plan_id = repaired.id
            state.execution.active_plan_id = repaired.id
            state.execution.pending_assignments = sorted(
                repaired.assignments, key=lambda row: (row.landing_minute, row.flight_id))
            state.execution.revision += 1
            state.execution.scope_flight_ids = sorted(set(state.execution.scope_flight_ids) |
                                                     {row.flight_id for row in repaired.assignments})
            port._record(state, "preview_hypothetical_repair", record)
            accepted = True
            break
        if not accepted:
            break
    else:
        reason = "event_budget_exhausted"
    continuation = _export(state, port, reason, repairs, fixed_ids)
    return {
        "method_version": REHEARSAL_VERSION,
        "controller_version": FEEDBACK_VERSION if feedback else "ordinary_cp_sat",
        "proposal_trace": proposal_trace,
        "model_calls": sum(row["model_calls"] for row in proposal_trace),
        "solver_call_budget": 2,
        "solver_timeout_parameter_seconds": .45,
        "semantics": "nominal_live_kernel_not_monte_carlo",
        "scheduled_events": [row.model_dump(mode="json") for row in events],
        "first_decision_boundary": baseline,
        "continuation": continuation, "solver_calls": len(repairs), "live_approval": False,
        "limitations": [("bounded_tool_controller_not_unrestricted_planner" if feedback else
                         "deterministic_repair_not_llm_feedback"), "nominal_timing_not_monte_carlo",
                        "live_approval_policy_not_executed"],
    }


def rehearse_candidate(source: SessionState, candidate, events, *, feedback=False, config=settings):
    # build_branch_preview runs in a worker thread, never on the HTTP event loop.
    return asyncio.run(_rehearse(source, candidate, events, feedback=feedback, config=config))
