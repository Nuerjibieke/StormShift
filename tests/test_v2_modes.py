import asyncio
from dataclasses import replace

import pytest

from stormshift_v2.config import settings
from stormshift_v2.optimizer import validate_assignments
from stormshift_v2.schemas import (
    DataMode,
    ExecutionAdvanceRequest,
    EventRequest,
    EventSpec,
    EventType,
    Mode,
    ObservationRequest,
    PlanProfile,
    PlanRequest,
    SandboxUpdate,
    SessionCreate,
)
from stormshift_v2.service import SessionService
from stormshift_v2.store import SessionStore
from stormshift_v2.stream import StreamManager


def _service(tmp_path) -> SessionService:
    return SessionService(
        config=replace(settings, data_dir=tmp_path, llm_mode="off"),
        store=SessionStore(tmp_path / "modes.db"),
        streams=StreamManager(),
    )

async def _approve_with_required_evidence(service, state):
    for _ in range(3):
        selected = state.plans[0]
        gate = state.decision_lab.get("approval_gates", {}).get(selected.id, {})
        if gate.get("status") != "needs_evidence":
            return await service.approve(state.id, selected.id)
        required = gate.get("required_query_ids", [])
        assert required, gate
        await service.acquire_observation(
            state.id,
            ObservationRequest(query_id=required[0]),
        )
        state = await service.plan(state.id, PlanRequest(replan=True))
    raise AssertionError("approval gate did not converge after required evidence")



async def _finish_execution(service, approval, request_prefix):
    state = approval.session
    events = list(approval.execution_events)
    for step in range(8):
        if state.phase.value in {"recovered", "infeasible"}:
            break
        if state.phase.value == "awaiting_approval":
            approval = await _approve_with_required_evidence(service, state)
            state = approval.session
            events.extend(approval.execution_events)
        advanced = await service.advance_execution(
            state.id,
            ExecutionAdvanceRequest(
                target_minute=480,
                expected_revision=state.execution.revision,
                request_id=f"{request_prefix}-{step}",
            ),
        )
        state = advanced.session
        events.extend(advanced.execution_events)
    return state, events


@pytest.mark.parametrize("scenario_id", ["storm", "vhhh_storm", "gate_outage", "medical"])
def test_scenario_library_runs_end_to_end(tmp_path, scenario_id):
    service = _service(tmp_path)

    async def exercise():
        state = await service.create(
            SessionCreate(
                mode=Mode.scenarios,
                scenario_id=scenario_id,
                data_mode=DataMode.synthetic,
                seed=2030,
            )
        )
        if scenario_id == "vhhh_storm":
            assert state.scenario.airport_icao == "VHHH"
            assert [runway.id for runway in state.scenario.runways] == ["07L", "07C", "07R"]
            assert state.scenario.layout is not None
            assert state.scenario.layout.source_type == "public_aip_simplified"
            assert state.scenario.layout.effective_date == "2026-09-03"
        planned = await service.plan(
            state.id,
            PlanRequest(
                profiles=[
                    PlanProfile.balanced,
                    PlanProfile.passenger,
                    PlanProfile.capacity,
                    PlanProfile.green,
                ]
            ),
        )
        assert planned.plans
        assert planned.phase.value == "awaiting_approval"
        assert planned.decision_lab["robustness"]["total_worlds"] == 3
        pareto = planned.decision_lab["pareto"]
        assert pareto["method"] == "conformal-risk-tier + epsilon-pareto + weighted-ideal-point"
        assert pareto["recommended_id"] == planned.plans[0].id
        assert pareto["recommended_id"] in pareto["frontier_ids"]
        assert len(pareto["points"]) == len(planned.plans)
        bargaining = planned.decision_lab["bargaining"]
        assert bargaining["method"] == "equal-weight Nash social welfare over CP-SAT candidates"
        assert bargaining["recommended_id"] in {plan.id for plan in planned.plans}
        assert {item["id"] for item in bargaining["candidates"]} == {
            plan.id for plan in planned.plans
        }
        assert any(trace.agent == "Robustness Agent" for trace in planned.traces)
        falsification = planned.decision_lab["falsification"]
        assert falsification["status"] == "completed"
        assert falsification["plan_id"] == planned.plans[0].id
        assert falsification["categories_tested"] == 4
        assert set(planned.decision_lab["falsification_by_plan"]) == {
            plan.id for plan in planned.plans
        }
        assert all(
            analysis["plan_id"] == plan_id
            for plan_id, analysis in planned.decision_lab["falsification_by_plan"].items()
        )
        assert any(trace.agent == "Falsification Agent" for trace in planned.traces)
        assert any(
            trace.agent == "Stakeholder Bargaining Mediator"
            for trace in planned.traces
        )
        assert planned.decision_lab["counterfactuals"][0]["source"] == "30-run-simpy-baseline"
        for candidate in planned.plans:
            proof = validate_assignments(
                planned.scenario,
                candidate.assignments,
                planned.intent,
            )
            assert not any(item.startswith("FAIL") for item in proof)

        approval = await _approve_with_required_evidence(service, planned)
        assert approval.session.phase.value == "executing"
        assert not any(event.type == "execution_tick" for event in approval.execution_events)
        final, execution_events = await _finish_execution(
            service, approval, f"finish-{scenario_id}"
        )
        assert final.phase.value == "recovered"
        assert any(event.type == "recovered" for event in execution_events)
        runway_reopenings = [
            event for event in execution_events
            if event.type == "runway_reopened"
        ]
        if scenario_id in {"storm", "vhhh_storm"}:
            reopened = next(
                event for event in runway_reopenings
                if event.payload["resource_id"] == "07R"
            )
            assert reopened.sim_minute == 24
            landings_on_07r = [
                event for event in execution_events
                if event.type == "execution_tick" and event.payload["runway"] == "07R"
            ]
            assert all(
                event.sim_minute >= reopened.sim_minute
                for event in landings_on_07r
            )
        else:
            assert not runway_reopenings

    asyncio.run(exercise())


def test_sandbox_state_and_dynamic_event_replan_end_to_end(tmp_path):
    service = _service(tmp_path)

    async def exercise():
        state = await service.create(
            SessionCreate(
                mode=Mode.sandbox,
                scenario_id="sandbox",
                data_mode=DataMode.synthetic,
                seed=2031,
                sandbox_flights=20,
            )
        )
        state = await service.update_sandbox(
            state.id,
            SandboxUpdate(
                weather_severity=4,
                runway_closure_min=15,
                passenger_multiplier=1.3,
                fuel_margin_delta=10,
            ),
        )
        state = await service.inject_event(
            state.id,
            EventRequest(
                event=EventSpec(
                    type=EventType.gate_outage,
                    minute=8,
                    resource_id="A4",
                    severity=3,
                    duration_min=18,
                    payload={"source": "test_timeline"},
                )
            ),
        )
        assert state.scenario.current_minute == 0
        assert any(
            item["minute"] == 8 and item["resource_id"] == "A4"
            for item in state.data_snapshot["scheduled_events"]
        )
        assert any(
            gate.id == "A4" and gate.available_minute == 0
            for gate in state.scenario.gates
        )

        planned = await service.plan(
            state.id,
            PlanRequest(
                profiles=[
                    PlanProfile.balanced,
                    PlanProfile.passenger,
                    PlanProfile.capacity,
                    PlanProfile.green,
                ]
            ),
        )
        assert planned.plans
        assert len(planned.scenario.flights) == 20
        approval = await _approve_with_required_evidence(service, planned)
        final, execution_events = await _finish_execution(
            service, approval, "sandbox-finish"
        )
        assert final.phase.value == "recovered", final.decision_lab.get("infeasible")
        gate_event = next(
            event for event in execution_events if event.type == "gate_outage"
        )
        assert gate_event.sim_minute == 8

    asyncio.run(exercise())
