from stormshift_v2.agents import run_recovery_workflow
from stormshift_v2.infeasibility import explain_infeasibility
from stormshift_v2.optimizer import solve_candidate
from stormshift_v2.schemas import (
    FlightSpec,
    FlightState,
    GateSpec,
    Mode,
    ObjectiveWeights,
    OperatorIntent,
    PlanProfile,
    ResourceStatus,
    RunwaySpec,
    ScenarioSpec,
    WeatherSpec,
)


def one_flight_scenario(*, reopen: int = 20, fuel: int = 8) -> ScenarioSpec:
    return ScenarioSpec(
        id="infeasible-certificate",
        name="Exact infeasibility certificate",
        airport="Test Airport",
        mode=Mode.sandbox,
        seed=41,
        runways=[
            RunwaySpec(
                id="07L",
                status=ResourceStatus.closed,
                reopen_minute=reopen,
                separation_min=3,
            )
        ],
        gates=[GateSpec(id="A1", status=ResourceStatus.available, taxi_minutes=4)],
        flights=[
            FlightSpec(
                id="TS101",
                state=FlightState.inbound,
                scheduled_arrival=0,
                planned_gate="A1",
                connections=20,
                fuel_margin_min=fuel,
            )
        ],
        weather=WeatherSpec(severity=3, label="storm"),
    )


def test_solver_failure_returns_irreducible_runway_fuel_core():
    plan, failure = solve_candidate(
        one_flight_scenario(), PlanProfile.balanced
    )

    assert plan is None
    assert failure is not None
    assert failure.solver_status == "infeasible"
    assert failure.core_irreducible is True
    assert failure.hard_safety_relaxations_allowed == 0
    assert {item.id for item in failure.conflict_core} == {
        "runway:07L",
        "fuel:TS101",
    }
    assert all(not item.relaxable for item in failure.conflict_core)
    assert all(item.safety_class == "hard_safety" for item in failure.conflict_core)
    assert "不可约冲突集" in failure.summary


def test_operator_delay_target_is_the_only_relaxable_core_member():
    scenario = one_flight_scenario(reopen=10, fuel=60)
    intent = OperatorIntent(
        raw_text="平均延误不能超过0分钟",
        weights=ObjectiveWeights(),
        average_delay_cap=0,
        needs_confirmation=False,
    )

    plan, failure = solve_candidate(
        scenario, PlanProfile.operator, intent
    )

    assert plan is None
    assert failure is not None
    core = {item.id: item for item in failure.conflict_core}
    assert set(core) == {"runway:07L", "operator:average_delay"}
    assert core["runway:07L"].relaxable is False
    assert core["operator:average_delay"].relaxable is True
    assert failure.hard_safety_relaxations_allowed == 0


def test_feasible_mirror_does_not_invent_a_conflict_core():
    scenario = one_flight_scenario(reopen=4, fuel=30)
    evidence = explain_infeasibility(scenario)

    assert evidence["solver_status"] == "feasible"
    assert evidence["conflict_core"] == []
    assert evidence["hard_safety_relaxations_allowed"] == 0



def test_workflow_surfaces_a_feasibility_core_agent_trace():
    result = run_recovery_workflow(
        one_flight_scenario(),
        [PlanProfile.balanced],
        runs=2,
    )

    assert result["candidates"] == []
    assert result["failure"].core_irreducible is True
    trace = next(
        item for item in result["traces"]
        if item.agent == "Feasibility Core Analyst"
    )
    assert trace.status == "completed"

