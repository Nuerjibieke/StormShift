import asyncio
from dataclasses import replace

from stormshift_v2.acdm import build_acdm_ledger
from stormshift_v2.config import settings
from stormshift_v2.optimizer import TURNAROUND_MINUTES, solve_candidate
from stormshift_v2.schemas import (
    DataMode,
    Mode,
    PlanProfile,
    PlanRequest,
    SessionCreate,
)
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.service import SessionService
from stormshift_v2.simulation import simulate_candidate
from stormshift_v2.store import SessionStore
from stormshift_v2.stream import StreamManager


def test_acdm_ledger_exposes_solver_lineage_without_fabricating_tsat():
    scenario = storm_scenario(seed=2050)
    plan, failure = solve_candidate(scenario, PlanProfile.balanced)
    assert failure is None and plan is not None
    plan.report = simulate_candidate(scenario, plan, runs=50)

    ledger = build_acdm_ledger(scenario, plan)
    records = {record["flight_id"]: record for record in ledger["records"]}

    assert ledger["full_acdm"] is False
    assert ledger["departure_sequencer_connected"] is False
    assert ledger["solver_contract"]["never_writes"] == [
        "TSAT", "weather observation", "hard safety policy",
    ]
    assert set(records) == {item.flight_id for item in plan.assignments}

    assignment = plan.assignments[0]
    flight = next(item for item in scenario.flights if item.id == assignment.flight_id)
    milestones = {
        item["code"]: item for item in records[assignment.flight_id]["milestones"]
    }
    assert milestones["STA"]["minute"] == flight.scheduled_arrival
    assert milestones["STA"]["role"] == "solver_input"
    assert milestones["ELDT"]["minute"] == assignment.landing_minute
    assert milestones["ELDT"]["role"] == "solver_output"
    assert milestones["EIBT"]["minute"] == (
        assignment.landing_minute + assignment.taxi_minutes
    )
    assert milestones["TOBT"]["minute"] == (
        milestones["EIBT"]["minute"] + TURNAROUND_MINUTES
    )
    assert milestones["TOBT"]["status"] == "exercise_target"
    assert milestones["TSAT"]["minute"] is None
    assert milestones["TSAT"]["role"] == "external_missing"
    uncertainty = milestones["ELDT"]["uncertainty"]
    assert uncertainty["method"] == "one-sided scenario-calibrated split conformal"
    assert uncertainty["coverage"] == .9
    assert uncertainty["calibration_samples"] == 25
    assert uncertainty["lower_minute"] == assignment.landing_minute
    assert uncertainty["upper_minute"] >= assignment.landing_minute
    assert milestones["TSAT"]["uncertainty"] is None
    assert ledger["uncertainty_calibration"]["calibration_samples"] == 25
    assert ledger["uncertainty_calibration"]["evaluation_samples"] == 25
    assert ledger["uncertainty_calibration"]["empirical_coverage"] >= .80
    assert ledger["uncertainty_calibration"]["calibration_health"] == "healthy"
    assert ledger["uncertainty_calibration"]["guarantee_scope"] == (
        "in-simulator exchangeability only"
    )


def test_session_planning_persists_candidate_and_committed_acdm_ledgers(tmp_path):
    service = SessionService(
        config=replace(settings, data_dir=tmp_path, llm_mode="off"),
        store=SessionStore(tmp_path / "acdm.db"),
        streams=StreamManager(),
    )

    async def exercise():
        state = await service.create(SessionCreate(
            mode=Mode.sandbox,
            scenario_id="sandbox",
            data_mode=DataMode.synthetic,
            seed=2051,
            sandbox_flights=6,
        ))
        planned = await service.plan(
            state.id,
            PlanRequest(profiles=[PlanProfile.balanced]),
        )
        candidate = planned.plans[0]
        assert planned.decision_lab["acdm"]["status"] == "candidate"
        assert planned.decision_lab["acdm"]["plan_id"] == candidate.id
        assert candidate.id in planned.decision_lab["acdm_by_plan"]
        assert any(
            trace.agent == "A-CDM Data Steward" for trace in planned.traces
        )

        approved = await service.approve(planned.id, candidate.id)
        ledger = approved.session.decision_lab["acdm"]
        assert ledger["status"] == "committed"
        assert ledger["plan_id"] == approved.session.selected_plan_id
        assert all(
            next(
                milestone
                for milestone in record["milestones"]
                if milestone["code"] == "ELDT"
            )["status"] == "committed_target"
            for record in ledger["records"]
        )

    asyncio.run(exercise())
