"""The preview must encounter the same incidents as the live execution kernel."""
import asyncio

from stormshift_v2 import branch_rehearsal as rehearsal
from stormshift_v2.branch_preview import build_branch_preview
from stormshift_v2.execution import commit_landing
from stormshift_v2.online_execution import advance_state, guided_secondary_event
from stormshift_v2.schemas import EventSpec, EventType, ExecutionAdvanceRequest, ExecutionCursor, Mode
from test_v2_branch_preview import fixture


def outage(state, plan, minute=0, identifier="failure-1"):
    return EventSpec(id=identifier, type=EventType.runway_closed, minute=minute,
                     resource_id=plan.assignments[0].runway, duration_min=60)


def test_event_changes_plan_and_repair_remains_isolated(tmp_path):
    _, state = fixture(tmp_path)
    plan = state.plans[0]
    event = outage(state, plan)
    state.data_snapshot["scheduled_events"] = [event.model_dump(mode="json")]
    before = state.model_dump(mode="json")
    result = build_branch_preview(state, [plan.id])
    branch = result["branches"][0]
    trace = branch["rehearsal"]
    first, after = trace["first_decision_boundary"], trace["continuation"]
    assert first["minute"] == 0 and first["event_constraint_failures"]
    assert trace["solver_calls"] == 1
    assert after["repairs"][0]["status"] == "nominally_valid_not_approved"
    assert not any(p.startswith("FAIL") for p in after["repairs"][0]["after_constraint_proof"])
    assert after["status"] == "completed"
    assert after["outcome"]["hard_constraints_passed"]
    assert after["processed_event_ids"] == [event.id]
    assert trace["live_approval"] is False
    assert state.model_dump(mode="json") == before
    # Event-aware nominal execution is distinct from unchanged 50-world MC.
    without = state.model_copy(deep=True)
    without.data_snapshot.pop("scheduled_events")
    static = build_branch_preview(without, [plan.id])["branches"][0]
    assert branch["report"]["raw_metrics"] == static["report"]["raw_metrics"]
    assert branch["sample"] == static["sample"]


def test_first_boundary_matches_kernel_and_is_repeatable(tmp_path):
    _, state = fixture(tmp_path)
    plan = state.plans[0]
    event = outage(state, plan)
    result = rehearsal.rehearse_candidate(state, plan, [event])
    isolated = rehearsal._snapshot_for_plan(state, plan, [event])
    port = rehearsal._MemoryExecutionPort(isolated)
    asyncio.run(advance_state(port, isolated, ExecutionAdvanceRequest(
        target_minute=480, expected_revision=1, request_id="direct"),
        stop_before_incident_response=True))
    first = result["first_decision_boundary"]
    assert first["events"] == [row.model_dump(mode="json") for row in port._event_buffers[state.id]]
    assert first["processed_event_ids"] == isolated.execution.processed_event_ids
    assert first["nominal_constraint_proof"] == rehearsal._proof(isolated)
    assert rehearsal.rehearse_candidate(state, plan, [event]) == result


def test_guided_external_failure_does_not_depend_on_candidate(tmp_path):
    _, state = fixture(tmp_path)
    state.mode = Mode.guided
    events = [guided_secondary_event(state, plan).model_dump(mode="json") for plan in state.plans]
    assert all(event == events[0] for event in events)
    assert events[0]["payload"]["affected_gates"] == ["A3", "A4", "R1"]


def test_medical_flight_only_appears_in_branch_scope(tmp_path):
    _, state = fixture(tmp_path)
    event = EventSpec(id="medical-test", type=EventType.medical_diversion, minute=0,
                      resource_id="MEDTEST", payload={"fuel_margin_min": 90})
    result = rehearsal.rehearse_candidate(state, state.plans[0], [event])
    assert "MEDTEST" not in {flight.id for flight in state.scenario.flights}
    assert result["first_decision_boundary"]["event_constraint_failures"]
    final = result["continuation"]
    assert "MEDTEST" in final["flight_states"]
    assert final["status"] == "completed"
    assert any(row["flight_id"] == "MEDTEST" for row in final["committed_operations"])
    assert len(final["processed_event_ids"]) == len(set(final["processed_event_ids"])) == 1


def test_committed_operation_is_not_rescheduled_during_repair(tmp_path):
    _, state = fixture(tmp_path)
    plan = state.plans[0]
    first = min(plan.assignments, key=lambda row: row.landing_minute)
    state.scenario.current_minute = first.landing_minute
    fixed = commit_landing(state.scenario, first, plan_id=plan.id, revision=1)
    state.execution = ExecutionCursor(active_plan_id=plan.id, minute=first.landing_minute,
                                      committed_operations=[fixed])
    event = outage(state, plan, first.landing_minute, "after-prefix")
    result = rehearsal.rehearse_candidate(state, plan, [event])
    final = result["continuation"]
    operation = next(row for row in final["committed_operations"] if row["flight_id"] == first.flight_id)
    for key in ("flight_id", "runway", "gate", "landing_minute", "gate_start_minute",
                "gate_release_minute", "plan_id", "revision"):
        assert operation[key] == fixed.model_dump(mode="json")[key]
    for repair in final["repairs"]:
        assert first.flight_id not in {row["flight_id"] for row in repair["after_assignments"]}
    assert state.execution.committed_operations[0] == fixed


def test_infeasible_repair_stops_without_fabricated_success(tmp_path, monkeypatch):
    _, state = fixture(tmp_path)
    plan = state.plans[0]
    monkeypatch.setattr(rehearsal, "solve_candidate", lambda *args, **kwargs: (None, None))
    trace = rehearsal.rehearse_candidate(state, plan, [outage(state, plan)])
    assert trace["continuation"]["status"] == "needs_decision"
    assert trace["continuation"]["stopped_reason"] == "repair_infeasible"
    assert trace["continuation"]["outcome"] is None
    assert trace["solver_calls"] == 1
    assert not trace["continuation"]["repairs"][0]["after_assignments"]


def test_solver_budget_and_unhandled_events_are_explicit(tmp_path):
    _, state = fixture(tmp_path)
    events = [EventSpec(id=f"med-{i}", type=EventType.medical_diversion, minute=0,
                        resource_id=f"MED{i}", payload={"fuel_margin_min": 100})
              for i in range(4)]
    result = rehearsal.rehearse_candidate(state, state.plans[0], events)
    final = result["continuation"]
    assert result["solver_calls"] == 2
    assert final["stopped_reason"] == "repair_budget_exhausted"
    assert final["status"] == "needs_decision"
    assert final["processed_event_ids"] == ["med-0", "med-1", "med-2"]
    assert final["unprocessed_event_ids"] == ["med-3"]
    assert "MED2" in final["flight_states"]
    assert "MED2" in final["pending_flight_ids"]
    assert any("MED2" in line for line in final["nominal_constraint_proof"] if line.startswith("FAIL"))


def test_event_budget_does_not_silently_drop_remaining_incidents(tmp_path):
    _, state = fixture(tmp_path)
    events = [EventSpec(id=f"storm-{i:02}", type=EventType.storm, minute=0,
                        severity=1, duration_min=0) for i in range(33)]
    result = rehearsal.rehearse_candidate(state, state.plans[0], events)
    final = result["continuation"]
    assert result["solver_calls"] == 0
    assert final["stopped_reason"] == "event_budget_exhausted"
    assert final["status"] == "needs_decision"
    assert len(final["processed_event_ids"]) == 32
    assert final["unprocessed_event_ids"] == ["storm-32"]
