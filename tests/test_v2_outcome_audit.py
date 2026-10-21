from stormshift_v2.optimizer import solve_candidate
from stormshift_v2.outcome_audit import build_outcome_audit
from stormshift_v2.schemas import ObjectiveWeights, OperatorIntent, PlanProfile
from stormshift_v2.scenarios import storm_scenario


def test_outcome_audit_uses_candidate_scope_as_passenger_denominator():
    scenario = storm_scenario()
    plan, failure = solve_candidate(scenario, PlanProfile.balanced)
    assert failure is None and plan is not None

    plan.report.missed_connections = 17
    plan.report.remaining_conflicts = 2
    plan.report.mean_delay_min = 19.5
    plan.report.method_version = "entity-world-v1"
    plan.report.runs = 50
    plan.report.raw_metrics = {"mean_delay_min": 19.5, "missed_connections": 17.0,
                               "remaining_conflicts": 2.0}
    plan.risk_shield = {
        "status": "watch",
        "fuel_escalations": 0,
        "connection_passengers_exposed": 31,
        "blocks_unattended_execution": False,
    }
    audit = build_outcome_audit(scenario, plan)

    flights = {flight.id: flight for flight in scenario.flights}
    expected_scope = sum(flights[item.flight_id].connections for item in plan.assignments)
    assert audit["passengers"]["scenario_total"] == sum(
        flight.connections for flight in scenario.flights
    )
    assert audit["passengers"]["decision_scope"] == expected_scope
    assert audit["passengers"]["predicted_on_time"] == expected_scope - 17
    assert audit["passengers"]["out_of_scope"] == (
        audit["passengers"]["scenario_total"] - expected_scope
    )
    assert audit["operations"]["remaining_queue_conflicts"] == 2
    assert audit["outcome_contract"]["source"] == "demo_default"
    assert audit["outcome_contract"]["status"] == "met_with_watch"
    assert audit["outcome_contract"]["passed"] == audit["outcome_contract"]["total"]
    assert audit["passengers"]["tail_risk_exposure_rate"] > 0
    assert audit["status"] == "passed_with_watch"


def test_outcome_audit_separates_operator_target_from_hard_safety():
    scenario = storm_scenario()
    plan, failure = solve_candidate(scenario, PlanProfile.balanced)
    assert failure is None and plan is not None

    plan.report.mean_delay_min = 24.0
    plan.report.missed_connections = 20
    plan.report.method_version = "entity-world-v1"
    plan.report.runs = 50
    plan.report.raw_metrics = {"mean_delay_min": 24.0, "missed_connections": 20.0,
                               "remaining_conflicts": 0.0}
    plan.risk_shield = {
        "status": "watch",
        "fuel_escalations": 0,
        "connection_passengers_exposed": 100,
        "blocks_unattended_execution": False,
    }
    intent = OperatorIntent(
        raw_text="平均延误不能超过 22 分钟",
        weights=ObjectiveWeights(),
        average_delay_cap=22,
        minimum_connection_rate=.98,
        maximum_missed_connections=5,
        maximum_tail_exposure_rate=.10,
        summary="平均延误上限 22 分钟",
        needs_confirmation=False,
    )
    audit = build_outcome_audit(scenario, plan, intent)

    assert audit["hard_constraints"]["all_passed"] is True
    assert audit["operator_checks"][0]["label"] == "仿真平均延误不超过 22 分钟"
    assert audit["operator_checks"][0]["actual"] == 24.0
    assert audit["operator_checks"][0]["passed"] is False
    checks = {item["id"]: item for item in audit["operator_checks"]}
    assert checks["minimum_connection_rate"]["passed"] is False
    assert checks["maximum_missed_connections"]["passed"] is False
    assert checks["maximum_tail_exposure_rate"]["passed"] is False
    assert audit["outcome_contract"]["source"] == "operator_plus_defaults"
    assert audit["outcome_contract"]["status"] == "target_missed"
    assert any(row["source"] == "operator" for row in audit["outcome_contract"]["rows"])
    assert audit["status"] == "passed_with_watch"
