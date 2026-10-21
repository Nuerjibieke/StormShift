from copy import deepcopy

from stormshift_v2.connection_journey import initialize_connection, process_connection, connection_complete
from stormshift_v2.execution import next_execution_minute
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.schemas import (
    CommittedOperation, ExecutionCursor, FlightAssignment, Mode, DataMode,
    SessionState, ResourceStatus, ResourceReservation,
)


class Recorder:
    def __init__(self):
        self.events = []
    def _record(self, state, kind, payload):
        self.events.append((kind, state.scenario.current_minute, deepcopy(payload)))


def fixture():
    scenario = storm_scenario(seed=2026)
    scenario.current_minute = 8
    for gate in scenario.gates:
        gate.status = ResourceStatus.available
        gate.occupied_by = None
        gate.available_minute = 0
    ids = [f.id for f in scenario.flights if f.connections > 0][:2]
    source, carrier = ids
    cursor = ExecutionCursor(active_plan_id="p", scope_flight_ids=ids, pending_assignments=[
        FlightAssignment(flight_id=source,runway="07L",gate="A4",landing_minute=1,hold_minutes=0,taxi_minutes=5)
    ])
    state = SessionState(mode=Mode.guided,data_mode=DataMode.synthetic,scenario=scenario,execution=cursor)
    initialize_connection(state)
    cursor.pending_assignments = []
    cursor.committed_operations = [
        CommittedOperation(flight_id=source,plan_id="p",revision=1,landing_minute=1,runway="07L",
            runway_release_minute=4,gate="A4",gate_start_minute=6,gate_release_minute=28,stage="at_gate",runway_released=True),
        CommittedOperation(flight_id=carrier,plan_id="p",revision=1,landing_minute=3,runway="07L",
            runway_release_minute=6,gate="A6",gate_start_minute=8,gate_release_minute=30,stage="at_gate",runway_released=True),
    ]
    for op in cursor.committed_operations:
        gate=next(g for g in scenario.gates if g.id==op.gate)
        gate.status=ResourceStatus.occupied; gate.occupied_by=op.flight_id; gate.available_minute=op.gate_release_minute
    return state, Recorder()


def step(state, recorder):
    minute = state.execution.connection.next_minute
    assert minute is not None
    state.scenario.current_minute = minute
    process_connection(recorder,state)


def test_full_journey_is_ordered_and_preserves_one_physical_airframe():
    state,r=fixture();process_connection(r,state)
    c=state.execution.connection
    assert c.passenger_stage=="disembarking"
    assert c.carrier_flight_id==state.execution.committed_operations[-1].flight_id
    assert not connection_complete(state)
    for _ in range(20):
        if connection_complete(state):break
        previous=state.scenario.current_minute
        step(state,r)
        assert state.scenario.current_minute>previous
    assert c.stage=="completed" and c.boarded_count==c.passenger_count==24
    kinds=[kind for kind,_,_ in r.events]
    ordered=["connection_disembarking","connection_transferring","connection_waiting","connection_boarding",
             "departure_pushback","departure_taxi","departure_holding","departure_takeoff","departure_airborne","connection_completed"]
    assert [kind for kind in kinds if kind in ordered]==ordered
    assert state.execution.scope_flight_ids==[o.flight_id for o in state.execution.committed_operations]
    assert c.flight_id not in state.execution.scope_flight_ids  # do not silently change inbound metric denominators
    assert r.events[0][2]["boarded_count"]==0  # frozen historical payload, not final-state backfill


def test_closed_runway_blocks_before_takeoff_and_never_reports_completion():
    state,r=fixture();process_connection(r,state)
    while state.execution.connection.stage!="holding_short":
        step(state,r)
    c=state.execution.connection
    next(runway for runway in state.scenario.runways if runway.id==c.runway).status=ResourceStatus.closed
    step(state,r)
    assert c.stage=="blocked"
    assert state.execution.status=="blocked"
    assert not connection_complete(state)
    assert "departure_takeoff" not in [kind for kind,_,_ in r.events]


def test_departure_runway_window_cannot_overlap_a_committed_arrival():
    state,r=fixture();process_connection(r,state)
    c=state.execution.connection
    state.scenario.committed_reservations.append(ResourceReservation(
        id="future-conflict",kind="runway",resource_id=c.runway,flight_id=c.source_flight_id,
        start_minute=c.takeoff_minute,end_minute=c.airborne_minute,committed_minute=8))
    while c.stage not in {"blocked","completed"}:
        step(state,r)
    assert c.stage=="blocked"
    assert "时间窗" in c.blocked_reason
    assert "departure_pushback" not in [kind for kind,_,_ in r.events]


def test_clock_exposes_departure_milestones_and_old_cursors_still_work():
    state,r=fixture();process_connection(r,state)
    assert next_execution_minute(state.scenario,state.execution) is not None
    c=state.execution.connection
    c.next_minute=90
    for op in state.execution.committed_operations:op.stage="released"
    for gate in state.scenario.gates:gate.status=ResourceStatus.available
    for runway in state.scenario.runways:runway.status=ResourceStatus.open
    assert next_execution_minute(state.scenario,state.execution)==90
    state.execution.connection=None
    assert connection_complete(state)

def test_ground_traffic_must_clear_before_departure_boarding_and_pushback():
    state,r=fixture();process_connection(r,state)
    c=state.execution.connection
    while c.passenger_stage!="waiting":
        step(state,r)
    state.execution.committed_operations[0].stage="taxiing"
    state.scenario.current_minute=c.boarding_start_minute
    process_connection(r,state)
    assert c.stage=="blocked"
    assert "滑行" in c.blocked_reason
    assert "departure_pushback" not in [kind for kind,_,_ in r.events]


def test_source_diversion_does_not_fabricate_a_successful_connection():
    state,r=fixture()
    from stormshift_v2.schemas import FlightState
    next(f for f in state.scenario.flights if f.id==state.execution.connection.source_flight_id).state=FlightState.diverted
    process_connection(r,state)
    assert state.execution.connection.stage=="cancelled"
    assert state.execution.connection.boarded_count==0
    assert "connection_completed" not in [kind for kind,_,_ in r.events]
