import asyncio
from dataclasses import replace

from stormshift_v2.config import settings
from stormshift_v2.optimizer import solve_candidate
from stormshift_v2.recourse import (
    analyze_recourse_impact,
    frozen_assignments,
    summarize_repair,
)
from stormshift_v2.schemas import (
    DataMode,
    ExecutionAdvanceRequest,
    EventSpec,
    FlightState,
    EventType,
    Mode,
    Phase,
    PlanProfile,
    SessionState,
)
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.service import SessionService
from stormshift_v2.simulation import generate_ranked_candidates
from stormshift_v2.store import SessionStore
from stormshift_v2.stream import StreamManager


def _secondary_event() -> EventSpec:
    return EventSpec(
        type=EventType.ground_vehicle_failure,
        minute=9,
        resource_id="TUG-3",
        severity=3,
        duration_min=14,
        payload={
            "zone": "Taxiway C",
            "affected_gates": ["A3", "A4", "R1"],
        },
    )


def test_causal_scope_is_smaller_than_future_schedule():
    scenario = storm_scenario()
    plan, failure = solve_candidate(scenario, PlanProfile.balanced)
    assert failure is None and plan is not None
    impact = analyze_recourse_impact(scenario, plan, _secondary_event())

    assert impact["affected_count"] > 0
    assert impact["frozen_count"] > 0
    assert impact["affected_count"] + impact["frozen_count"] == impact["pending_count"]
    assert set(impact["affected_flight_ids"]).isdisjoint(impact["preserved_flight_ids"])
    assert all(path[:2] == ["TUG-3", "Taxiway C"] for path in impact["dependency_paths"])


def test_cp_sat_keeps_causal_boundary_fixed():
    scenario = storm_scenario()
    previous, failure = solve_candidate(scenario, PlanProfile.balanced)
    assert failure is None and previous is not None
    event = _secondary_event()
    impact = analyze_recourse_impact(scenario, previous, event)
    state = SessionState(
        mode=Mode.guided,
        data_mode=DataMode.synthetic,
        scenario=scenario,
    )
    SessionService._apply_event(None, state, event)
    for assignment in previous.assignments:
        flight = next(item for item in scenario.flights if item.id == assignment.flight_id)
        flight.state = (
            "landed" if assignment.landing_minute <= event.minute else "holding"
        )

    repaired, failure = solve_candidate(
        scenario,
        PlanProfile.balanced,
        frozen_assignments=frozen_assignments(previous, impact),
    )
    assert failure is None and repaired is not None
    audit = summarize_repair(previous, repaired, impact)
    assert audit["preserved_verified"] is True
    before = {item.flight_id: item for item in previous.assignments}
    after = {item.flight_id: item for item in repaired.assignments}
    for flight_id in impact["preserved_flight_ids"]:
        assert (
            after[flight_id].runway,
            after[flight_id].landing_minute,
            after[flight_id].gate,
        ) == (
            before[flight_id].runway,
            before[flight_id].landing_minute,
            before[flight_id].gate,
        )


def test_guided_approval_records_auditable_local_repair(tmp_path):
    async def exercise():
        scenario = storm_scenario()
        plans, failure = generate_ranked_candidates(
            scenario,
            [PlanProfile.balanced, PlanProfile.resilient],
            runs=50,
        )
        assert failure is None and plans
        plan = plans[0]
        assert plan.profile == PlanProfile.resilient
        store = SessionStore(tmp_path / "recourse.db")
        service = SessionService(
            config=replace(settings, data_dir=tmp_path),
            store=store,
            streams=StreamManager(),
        )
        state = SessionState(
            mode=Mode.guided,
            data_mode=DataMode.synthetic,
            scenario=scenario,
            phase=Phase.awaiting_approval,
            plans=[plan],
        )
        store.save_session(state)
        store.save_plan(state.id, plan)

        approval = await service.approve(state.id, plan.id)
        assert approval.session.phase == Phase.executing
        assert not any(
            event.type == "execution_tick"
            for event in approval.execution_events
        )

        incident = await service.advance_execution(
            state.id,
            ExecutionAdvanceRequest(
                target_minute=9,
                expected_revision=approval.session.execution.revision,
                request_id="guided-to-secondary-failure",
            ),
        )
        event_types = [event.type for event in incident.execution_events]
        assert event_types.index("ground_vehicle_failure") < event_types.index(
            "trusted_state_reconciled"
        )
        replan_event = next(
            event for event in incident.execution_events
            if event.type in {
                "automatic_policy_replan",
                "automatic_policy_replan_requires_approval",
            }
        )
        assert event_types.index("trusted_state_reconciled") < event_types.index(
            replan_event.type
        )
        recourse = replan_event.payload["recourse"]
        assert recourse["strategy"] == "causal_local_repair"
        assert recourse["preserved_verified"] is True
        assert recourse["frozen_count"] > 0
        assert incident.session.scenario.current_minute == 9
        committed_ids = [
            item.flight_id
            for item in incident.session.execution.committed_operations
        ]
        assert committed_ids
        assert len(committed_ids) == len(set(committed_ids))
        trusted = incident.session.decision_lab["trusted_state"]
        assert trusted["changed_facts"]
        assert trusted["invalidated_constraints"]
        assert all(
            candidate.evidence_basis["trusted_state_digest"] == trusted["digest"]
            for candidate in incident.session.plans
        )

        current = incident.session
        if current.phase == Phase.awaiting_approval:
            resumed = await service.approve(current.id, current.plans[0].id)
            current = resumed.session
        final = await service.advance_execution(
            current.id,
            ExecutionAdvanceRequest(
                target_minute=480,
                expected_revision=current.execution.revision,
                request_id="guided-finish-after-replan",
            ),
        )
        assert final.session.phase == Phase.recovered
        outcome = final.session.decision_lab["execution_outcome"]
        assert outcome["pending"] == 0
        assert outcome["hard_constraints_passed"] is True
        assert outcome["turnaround_complete"] + outcome["safe_diversions"] == outcome["scope_flights"]
        final_ids = [
            item.flight_id
            for item in final.session.execution.committed_operations
        ]
        assert len(final_ids) == len(set(final_ids))
        assert all(
            item.stage == "released"
            for item in final.session.execution.committed_operations
        )

    asyncio.run(exercise())


def test_event_time_fuel_clock_preserves_absolute_deadlines():
    scenario = storm_scenario()
    airborne = {
        flight.id: flight
        for flight in scenario.flights
        if flight.state in {
            FlightState.inbound,
            FlightState.approach,
            FlightState.holding,
        }
    }
    initial_deadlines = {
        flight_id: scenario.current_minute + flight.fuel_margin_min
        for flight_id, flight in airborne.items()
    }
    ground_fuel = {
        flight.id: flight.fuel_margin_min
        for flight in scenario.flights
        if flight.id not in airborne
    }

    first = _secondary_event()
    SessionService._apply_event(None, SessionState(
        mode=Mode.guided,
        data_mode=DataMode.synthetic,
        scenario=scenario,
    ), first)

    assert scenario.current_minute == 9
    assert first.payload["fuel_clock"]["elapsed_min"] == 9
    assert first.payload["fuel_clock"]["updated_flights"] == len(airborne)
    for flight_id, deadline in initial_deadlines.items():
        flight = airborne[flight_id]
        assert scenario.current_minute + flight.fuel_margin_min == deadline
    for flight_id, margin in ground_fuel.items():
        flight = next(item for item in scenario.flights if item.id == flight_id)
        assert flight.fuel_margin_min == margin

    second = EventSpec(
        type=EventType.gate_outage,
        minute=14,
        resource_id="A6",
        severity=2,
        duration_min=5,
    )
    SessionService._apply_event(None, SessionState(
        mode=Mode.guided,
        data_mode=DataMode.synthetic,
        scenario=scenario,
    ), second)

    assert second.payload["fuel_clock"]["elapsed_min"] == 5
    for flight_id, deadline in initial_deadlines.items():
        flight = airborne[flight_id]
        assert scenario.current_minute + flight.fuel_margin_min == deadline


def test_fuel_clock_can_reach_zero_without_creating_time():
    scenario = storm_scenario()
    flight = next(item for item in scenario.flights if item.id == "SK218")
    flight.fuel_margin_min = 8
    event = EventSpec(
        type=EventType.ground_vehicle_failure,
        minute=10,
        resource_id="TUG-X",
        duration_min=2,
    )

    SessionService._apply_event(None, SessionState(
        mode=Mode.guided,
        data_mode=DataMode.synthetic,
        scenario=scenario,
    ), event)

    assert flight.fuel_margin_min == 0
    assert "SK218" in event.payload["fuel_clock"]["critical_flight_ids"]
    assert flight.state == FlightState.diverted
    assert "SK218" in event.payload["fuel_clock"]["expired_flight_ids"]
