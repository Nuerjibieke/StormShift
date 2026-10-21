from stormshift_v2.causal_attribution import attribute_recovery_burden
from stormshift_v2.optimizer import solve_candidate
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.schemas import EventSpec, EventType, PlanProfile


def _reference(scenario):
    plan, failure = solve_candidate(scenario, PlanProfile.balanced)
    assert failure is None and plan is not None
    return plan


def test_initial_storm_attribution_reoptimizes_all_factor_coalitions():
    scenario = storm_scenario(seed=2061)

    result = attribute_recovery_burden(
        scenario,
        _reference(scenario),
        runs_per_coalition=3,
    )

    assert result["status"] == "completed"
    assert result["factor_count"] == 2
    assert result["coalition_count"] == 4
    assert {row["id"] for row in result["factors"]} == {
        "weather_runway",
        "gate_pressure",
    }
    assert result["additivity"]["passed"] is True
    assert result["additivity"]["absolute_error"] == 0
    assert all(world["feasible"] for world in result["coalitions"])
    assert result["common_seed_schedule"] is True


def test_secondary_failure_gets_its_own_exact_shapley_share():
    scenario = storm_scenario(seed=2062)
    scenario.current_minute = 9
    event = EventSpec(
        type=EventType.ground_vehicle_failure,
        minute=9,
        resource_id="TUG-3",
        duration_min=14,
        payload={
            "affected_gates": ["A3", "A4", "R1"],
            "zone": "Taxiway C",
        },
    )
    scenario.events.append(event)
    for gate in scenario.gates:
        if gate.id in event.payload["affected_gates"]:
            gate.available_minute = max(gate.available_minute, 23)

    first = attribute_recovery_burden(
        scenario,
        _reference(scenario),
        runs_per_coalition=3,
    )
    second = attribute_recovery_burden(
        scenario,
        _reference(scenario),
        runs_per_coalition=3,
    )

    assert first["status"] == "completed"
    assert first["factor_count"] == 3
    assert first["coalition_count"] == 8
    assert {row["id"] for row in first["factors"]} == {
        "weather_runway",
        "gate_pressure",
        "dynamic_incident",
    }
    assert first["additivity"]["passed"] is True
    assert first["total_burden"] == second["total_burden"]
    assert first["factors"] == second["factors"]
    assert first["coalitions"] == second["coalitions"]
    assert first["passenger_denominator"] > 0

