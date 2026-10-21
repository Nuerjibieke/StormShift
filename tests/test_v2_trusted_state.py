import asyncio
import copy
from dataclasses import replace

import stormshift_v2.online_execution as online_execution_module
import stormshift_v2.service as service_module
from stormshift_v2.config import settings
from stormshift_v2.schemas import (
    DataMode, ExecutionAdvanceRequest, EventSpec, EventType, Mode, PlanProfile, PlanRequest,
    SessionCreate,
)
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.service import SessionService
from stormshift_v2.store import SessionStore
from stormshift_v2.stream import StreamManager
from stormshift_v2.trusted_state import (
    apply_trusted_operational_state,
    build_trusted_operational_state,
    plan_evidence_basis,
)


def test_conflicted_and_stale_claims_are_audited_but_excluded_from_solver():
    scenario = storm_scenario()
    trusted = build_trusted_operational_state(scenario, DataMode.synthetic)
    compiled = apply_trusted_operational_state(scenario, trusted)

    a2_claims = [
        item for item in trusted["claims"]
        if item["key"] == "gate.A2.available_minute"
    ]
    stale_fuel = next(
        item for item in trusted["claims"]
        if item["id"] == "flight:SK218:fuel:flight-plan"
    )
    assert [(item["value"], item["status"], item["solver_eligible"]) for item in a2_claims] == [
        (15, "conflicted_excluded", False),
        (28, "confirmed", True),
    ]
    assert stale_fuel["status"] == "stale_excluded"
    assert stale_fuel["solver_eligible"] is False
    assert trusted["solver_input_values"]["gate.A2.available_minute"] == 28
    assert trusted["solver_input_values"]["flight.SK218.fuel_margin_min"] == 34
    assert compiled.gates[1].available_minute == 28
    assert trusted["quality"]["detected_conflicts"] == 1
    assert trusted["quality"]["resolved_conflicts"] == 1
    assert trusted["quality"]["stale_excluded"] == 1
    assert trusted["quality"]["unresolved_critical"] == 0
    argumentation = trusted["argumentation"]
    assert argumentation["status"] == "resolved"
    assert argumentation["conflict_free"] is True
    assert argumentation["attack_count"] == 2
    assert len(argumentation["accepted_ids"]) == 29
    assert len(argumentation["rejected_ids"]) == 2
    assert [item["action"] for item in trusted["resolution_log"]] == [
        "query", "confirm", "abstain", "abstain",
    ]
    assert len(trusted["digest"]) == 64


def test_failure_invalidates_changed_facts_before_constraints_are_recompiled():
    before_scenario = storm_scenario()
    before = build_trusted_operational_state(before_scenario, DataMode.synthetic)
    after_scenario = before_scenario.model_copy(deep=True)
    event = EventSpec(
        type=EventType.ground_vehicle_failure,
        minute=9,
        resource_id="TUG-3",
        duration_min=14,
        payload={"affected_gates": ["A3", "A4", "R1"]},
    )
    after_scenario.current_minute = event.minute
    for gate in after_scenario.gates:
        if gate.id in {"A3", "A4", "R1"}:
            gate.available_minute = max(gate.available_minute, 23)
    fuel_updates = []
    for flight in after_scenario.flights:
        if flight.state.value not in {"inbound", "approach", "holding"}:
            continue
        previous = flight.fuel_margin_min
        flight.fuel_margin_min = max(0, previous - 9)
        fuel_updates.append({
            "flight_id": flight.id,
            "before_min": previous,
            "after_min": flight.fuel_margin_min,
        })
    event.payload["fuel_clock"] = {"updates": fuel_updates}

    after = build_trusted_operational_state(
        after_scenario,
        DataMode.synthetic,
        previous=before,
        event=event,
    )

    changed_keys = {item["key"] for item in after["changed_facts"]}
    invalidated_ids = {item["id"] for item in after["invalidated_constraints"]}
    assert {
        "gate.A3.available_minute",
        "gate.A4.available_minute",
        "gate.R1.available_minute",
    }.issubset(changed_keys)
    assert {
        "gate-window:A3",
        "gate-window:A4",
        "gate-window:R1",
    }.issubset(invalidated_ids)
    assert any(item.startswith("fuel-deadline:") for item in invalidated_ids)
    assert all(
        item["status"] == "invalidated_then_recompiled"
        for item in after["invalidated_constraints"]
    )
    assert after["snapshot_minute"] == 9
    assert after["digest"] != before["digest"]
    assert "before CP-SAT" in after["summary"]


def test_plan_evidence_basis_is_compact_and_bound_to_snapshot_digest():
    trusted = build_trusted_operational_state(storm_scenario(), DataMode.synthetic)
    basis = plan_evidence_basis(trusted)

    assert basis == {
        "status": "ready",
        "trusted_state_digest": trusted["digest"],
        "snapshot_minute": 0,
        "confirmed_facts": 29,
        "resolved_conflicts": 1,
        "stale_claims_excluded": 1,
        "minimum_confidence": .94,
        "compiled_constraints": 18,
        "unresolved_critical": 0,
    }

def test_service_reconciles_evidence_before_solver_and_binds_plan_digest(tmp_path):
    service = SessionService(
        config=replace(settings, data_dir=tmp_path, llm_mode="off"),
        store=SessionStore(tmp_path / "trusted-state.db"),
        streams=StreamManager(),
    )

    async def exercise():
        state = await service.create(SessionCreate(
            mode=Mode.guided,
            scenario_id="storm",
            data_mode=DataMode.synthetic,
            seed=2060,
        ))
        planned = await service.plan(
            state.id,
            PlanRequest(profiles=[PlanProfile.balanced]),
        )

        trusted = planned.decision_lab["trusted_state"]
        assert trusted["status"] == "ready"
        assert planned.traces[0].agent == "Evidence Coordinator"
        assert planned.traces[1].agent == "Formal Argumentation Agent"
        assert planned.traces[2].agent == "Verifiable Constraint Compiler"
        assert planned.traces[3].agent in {
            "Weather Agent", "Runway Agent", "Gate & Ground Agent",
            "Airline & Passenger Agent",
        }
        assert planned.plans[0].evidence_basis["trusted_state_digest"] == trusted["digest"]
        assert planned.plans[0].evidence_basis["unresolved_critical"] == 0
        event_types = [item.type for item in service.store.replay(planned.id)]
        assert event_types.index("trusted_state_compiled") < event_types.index("plan_generated")

    asyncio.run(exercise())


def test_service_abstains_without_calling_solver_when_arguments_are_undecided(
    tmp_path,
    monkeypatch,
):
    blocked = build_trusted_operational_state(storm_scenario(), DataMode.synthetic)
    blocked["status"] = "blocked"
    blocked["summary"] = "A critical runway claim remains undecided."
    blocked["quality"]["unresolved_critical"] = 1
    blocked["argumentation"]["status"] = "blocked"
    blocked["argumentation"]["conflict_free"] = False
    blocked["argumentation"]["unresolved_keys"] = ["runway.07L.status"]
    blocked["argumentation"]["summary"] = "Grounded extension cannot select either claim."

    service = SessionService(
        config=replace(settings, data_dir=tmp_path, llm_mode="off"),
        store=SessionStore(tmp_path / "argument-abstention.db"),
        streams=StreamManager(),
    )

    async def exercise():
        state = await service.create(SessionCreate(
            mode=Mode.guided,
            scenario_id="storm",
            data_mode=DataMode.synthetic,
            seed=2061,
        ))
        monkeypatch.setattr(
            service_module,
            "build_trusted_operational_state",
            lambda *args, **kwargs: blocked,
        )
        monkeypatch.setattr(
            online_execution_module,
            "build_trusted_operational_state",
            lambda *args, **kwargs: blocked,
        )
        monkeypatch.setattr(
            online_execution_module,
            "build_trusted_operational_state",
            lambda *args, **kwargs: blocked,
        )

        monkeypatch.setattr(
            online_execution_module,
            "build_trusted_operational_state",
            lambda *args, **kwargs: blocked,
        )

        def forbidden_solver(*args, **kwargs):
            raise AssertionError("CP-SAT workflow must not run with undecided critical facts")

        monkeypatch.setattr(service_module, "run_recovery_workflow", forbidden_solver)
        planned = await service.plan(
            state.id,
            PlanRequest(profiles=[PlanProfile.balanced]),
        )

        assert planned.phase.value == "infeasible"
        assert planned.plans == []
        assert planned.selected_plan_id is None
        assert planned.decision_lab["infeasible"]["solver_status"] == "NOT_RUN_EVIDENCE_BLOCKED"
        assert planned.decision_lab["infeasible"]["solver_calls"] == 0
        assert planned.traces[1].agent == "Formal Argumentation Agent"
        assert planned.traces[1].status == "failed"
        events = service.store.replay(planned.id)
        event = next(item for item in events if item.type == "planning_infeasible")
        assert event.payload["solver_calls"] == 0
        assert event.payload["unresolved_fact_keys"] == ["runway.07L.status"]

    asyncio.run(exercise())


def test_guided_fault_replan_has_no_evidence_gate_bypass(tmp_path, monkeypatch):
    service = SessionService(
        config=replace(settings, data_dir=tmp_path, llm_mode="off"),
        store=SessionStore(tmp_path / "guided-argument-abstention.db"),
        streams=StreamManager(),
    )

    async def exercise():
        state = await service.create(SessionCreate(
            mode=Mode.guided,
            scenario_id="storm",
            data_mode=DataMode.synthetic,
            seed=2062,
        ))
        planned = await service.plan(
            state.id,
            PlanRequest(profiles=[PlanProfile.balanced]),
        )
        blocked = copy.deepcopy(planned.decision_lab["trusted_state"])
        blocked["status"] = "blocked"
        blocked["summary"] = "A critical runway claim remains undecided."
        blocked["quality"]["unresolved_critical"] = 1
        blocked["argumentation"]["status"] = "blocked"
        blocked["argumentation"]["conflict_free"] = False
        blocked["argumentation"]["unresolved_keys"] = ["runway.07L.status"]
        blocked["argumentation"]["summary"] = "Grounded extension cannot select either claim."
        monkeypatch.setattr(
            service_module,
            "build_trusted_operational_state",
            lambda *args, **kwargs: blocked,
        )
        monkeypatch.setattr(
            online_execution_module,
            "build_trusted_operational_state",
            lambda *args, **kwargs: blocked,
        )

        def forbidden_solver(*args, **kwargs):
            raise AssertionError("guided automatic replan bypassed evidence abstention")

        monkeypatch.setattr(service_module, "run_recovery_workflow", forbidden_solver)
        approval = await service.approve(planned.id, planned.plans[0].id)
        response = await service.advance_execution(
            planned.id,
            ExecutionAdvanceRequest(
                target_minute=9,
                expected_revision=approval.session.execution.revision,
                request_id="evidence-blocked-at-t9",
            ),
        )

        assert response.session.phase.value == "infeasible"
        failure = next(
            item for item in response.execution_events
            if item.type == "automatic_policy_replan_failed"
        )
        assert failure.payload["solver_status"] == "NOT_RUN_EVIDENCE_BLOCKED"
        assert failure.payload["solver_calls"] == 0
        assert failure.payload["switch_executed"] is False
        assert failure.payload["safety_gate"] == "evidence_abstention"
        assert response.session.decision_lab["infeasible"]["solver_calls"] == 0

    asyncio.run(exercise())


