from stormshift_v2.passenger_rescue import optimize_passenger_rescue
from stormshift_v2.risk_shield import assess_conformal_risk
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.schemas import PlanProfile
from stormshift_v2.simulation import generate_ranked_candidates


def _plan(seed: int = 2071):
    scenario = storm_scenario(seed=seed)
    plans, failure = generate_ranked_candidates(
        scenario,
        [PlanProfile.balanced],
        runs=20,
    )
    assert failure is None and plans
    plan = plans[0]
    plan.risk_shield = assess_conformal_risk(scenario, plan)
    return scenario, plan


def test_simulation_exposes_per_flight_missed_connection_risk():
    _, plan = _plan()

    expected = sum(
        row["expected_missed_passengers"]
        for row in plan.report.per_flight.values()
    )

    assert abs(expected - plan.report.missed_connections) <= 0.5
    assert all(
        0 <= row["miss_probability"] <= 1
        for row in plan.report.per_flight.values()
    )


def test_min_cost_flow_conserves_passengers_capacity_and_connection_windows():
    scenario, plan = _plan(seed=2072)

    rescue = optimize_passenger_rescue(scenario, plan)

    assert rescue["at_risk_passengers"] == plan.report.missed_connections
    assert (
        rescue["reaccommodated_passengers"] + rescue["remaining_stranded"]
        == rescue["at_risk_passengers"]
    )
    assert all(rescue["constraints"].values())
    assert all(
        option["allocated"] <= option["capacity"]
        for option in rescue["options"]
    )
    group_by_flight = {
        group["flight_id"]: group for group in rescue["groups"]
    }
    assert all(
        row["departure_minute"]
        >= group_by_flight[row["from_flight"]]["minimum_rebook_departure"]
        for row in rescue["flows"]
    )
    assert rescue == optimize_passenger_rescue(scenario, plan)


def test_capacity_shortfall_is_explicit_and_never_fabricates_seats():
    scenario, plan = _plan(seed=2073)
    plan.report.missed_connections = 300

    rescue = optimize_passenger_rescue(scenario, plan)

    assert rescue["status"] == "capacity_shortfall"
    assert rescue["remaining_stranded"] > 0
    assert rescue["counterfactual"]["stranding_avoided"] == (
        rescue["reaccommodated_passengers"]
    )
    assert rescue["constraints"]["seat_capacity"] is True

