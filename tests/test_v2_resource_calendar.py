from __future__ import annotations

from stormshift_v2.optimizer import solve_candidate, validate_assignments
from stormshift_v2.resource_calendar import (
    assignment_reservations,
    effective_turnaround_buffers,
    validate_resource_calendar,
)
from stormshift_v2.scenarios import medical_scenario, storm_scenario
from stormshift_v2.schemas import (
    DataMode,
    FlightAssignment,
    Mode,
    PlanProfile,
    ResourceStatus,
    SessionState,
)
from stormshift_v2.store import SessionStore


def _two_flight_scenario(*, current_minute: int = 0):
    scenario = storm_scenario(seed=2061)
    flights = [
        flight.model_copy(deep=True)
        for flight in scenario.flights
        if flight.id in {"SK218", "TK608"}
    ]
    runway = next(
        item.model_copy(deep=True)
        for item in scenario.runways
        if item.id == "07L"
    )
    runway.status = ResourceStatus.open
    runway.reopen_minute = None
    gate = next(
        item.model_copy(deep=True)
        for item in scenario.gates
        if item.id == "A4"
    )
    gate.status = ResourceStatus.available
    gate.available_minute = 0
    return scenario.model_copy(
        update={
            "current_minute": current_minute,
            "flights": flights,
            "runways": [runway],
            "gates": [gate],
            "committed_reservations": [],
        },
        deep=True,
    )


def _assignment(
    flight_id: str,
    landing_minute: int,
    scheduled_arrival: int,
) -> FlightAssignment:
    return FlightAssignment(
        flight_id=flight_id,
        runway="07L",
        landing_minute=landing_minute,
        gate="A4",
        hold_minutes=landing_minute - scheduled_arrival,
        taxi_minutes=5,
        remote_stand=False,
    )


def _medical_candidate():
    scenario = medical_scenario(seed=2062)
    candidate, infeasible = solve_candidate(
        scenario,
        PlanProfile.balanced,
        timeout_seconds=1,
    )
    assert infeasible is None
    assert candidate is not None
    assert not any(
        row.startswith("FAIL") for row in candidate.constraint_proof
    )
    return scenario, candidate


def test_a4_historical_overlap_9_31_and_24_46_is_rejected():
    scenario = _two_flight_scenario()
    assignments = [
        _assignment("SK218", 4, 4),
        _assignment("TK608", 19, 7),
    ]

    proof = validate_assignments(scenario, assignments)

    assert any(
        "A4" in row and "[9,31)" in row and "[24,46)" in row
        for row in proof
    )


def test_committed_sliding_reservation_is_fixed_in_cp_sat_and_boundary_is_legal():
    scenario = _two_flight_scenario(current_minute=15)
    committed = assignment_reservations(
        scenario,
        [_assignment("SK218", 4, 4)],
        source_plan_id="approved-v1",
        revision=1,
        committed_minute=0,
    )
    scenario.committed_reservations = committed

    candidate, infeasible = solve_candidate(
        scenario,
        PlanProfile.balanced,
        timeout_seconds=1,
    )

    assert infeasible is None
    assert candidate is not None
    assert [item.flight_id for item in candidate.assignments] == ["TK608"]
    # A4 is committed on [9,31); taxi is 5, so landing at 26 is the
    # earliest legal left-closed/right-open boundary.
    assert candidate.assignments[0].landing_minute == 26
    assert not any(
        row.startswith("FAIL")
        for row in validate_assignments(
            scenario,
            candidate.assignments,
        )
    )


def test_historical_commitment_is_not_retroactively_invalidated_by_outage():
    scenario = _two_flight_scenario(current_minute=15)
    committed = assignment_reservations(
        scenario,
        [_assignment("SK218", 4, 4)],
        source_plan_id="approved-v1",
        committed_minute=0,
    )
    scenario.gates[0].status = ResourceStatus.blocked
    scenario.gates[0].available_minute = 30

    assert validate_resource_calendar(scenario, committed) == []


def test_validator_defaults_to_exact_active_flight_coverage():
    scenario, candidate = _medical_candidate()

    missing = [
        item for item in candidate.assignments if item.flight_id != "MED901"
    ]
    proof = validate_assignments(scenario, missing)
    assert any("未覆盖全部待调度航班" in row and "MED901" in row for row in proof)

    partial_proof = validate_assignments(
        scenario,
        missing[:1],
        require_complete=False,
    )
    assert not any("未覆盖全部待调度航班" in row for row in partial_proof)

    med = next(
        item for item in candidate.assignments if item.flight_id == "MED901"
    )
    duplicate = [*candidate.assignments, med]
    proof = validate_assignments(scenario, duplicate)
    assert any(
        "重复分配" in row and "MED901" in row for row in proof
    )

    ghost = candidate.assignments[0].model_copy(
        update={"flight_id": "GHOST"}
    )
    proof = validate_assignments(scenario, [ghost], require_complete=False)
    assert any("未知航班" in row and "GHOST" in row for row in proof)


def test_med901_deadline_and_priority_order_are_enforced():
    scenario, candidate = _medical_candidate()
    med = next(
        item for item in candidate.assignments if item.flight_id == "MED901"
    )
    late_med = med.model_copy(
        update={
            "landing_minute": scenario.current_minute + 9,
            "hold_minutes": scenario.current_minute + 9 - 1,
        }
    )
    late_plan = [
        late_med if item.flight_id == "MED901" else item
        for item in candidate.assignments
    ]
    proof = validate_assignments(scenario, late_plan)
    assert any("MED901" in row and "8 分钟" in row for row in proof)

    regular = next(
        item for item in candidate.assignments if item.flight_id == "SK712"
    )
    priority_med = med.model_copy(
        update={"landing_minute": 7, "hold_minutes": 6}
    )
    earlier_regular = regular.model_copy(
        update={"landing_minute": 2, "hold_minutes": 0}
    )
    priority_plan = [
        (
            priority_med
            if item.flight_id == "MED901"
            else earlier_regular
            if item.flight_id == "SK712"
            else item
        )
        for item in candidate.assignments
    ]
    proof = validate_assignments(scenario, priority_plan)
    assert any("医疗优先航班排在普通航班之后" in row for row in proof)


def test_derived_assignment_fields_cannot_be_faked():
    scenario, candidate = _medical_candidate()
    target = candidate.assignments[0]
    forged = target.model_copy(
        update={
            "hold_minutes": target.hold_minutes + 11,
            "taxi_minutes": target.taxi_minutes + 7,
            "remote_stand": not target.remote_stand,
        }
    )
    forged_plan = [
        forged if item.flight_id == target.flight_id else item
        for item in candidate.assignments
    ]

    proof = validate_assignments(scenario, forged_plan)

    assert any("等待时间字段不可信" in row for row in proof)
    assert any("滑行时间字段不可信" in row for row in proof)
    assert any("远机位字段" in row for row in proof)


def test_runway_separation_boundary_is_legal_but_inside_is_rejected():
    scenario = _two_flight_scenario()
    extra_gate = next(
        item.model_copy(deep=True)
        for item in storm_scenario(seed=2061).gates
        if item.id == "A5"
    )
    extra_gate.status = ResourceStatus.available
    scenario.gates.append(extra_gate)
    left = _assignment("SK218", 4, 4)
    boundary = FlightAssignment(
        flight_id="TK608",
        runway="07L",
        landing_minute=7,
        gate="A5",
        hold_minutes=0,
        taxi_minutes=6,
        remote_stand=False,
    )

    proof = validate_assignments(scenario, [left, boundary])
    assert not any(row.startswith("FAIL") for row in proof)

    inside = boundary.model_copy(
        update={"landing_minute": 6, "hold_minutes": -1}
    )
    proof = validate_assignments(scenario, [left, inside])
    assert any(
        "runway 07L" in row and "重复占用" in row for row in proof
    )


def test_committed_reservations_survive_sqlite_session_round_trip(tmp_path):
    scenario = _two_flight_scenario(current_minute=15)
    scenario.committed_reservations = assignment_reservations(
        scenario,
        [_assignment("SK218", 4, 4)],
        source_plan_id="approved-v1",
        revision=2,
        committed_minute=0,
    )
    state = SessionState(
        mode=Mode.guided,
        data_mode=DataMode.synthetic,
        scenario=scenario,
    )
    store = SessionStore(tmp_path / "resource-calendar.db")

    store.save_session(state)
    restored = store.get_session(state.id)

    assert restored is not None
    assert restored.scenario.committed_reservations == (
        scenario.committed_reservations
    )


def test_turnaround_buffer_applies_only_to_the_plan_that_was_solved_with_it():
    _, candidate = _medical_candidate()
    flight_id = candidate.assignments[0].flight_id
    candidate.risk_shield = {
        "adaptive_envelope": {
            "turnaround_buffer_by_flight": {flight_id: 3},
            "applies_to_candidate": False,
        }
    }
    assert effective_turnaround_buffers(candidate) == {}

    candidate.risk_shield["adaptive_envelope"][
        "applies_to_candidate"
    ] = True
    assert effective_turnaround_buffers(candidate) == {flight_id: 3}
