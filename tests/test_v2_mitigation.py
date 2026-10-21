from stormshift_v2.mitigation import (
    apply_intervention_outcome,
    optimize_intervention_portfolio,
)
from stormshift_v2.optimizer import solve_candidate
from stormshift_v2.passenger_rescue import optimize_passenger_rescue
from stormshift_v2.schemas import OperatorIntent, PlanProfile
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.simulation import simulate_candidate


OPERATIONAL_FIELDS = (
    "mean_delay_min",
    "p90_delay_min",
    "cvar_delay_min",
    "fuel_risk_probability",
    "remaining_conflicts",
    "emissions_index",
    "recovery_minute",
)


def _plan(severity: int = 4):
    scenario = storm_scenario()
    scenario.weather.severity = severity
    plan, failure = solve_candidate(scenario, PlanProfile.balanced)
    assert failure is None and plan is not None
    plan.report = simulate_candidate(scenario, plan, 30)
    return scenario, plan


def test_connection_bonus_changes_passenger_outcome_only():
    scenario, plan = _plan()
    flight_id = max(
        plan.assignments,
        key=lambda item: next(
            flight.connections
            for flight in scenario.flights
            if flight.id == item.flight_id
        ),
    ).flight_id
    baseline = simulate_candidate(scenario, plan, 30)
    treated = simulate_candidate(
        scenario,
        plan,
        30,
        connection_buffer_bonus={flight_id: 14},
    )

    assert treated.missed_connections <= baseline.missed_connections
    assert all(
        getattr(treated, field) == getattr(baseline, field)
        for field in OPERATIONAL_FIELDS
    )


def test_cp_sat_selects_minimal_safe_portfolio_and_freezes_assignments(monkeypatch):
    # Controlled tight-transfer incident, not a seed assumed to create distress.
    # The aircraft schedule is unchanged; both simulation and risk checker see
    # the same explicitly shortened eight-minute connection window.
    for module in ("stormshift_v2.simulation", "stormshift_v2.risk_shield"):
        monkeypatch.setattr(module + ".connection_buffer_minutes",
                            lambda seed, flight_id: 8)
    scenario, plan = _plan()
    original_assignments = [
        item.model_dump(mode="json") for item in plan.assignments
    ]
    original_missed = plan.report.missed_connections
    intent = OperatorIntent(
        raw_text="转机率至少99.5%，尾部风险不超过1%",
        minimum_connection_rate=.995,
        maximum_tail_exposure_rate=.01,
        needs_confirmation=False,
    )

    result = optimize_intervention_portfolio(
        scenario,
        plan,
        intent,
        runs=30,
    )

    assert result["status"] in {"target_met", "partial"}
    assert result["selected_actions"]
    assert result["baseline"]["tail_risk_exposed"] > 0
    assert result["mitigated"]["predicted_missed"] <= result["baseline"]["predicted_missed"]
    assert result["mitigated"]["tail_risk_exposed"] <= result["baseline"]["tail_risk_exposed"]
    assert result["proof"]["solver_status"] == "OPTIMAL"
    assert result["proof"]["paired_common_random_numbers"] is True
    assert result["proof"]["assignments_unchanged"] is True
    assert result["proof"]["operational_metrics_unchanged"] is True
    effective = apply_intervention_outcome(plan, result)
    rescue = optimize_passenger_rescue(scenario, effective)
    assert effective.report.missed_connections == result["mitigated"]["predicted_missed"]
    assert effective.risk_shield["connection_passengers_exposed"] == (
        result["mitigated"]["tail_risk_exposed"]
    )
    assert rescue["at_risk_passengers"] == result["mitigated"]["predicted_missed"]
    assert plan.report.missed_connections == original_missed
    assert [
        item.model_dump(mode="json") for item in plan.assignments
    ] == original_assignments
    assert all(item["used"] <= item["budget"] for item in result["resources"])
    selected_flights = [
        item["flight_id"] for item in result["selected_actions"]
    ]
    assert len(selected_flights) == len(set(selected_flights))


def test_default_guardrail_uses_no_resources_when_already_met():
    scenario, plan = _plan(severity=1)
    result = optimize_intervention_portfolio(scenario, plan, runs=20)

    assert result["status"] == "not_needed"
    assert result["selected_actions"] == []
    assert result["targets"]["source"] == "proactive_guardrail"
    assert all(item["used"] == 0 for item in result["resources"])
