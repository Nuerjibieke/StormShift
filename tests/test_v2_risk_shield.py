from stormshift_v2.risk_shield import assess_conformal_risk
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.schemas import PlanProfile, ResourceStatus
from stormshift_v2.simulation import generate_ranked_candidates, simulate_candidate
from stormshift_v2.optimizer import diagnose_infeasibility, solve_candidate


def test_two_stage_conformal_candidate_reduces_tail_fuel_exposure():
    scenario = storm_scenario()
    plans, failure = generate_ranked_candidates(
        scenario,
        [
            PlanProfile.balanced,
            PlanProfile.passenger,
            PlanProfile.resilient,
            PlanProfile.green,
        ],
        runs=50,
    )

    assert failure is None
    assert plans[0].profile == PlanProfile.resilient
    assert plans[0].risk_shield["status"] in {"clear", "watch"}
    assert plans[0].risk_shield["blocks_unattended_execution"] is False
    assert plans[0].risk_shield["two_stage_solver"].endswith("CP-SAT")
    assert plans[0].report.fuel_risk_probability <= .05
    assert plans[0].risk_shield["calibration_samples"] == 25
    assert plans[0].risk_shield["evaluation_samples"] == 25
    assert plans[0].risk_shield["empirical_coverage"] >= .80
    assert plans[0].risk_shield["calibration_health"] == "healthy"

    conventional = [plan for plan in plans if plan.profile != PlanProfile.resilient]
    assert conventional
    assert plans[0].report.fuel_risk_probability < min(
        plan.report.fuel_risk_probability for plan in conventional
    )
    for row in plans[0].risk_shield["rows"]:
        assert row["eldt_upper_90"] <= row["fuel_reserve_deadline"]


def test_risk_shield_escalates_a_nominal_plan_with_tail_reserve_violation():
    scenario = storm_scenario()
    candidate, failure = solve_candidate(scenario, PlanProfile.balanced)
    assert failure is None and candidate is not None
    candidate.report = simulate_candidate(scenario, candidate, runs=50)

    shield = assess_conformal_risk(scenario, candidate)

    assert shield["status"] == "escalate"
    assert shield["blocks_unattended_execution"] is True
    assert shield["fuel_escalations"] >= 1
    assert any(row["fuel_slack_min"] < 0 for row in shield["rows"])


def test_resilient_generation_is_deterministic_for_fixed_seed():
    scenario = storm_scenario()
    profiles = [PlanProfile.balanced, PlanProfile.resilient]
    left, _ = generate_ranked_candidates(scenario, profiles, runs=20)
    right, _ = generate_ranked_candidates(scenario, profiles, runs=20)

    def signature(plan):
        return [
            (item.flight_id, item.runway, item.landing_minute, item.gate)
            for item in plan.assignments
        ]

    assert left[0].profile == right[0].profile == PlanProfile.resilient
    assert signature(left[0]) == signature(right[0])
    assert left[0].risk_shield["rows"] == right[0].risk_shield["rows"]


def test_infeasibility_diagnostic_builds_relaxation_options():
    scenario = storm_scenario()
    for runway in scenario.runways:
        runway.status = ResourceStatus.closed
        runway.reopen_minute = None

    report = diagnose_infeasibility(scenario)

    assert report.options
    assert report.options[0].id == "declare_runway_recovery"

