"""Regression for an occupied-gate hint pinning the execution clock at T+28."""
from stormshift_v2.execution import next_execution_minute
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.schemas import CommittedOperation, ConnectingDeparture, ExecutionCursor, ResourceStatus


def _state():
    scenario = storm_scenario(seed=2026)
    scenario.current_minute = 28
    for runway in scenario.runways:
        runway.status = ResourceStatus.open
        runway.reopen_minute = None
    for gate in scenario.gates:
        gate.status = ResourceStatus.available
    gate = scenario.gates[0]
    gate.status = ResourceStatus.occupied
    gate.occupied_by = "SK218"
    gate.available_minute = 28  # stale hint, not the committed release at 30
    operation = CommittedOperation(
        flight_id="SK218", plan_id="test", revision=1, landing_minute=4,
        runway="07L", runway_release_minute=7, runway_released=True,
        gate=gate.id, gate_start_minute=8, gate_release_minute=30, stage="at_gate",
    )
    cursor = ExecutionCursor(active_plan_id="test", minute=28, status="running",
                             committed_operations=[operation])
    return scenario, cursor


def test_stale_gate_hint_cannot_pin_the_clock_to_current_minute():
    scenario, cursor = _state()
    assert next_execution_minute(scenario, cursor) == 30


def test_departure_retained_stand_uses_connection_milestones():
    scenario, cursor = _state()
    cursor.committed_operations[0].stage = "released"
    cursor.connection = ConnectingDeparture(source_flight_id="SK218", passenger_count=24,
        carrier_flight_id="SK218", gate=scenario.gates[0].id, stage="boarding", next_minute=34)
    assert next_execution_minute(scenario, cursor) == 34


def test_unmanaged_initial_gate_still_releases_at_current_minute():
    scenario, cursor = _state()
    cursor.committed_operations = []
    assert next_execution_minute(scenario, cursor) == 28
