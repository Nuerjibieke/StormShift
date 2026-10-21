"""A changed runway assumption is isolated from live facts and Monte Carlo."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from stormshift_v2.branch_preview import BranchPreviewRequest, build_branch_preview, preview_id
from stormshift_v2.execution import commit_landing
from stormshift_v2.optimizer import solve_candidate
from stormshift_v2.runway_hypothesis import RunwayDelayHypothesis, delay_notice
from stormshift_v2.schemas import EventSpec, EventType, ExecutionCursor, ResourceStatus
from test_v2_branch_preview import fixture, request


def closed_fixture(tmp_path):
    service, state = fixture(tmp_path)
    target, other = state.scenario.runways[:2]
    target.status = other.status = ResourceStatus.closed
    target.reopen_minute, other.reopen_minute = 6, 240
    state.plans = [solve_candidate(state.scenario, p.profile)[0] for p in state.plans]
    assert all(p is not None and p.assignments for p in state.plans)
    assert all(a.runway == target.id for p in state.plans for a in p.assignments)
    service.store.save_session(state)
    return service, service.require(state.id), RunwayDelayHypothesis(runway_id=target.id, delay_min=10)


def test_only_nominal_event_branch_changes_and_source_stays_intact(tmp_path):
    _, state, assumption = closed_fixture(tmp_path)
    before = state.model_dump(mode="json")
    base = build_branch_preview(state, [state.plans[0].id])
    result = build_branch_preview(state, [state.plans[0].id], runway_delay=assumption)
    branch = result["branches"][0]
    meta = result["runway_delay_hypothesis"]
    assert (meta["notice_minute"], meta["original_reopen_minute"], meta["hypothetical_reopen_minute"]) == (0, 6, 16)
    assert meta["scope"] == "nominal_event_rehearsal_only"
    assert branch["report"] == base["branches"][0]["report"]
    assert branch["sample"] == base["branches"][0]["sample"]
    assert branch["rehearsal"] == base["branches"][0]["rehearsal"]
    trial = branch["hypothesis_rehearsal"]
    assert trial["live_approval"] is False and trial["model_calls"] == 0
    assert trial["first_decision_boundary"]["minute"] == 0
    assert trial["first_decision_boundary"]["event_constraint_failures"]
    assert trial["continuation"]["status"] == "completed"
    assert trial["solver_calls"] == 1
    assert trial["continuation"]["outcome"]["hard_constraints_passed"]
    assert all(op["landing_minute"] >= 16 for op in trial["continuation"]["committed_operations"])
    comparison = branch["hypothesis_comparison"]
    assert comparison["baseline"]["solver_calls"] == 0
    assert comparison["hypothesis"]["solver_calls"] == 1
    assert comparison["hypothesis"]["completed_minute"] > comparison["baseline"]["completed_minute"]
    assert comparison["hypothesis"]["changed_pending_flight_ids"]
    assert state.model_dump(mode="json") == before
    assert result["source_digest"] == base["source_digest"] and result["id"] != base["id"]
    assert result == build_branch_preview(state, [state.plans[0].id], runway_delay=assumption)


def test_shared_notice_applies_to_all_candidates_with_same_scope(tmp_path):
    _, state, assumption = closed_fixture(tmp_path)
    state.data_snapshot["scheduled_events"] = [EventSpec(
        id="same-storm", type=EventType.storm, minute=1, severity=1).model_dump(mode="json")]
    report = build_branch_preview(state, [p.id for p in state.plans], runway_delay=assumption)
    notices = []
    for branch in report["branches"]:
        assert branch["scope_flight_ids"] == report["scope_flight_ids"]
        base = branch["rehearsal"]["scheduled_events"]
        events = branch["hypothesis_rehearsal"]["scheduled_events"]
        assert len(events) == len(base) + 1 and all(e in events for e in base)
        notices.append([e for e in events if e["id"] == report["runway_delay_hypothesis"]["event_id"]][0])
    assert notices[0] == notices[1] == notices[2]


@pytest.mark.parametrize("change", ["open", "no_time", "expired", "beyond_horizon", "unknown"])
def test_invalid_runway_assumptions_are_not_silently_coerced(tmp_path, change):
    _, state, assumption = closed_fixture(tmp_path)
    runway = state.scenario.runways[0]
    if change == "open":
        runway.status = ResourceStatus.open
    elif change == "no_time":
        runway.reopen_minute = None
    elif change == "expired":
        runway.reopen_minute = 0
    elif change == "beyond_horizon":
        runway.reopen_minute = 240
    else:
        assumption.runway_id = "UNKNOWN"
    with pytest.raises(ValueError):
        build_branch_preview(state, [state.plans[0].id], runway_delay=assumption)


def test_conflicting_scheduled_runway_instruction_is_rejected(tmp_path):
    _, state, assumption = closed_fixture(tmp_path)
    event = EventSpec(id="explicit-clearance", type=EventType.runway_reopened,
                      resource_id=assumption.runway_id, minute=8)
    state.data_snapshot["scheduled_events"] = [event.model_dump(mode="json")]
    before = state.model_dump(mode="json")
    with pytest.raises(ValueError, match="Conflicting scheduled"):
        build_branch_preview(state, [state.plans[0].id], runway_delay=assumption)
    assert state.model_dump(mode="json") == before


def test_existing_committed_operations_survive_late_opening_preview(tmp_path):
    _, state = fixture(tmp_path)
    target = state.scenario.runways[1]
    target.status, target.reopen_minute = ResourceStatus.closed, 100
    plan, failure = solve_candidate(state.scenario, state.plans[0].profile)
    assert plan is not None and failure is None
    state.plans = [plan]
    first = min(plan.assignments, key=lambda row: row.landing_minute)
    assert first.runway != target.id
    state.scenario.current_minute = first.landing_minute
    operation = commit_landing(state.scenario, first, plan_id=plan.id, revision=1)
    state.execution = ExecutionCursor(active_plan_id=plan.id, minute=first.landing_minute,
                                     committed_operations=[operation])
    before = state.model_dump(mode="json")
    report = build_branch_preview(state, [plan.id],
        runway_delay=RunwayDelayHypothesis(runway_id=target.id, delay_min=10))
    branch = report["branches"][0]
    assert branch["status"] == "evaluated"
    assert first.flight_id not in report["scope_flight_ids"]
    assert report["scope_connection_passengers"] == sum(f.connections for f in state.scenario.flights
                                                       if f.id in report["scope_flight_ids"])
    final = branch["hypothesis_rehearsal"]["continuation"]
    fixed = next(op for op in final["committed_operations"] if op["flight_id"] == first.flight_id)
    for key in ("flight_id", "runway", "gate", "landing_minute", "gate_start_minute", "gate_release_minute", "plan_id", "revision"):
        assert fixed[key] == operation.model_dump(mode="json")[key]
    assert state.model_dump(mode="json") == before


def test_http_cache_identity_validation_and_no_live_events(tmp_path, monkeypatch):
    from stormshift_v2 import api
    service, state, assumption = closed_fixture(tmp_path)
    monkeypatch.setattr(api, "service", service)
    client = TestClient(api.app)
    url = f"/api/v2/sessions/{state.id}/previews"
    payload = request(state).model_dump()
    payload["runway_delay"] = assumption.model_dump()
    before = service.store.get_session(state.id).model_dump(mode="json")
    events = service.store.replay(state.id)
    response = client.post(url, json=payload)
    assert response.status_code == 200, response.text
    report = response.json()
    assert client.post(url, json=payload).json() == report
    assert client.get(url + "/" + report["id"]).json() == report
    payload["runway_delay"]["delay_min"] = 5
    second = client.post(url, json=payload)
    assert second.status_code == 200
    assert second.json()["id"] != report["id"]
    for invalid in (0, 31, True, "10", 10.5):
        payload["runway_delay"]["delay_min"] = invalid
        assert client.post(url, json=payload).status_code == 422
    payload["runway_delay"] = {"runway_id": "UNKNOWN", "delay_min": 10}
    assert client.post(url, json=payload).status_code == 409
    payload["plan_ids"] = [state.plans[0].id]
    payload["runway_delay"] = assumption.model_dump()
    payload["feedback"] = True
    assert client.post(url, json=payload).status_code == 422
    assert service.store.get_session(state.id).model_dump(mode="json") == before
    assert service.store.replay(state.id) == events
    assert len(service.store.list_sessions()) == 1


def test_infeasible_hypothesis_has_no_completion_time_or_success(tmp_path, monkeypatch):
    from stormshift_v2 import branch_rehearsal
    _, state, assumption = closed_fixture(tmp_path)
    monkeypatch.setattr(branch_rehearsal, "solve_candidate", lambda *a, **k: (None, None))
    branch = build_branch_preview(state, [state.plans[0].id], runway_delay=assumption)["branches"][0]
    row = branch["hypothesis_comparison"]["hypothesis"]
    assert row["status"] == "needs_decision"
    assert row["completed_minute"] is None and row["outcome"] is None
    assert row["changed_pending_flight_ids"] == []
    assert branch["hypothesis_rehearsal"]["continuation"]["stopped_reason"] == "repair_infeasible"
