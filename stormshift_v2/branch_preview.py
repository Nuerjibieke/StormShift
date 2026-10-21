"""Read-only, same-snapshot futures. No plan generation or live execution here."""
from __future__ import annotations

import hashlib
import json

from pydantic import Field, model_validator
from .config import settings
from .repair_controller import FEEDBACK_VERSION

from .branch_rehearsal import REHEARSAL_VERSION, rehearsal_events, rehearse_candidate
from .intent_compiler import confirmed_intent
from .optimizer import ACTIVE_STATES, validate_assignments
from .resource_calendar import effective_turnaround_buffers, validate_resource_calendar
from .schemas import SessionState, StrictModel
from .runway_hypothesis import RunwayDelayHypothesis, delay_notice
from .simulation import (
    PREVIEW_METHOD_VERSION, _single_run, build_passenger_graph, simulate_candidate,
)


class BranchPreviewRequest(StrictModel):
    plan_ids: list[str] = Field(min_length=1, max_length=3)
    expected_sequence: int = Field(ge=0)
    expected_updated_at: str = Field(min_length=1, max_length=100)
    feedback: bool = False
    runway_delay: RunwayDelayHypothesis | None = None

    @model_validator(mode="after")
    def single_feedback_candidate(self):
        if self.feedback and len(self.plan_ids) != 1:
            raise ValueError("反馈修正对照一次只选择一个候选 / Select one candidate")
        if self.feedback and self.runway_delay is not None:
            raise ValueError("晚开放对照与模型反馈请分开运行 / Run the delay and feedback comparisons separately")
        return self


def snapshot_digest(state: SessionState) -> str:
    # require() refreshes this read-only audit on each call; it is not world state.
    payload = state.model_dump(mode="json")
    payload["decision_lab"].pop("event_integrity", None)
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()


def preview_id(state: SessionState, plan_ids: list[str], *, feedback=False, config=settings, runway_delay=None) -> str:
    data = [PREVIEW_METHOD_VERSION, REHEARSAL_VERSION, snapshot_digest(state), sorted(plan_ids)]
    if feedback:
        data += [FEEDBACK_VERSION, config.llm_mode, config.fast_model, config.fast_base_url]
    if runway_delay is not None:
        data += ["runway-delay-notice-v1", runway_delay.model_dump(mode="json")]
    return "preview_" + hashlib.sha256(json.dumps(data).encode()).hexdigest()[:32]


def verify_preview_request(state: SessionState, request: BranchPreviewRequest) -> None:
    if (state.sequence != request.expected_sequence
            or state.model_dump(mode="json")["updated_at"] != request.expected_updated_at):
        raise ValueError("现场已变化，请刷新后重新预演 / Snapshot changed; refresh first")
    if len(set(request.plan_ids)) != len(request.plan_ids):
        raise ValueError("不能重复选择同一个方案 / Duplicate candidate")
    if not set(request.plan_ids) <= {plan.id for plan in state.plans}:
        raise ValueError("候选方案不属于当前会话 / Candidate is not in this session")
    if state.intent and state.intent.needs_confirmation:
        raise ValueError("先确认或撤回指挥草稿，再进行预演 / Resolve the pending intent first")
    if request.runway_delay is not None:
        candidate = next(p for p in state.plans if p.id == request.plan_ids[0])
        delay_notice(state, rehearsal_events(state, candidate), request.runway_delay)


def build_branch_preview(state: SessionState, plan_ids: list[str], *, feedback=False, config=settings, runway_delay=None) -> dict:
    """Deterministic 50-world comparison, plus world 0 for a shared scrubber.

    All metrics concern the same not-yet-committed flight scope. The committed
    prefix is shown as recorded, not counted as a newly simulated success.
    """
    state = state.model_copy(deep=True)
    scenario = state.scenario
    committed = {row.flight_id for row in scenario.committed_reservations}
    operations = state.execution.committed_operations if state.execution else []
    calendar_failures = validate_resource_calendar(scenario, scenario.committed_reservations)
    reservations = {
        (row.flight_id, row.kind): row for row in scenario.committed_reservations
    }
    for op in operations:
        expected = [
            ("runway", op.runway, op.landing_minute, op.runway_release_minute),
            ("gate", op.gate, op.gate_start_minute, op.gate_release_minute),
        ]
        for kind, resource, start, end in expected:
            row = reservations.get((op.flight_id, kind))
            if row is None or (row.resource_id, row.start_minute, row.end_minute) != (resource, start, end):
                calendar_failures.append("FAIL 已执行航班资源证据不完整 / Incomplete committed calendar: " + op.flight_id)
    if calendar_failures:
        raise ValueError("; ".join(calendar_failures))
    active = sorted(flight.id for flight in scenario.flights
                    if flight.state in ACTIVE_STATES and flight.id not in committed)
    if not active:
        raise ValueError("没有待执行航班需要预演 / No pending flights")
    passenger_total = sum(flight.connections for flight in scenario.flights if flight.id in active)
    graph = build_passenger_graph(scenario)
    by_id = {plan.id: plan for plan in state.plans}
    shared_events = rehearsal_events(state, by_id[sorted(plan_ids)[0]])
    hypothesis = None
    hypothesis_events = None
    if runway_delay is not None:
        if feedback:
            raise ValueError("Run the delay and feedback comparisons separately")
        hypothesis, notice = delay_notice(state, shared_events, runway_delay)
        hypothesis_events = sorted([*shared_events, notice], key=lambda event: (event.minute, event.id))
    branches = []
    for plan_id in sorted(plan_ids):
        plan = by_id[plan_id].model_copy(deep=True)
        # Keep invalid/unknown/non-active assignments so the independent validator
        # can reject them; remove only operations backed by the committed ledger.
        plan.assignments = [row for row in plan.assignments if row.flight_id not in committed]
        proof = validate_assignments(
            scenario, plan.assignments, confirmed_intent(state),
            turnaround_buffer_by_flight=effective_turnaround_buffers(plan))
        failures = [row for row in proof if row.startswith("FAIL")]
        if not plan.feasible:
            failures.append("FAIL 候选已标记不可行 / Candidate marked infeasible")
        branch = {
            "plan_id": plan.id, "title": plan.title, "profile": plan.profile.value,
            "status": "invalid" if failures else "evaluated",
            "constraint_proof": proof, "failures": failures,
            "report": None, "sample": [], "rehearsal": None, "scope_flight_ids": active,
        }
        if not failures:
            report = simulate_candidate(scenario, plan, runs=50, respect_committed=True)
            sample = _single_run(scenario, plan, graph, scenario.seed * 1009,
                                 respect_committed=True)
            branch["report"] = report.model_dump(mode="json")
            branch["rehearsal"] = rehearse_candidate(state, plan, shared_events)
            if hypothesis_events is not None:
                delayed = rehearse_candidate(state, plan, hypothesis_events)
                branch["hypothesis_rehearsal"] = delayed
                branch["hypothesis_comparison"] = {
                    "baseline": rehearsal_metrics(branch["rehearsal"]),
                    "hypothesis": rehearsal_metrics(delayed),
                    "controls": ["same_source_snapshot", "same_initial_candidate", "same_confirmed_intent",
                                 "same_committed_prefix", "same_events_except_delay_notice",
                                 "same_solver_call_cap_and_search_parameters"],
                    "caveats": ["nominal_not_stochastic", "actual_call_counts_may_differ",
                                "hypothetical_repairs_not_approved", "no_claim_of_improvement",
                                "incomplete_runs_have_no_completion_time"],
                }
            if feedback:
                branch["feedback_rehearsal"] = rehearse_candidate(
                    state, plan, shared_events, feedback=True, config=config)
                branch["repair_comparison"] = repair_comparison(
                    branch["rehearsal"], branch["feedback_rehearsal"])
            branch["sample"] = [{
                "flight_id": row.flight_id, "runway": row.runway, "gate": row.gate,
                "landing_minute": sample.landing_times[row.flight_id],
                "runway_release_minute": sample.landing_times[row.flight_id]
                    + next(r.separation_min for r in scenario.runways if r.id == row.runway),
                "gate_in_minute": sample.gate_in_times[row.flight_id],
                "gate_out_minute": sample.gate_out_times[row.flight_id],
                "missed_connections": sample.missed_by_flight.get(row.flight_id, 0),
                "fuel_risk": sample.fuel_risks[row.flight_id],
            } for row in sorted(plan.assignments, key=lambda item: item.flight_id)]
        branches.append(branch)
    end = max([float(scenario.current_minute) + 1]
              + [row["gate_out_minute"] for branch in branches for row in branch["sample"]]
              + [float(op.gate_release_minute) for op in operations])
    return {
        "id": preview_id(state, plan_ids, feedback=feedback, config=config, runway_delay=runway_delay),
        "runway_delay_hypothesis": hypothesis,
        "feedback_requested": feedback,
        "source_digest": snapshot_digest(state),
        "session_id": state.id,
        "source_sequence": state.sequence,
        "source_updated_at": state.model_dump(mode="json")["updated_at"],
        "source_minute": scenario.current_minute,
        "method_version": PREVIEW_METHOD_VERSION, "rehearsal_version": REHEARSAL_VERSION,
        "runs": 50, "sample_world_index": 0, "sample_world_seed": scenario.seed * 1009,
        "world_seed_formula": "scenario.seed * 1009 + index * 7919",
        "scope_flight_ids": active, "scope_connection_passengers": passenger_total,
        "excluded_flight_ids": sorted(f.id for f in scenario.flights if f.id not in active),
        "committed_operations": [op.model_dump(mode="json") for op in operations],
        "committed_reservations": [row.model_dump(mode="json") for row in scenario.committed_reservations],
        "end_minute": end, "branches": branches,
        "limitations": [
            "conditional_current_snapshot",
            "monte_carlo_excludes_future_events",
            "event_rehearsal_uses_nominal_times",
            "synthetic_operations_not_real_airport_prediction",
            "sample_world_not_aggregate",
            "preview_is_not_approval",
        ],
    }


def rehearsal_metrics(run):
    final = run["continuation"]
    changed = set()
    for repair in final["repairs"]:
        before = {row["flight_id"]: row for row in repair["before_assignments"]}
        for row in repair["after_assignments"]:
            prior = before.get(row["flight_id"])
            if prior and any(row[key] != prior[key] for key in ("runway", "gate", "landing_minute")):
                changed.add(row["flight_id"])
    return {
        "status": final["status"], "completed_minute": final["minute"]
            if final["status"] == "completed" else None,
        "outcome": final["outcome"], "changed_pending_flight_ids": sorted(changed),
        "solver_calls": run["solver_calls"], "model_calls": run["model_calls"],
    }


def repair_comparison(baseline, feedback):
    return {
        "baseline": rehearsal_metrics(baseline), "feedback": rehearsal_metrics(feedback),
        "controls": ["same_snapshot", "same_observed_event_stream", "same_profile_and_confirmed_intent",
                     "same_committed_prefix", "same_solver_call_cap_and_search_parameters"],
        "caveats": ["nominal_not_stochastic", "actual_call_counts_may_differ",
                    "feedback_has_model_overhead", "no_claim_of_improvement",
                    "incomplete_runs_have_no_completion_time"],
    }
