from stormshift_v2.runtime_assurance import verify_runtime_contract
from stormshift_v2.schemas import ExecutionEvent
from stormshift_v2.scenarios import storm_scenario


def _event(sequence, minute, event_type, payload=None):
    return ExecutionEvent(
        sequence=sequence,
        session_id="session_contract",
        sim_minute=minute,
        type=event_type,
        payload=payload or {},
    )


def _guided_events(response_minute=10):
    incident_id = "evt_tug"
    return [
        _event(1, 0, "plan_approved", {"plan_id": "plan_a"}),
        _event(2, 4, "execution_tick", {"flight_id": "A"}),
        _event(
            3,
            9,
            "ground_vehicle_failure",
            {
                "id": incident_id,
                "payload": {
                    "policy_decision": {
                        "action": "causal_local_repair",
                    }
                },
            },
        ),
        _event(
            4,
            response_minute,
            "automatic_replan",
            {"trigger": incident_id, "safety_gate": "passed"},
        ),
    ]


def test_runtime_contract_passes_bounded_guided_recovery():
    result = verify_runtime_contract(
        _guided_events(),
        target_phase="recovered",
        recovery_minute=35,
    )

    assert result["status"] == "passed"
    assert result["passed"] == result["total"] == 4
    assert result["observed_worst_response_min"] == 1
    assert [item["type"] for item in result["timeline"]] == [
        "plan_approved",
        "ground_vehicle_failure",
        "automatic_replan",
        "recovered",
    ]


def test_runtime_contract_rejects_late_incident_response():
    result = verify_runtime_contract(
        _guided_events(response_minute=13),
        target_phase="recovered",
        recovery_minute=35,
    )

    assert result["status"] == "failed"
    rule = next(
        item for item in result["rules"]
        if item["id"] == "bounded_incident_response"
    )
    assert rule["passed"] is False
    assert result["observed_worst_response_min"] == 4


def test_runtime_contract_accepts_explicit_human_safe_stop():
    events = _guided_events()[:-1] + [
        _event(
            4,
            10,
            "automatic_replan_requires_approval",
            {"trigger": "evt_tug", "safety_gate": "human_required"},
        )
    ]
    result = verify_runtime_contract(
        events,
        target_phase="awaiting_approval",
    )

    assert result["status"] == "safe_stop"
    assert result["passed"] == result["total"] == 4
    assert result["safety_gates"][0]["safe_stop"] is True


def test_runtime_contract_detects_motion_without_authorization():
    result = verify_runtime_contract(
        [_event(1, 3, "execution_tick", {"flight_id": "A"})],
        target_phase="monitoring",
    )

    assert result["status"] == "failed"
    rule = next(
        item for item in result["rules"]
        if item["id"] == "authorization_before_motion"
    )
    assert rule["passed"] is False


def test_runtime_contract_rejects_actions_outside_resource_time_envelope():
    scenario = storm_scenario()
    events = [
        _event(1, 0, "plan_approved", {"plan_id": "plan_a"}),
        _event(
            2,
            23,
            "execution_tick",
            {
                "flight_id": "SK218",
                "runway": "07R",
                "gate": "A3",
                "plan_id": "plan_a",
                "schedule_revision": "approved",
            },
        ),
        _event(
            3,
            24,
            "execution_tick",
            {
                "flight_id": "SK218",
                "runway": "07R",
                "gate": "A3",
                "plan_id": "plan_a",
                "schedule_revision": "approved",
            },
        ),
    ]

    result = verify_runtime_contract(
        events,
        target_phase="monitoring",
        scenario=scenario,
    )

    assert result["status"] == "failed"
    assert result["passed"] == 4
    assert result["total"] == 5
    envelope = result["execution_envelope"]
    assert envelope["passed"] is False
    assert envelope["checked_actions"] == 2
    assert any("07R unavailable until T+24" in item for item in envelope["violations"])
    assert any("duplicate execution" in item for item in envelope["violations"])
    assert any("separation 1m < 3m" in item for item in envelope["violations"])
    rule = next(
        item for item in result["rules"]
        if item["id"] == "execution_resource_envelope"
    )
    assert rule["passed"] is False


def test_runtime_contract_reconstructs_dynamic_runway_closure_windows():
    scenario = storm_scenario()
    events = [
        _event(1, 0, "plan_approved", {"plan_id": "plan_a"}),
        _event(
            2,
            5,
            "execution_tick",
            {
                "flight_id": "SK218",
                "runway": "07L",
                "gate": "A3",
                "plan_id": "plan_a",
                "schedule_revision": "approved",
            },
        ),
        _event(
            3,
            10,
            "runway_closed",
            {"resource_id": "07L", "duration_min": 20},
        ),
        _event(
            4,
            12,
            "execution_tick",
            {
                "flight_id": "CX551",
                "runway": "07L",
                "gate": "A4",
                "plan_id": "plan_a",
                "schedule_revision": "approved",
            },
        ),
        _event(5, 15, "runway_reopened", {"runway": "07L"}),
        _event(
            6,
            16,
            "execution_tick",
            {
                "flight_id": "JR330",
                "runway": "07L",
                "gate": "A6",
                "plan_id": "plan_a",
                "schedule_revision": "approved",
            },
        ),
    ]

    result = verify_runtime_contract(
        events,
        target_phase="monitoring",
        scenario=scenario,
    )
    envelope = result["execution_envelope"]

    assert envelope["runway_windows"]["07L"] == [
        {"closed_from": 10.0, "reopened_at": 15.0}
    ]
    by_flight = {row["flight_id"]: row for row in envelope["checks"]}
    assert by_flight["SK218"]["passed"] is True
    assert by_flight["CX551"]["passed"] is False
    assert by_flight["CX551"]["reasons"] == ["07L closed T+10–T+15"]
    assert by_flight["JR330"]["passed"] is True


def test_runtime_contract_rejects_unapproved_schedule_provenance():
    scenario = storm_scenario()
    result = verify_runtime_contract(
        [
            _event(1, 0, "plan_approved", {"plan_id": "plan_a"}),
            _event(
                2,
                24,
                "execution_tick",
                {
                    "flight_id": "SK218",
                    "runway": "07R",
                    "gate": "A3",
                    "plan_id": "plan_b",
                    "schedule_revision": "approved",
                },
            ),
        ],
        target_phase="monitoring",
        scenario=scenario,
    )

    envelope = result["execution_envelope"]
    assert envelope["passed"] is False
    assert envelope["checks"][0]["reasons"] == [
        "action does not belong to an approved plan"
    ]


def test_runtime_contract_rejects_tampered_fuel_clock_evidence():
    events = [
        _event(1, 0, "plan_approved", {"plan_id": "plan_a"}),
        _event(
            2,
            9,
            "ground_vehicle_failure",
            {
                "id": "evt_fuel",
                "payload": {
                    "fuel_clock": {
                        "from_minute": 0,
                        "to_minute": 9,
                        "elapsed_min": 9,
                        "updated_flights": 1,
                        "minimum_remaining_min": 30,
                        "critical_flight_ids": [],
                        "expired_flight_ids": [],
                        "deadline_invariant": (
                            "deadlines preserved; overdue flights diverted"
                        ),
                        "updates": [
                            {
                                "flight_id": "SK218",
                                "before_min": 34,
                                "after_min": 30,
                                "absolute_deadline_min": 34,
                                "status": "preserved",
                            }
                        ],
                    },
                    "policy_decision": {
                        "action": "continue_and_monitor",
                    },
                },
            },
        ),
    ]

    result = verify_runtime_contract(
        events,
        target_phase="monitoring",
    )

    assert result["status"] == "failed"
    assert result["total"] == 5
    assert result["fuel_clock"]["passed"] is False
