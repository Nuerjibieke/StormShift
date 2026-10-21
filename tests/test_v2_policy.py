import asyncio
from dataclasses import replace

from stormshift_v2.config import settings
from stormshift_v2.optimizer import solve_candidate, validate_assignments
from stormshift_v2.policy import (
    compile_contingency_policy,
    evaluate_policy_event,
)
from stormshift_v2.schemas import (
    DataMode,
    ExecutionAdvanceRequest,
    EventRequest,
    EventSpec,
    EventType,
    Mode,
    Phase,
    PlanProfile,
    PlanRequest,
    SessionCreate,
)
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.store import SessionStore
from stormshift_v2.stream import StreamManager
from stormshift_v2.service import SessionService


def _policy():
    scenario = storm_scenario(seed=2042)
    plan, failure = solve_candidate(scenario, PlanProfile.balanced)
    assert failure is None and plan is not None
    regret = {
        "method": "test-minimax-regret",
        "recommended_id": plan.id,
        "candidates": [{"id": plan.id, "title": plan.title}],
    }
    policy = compile_contingency_policy(
        scenario,
        plan,
        {"total_worlds": 3},
        regret,
    )
    return scenario, plan, policy


def test_policy_compiles_three_auditable_solver_branches():
    _, plan, policy = _policy()

    assert policy["source_plan_id"] == plan.id
    assert policy["branch_count"] == 3
    assert policy["approved"] is False
    assert policy["evidence"]["hard_safety_gate"] == "mandatory"
    assert policy["evidence"]["prepared_branches"] == 0
    assert all(branch["prepared_response"]["status"] == "solve_on_trigger" for branch in policy["branches"])
    assert {
        branch["action"] for branch in policy["branches"]
    } == {"global_replan", "causal_local_repair", "priority_replan"}
    assert all("CP-SAT" in branch["solver"] for branch in policy["branches"])


def test_runtime_assurance_matches_storm_and_ground_failure():
    scenario, plan, policy = _policy()
    policy["approved"] = True

    storm = evaluate_policy_event(
        policy,
        EventSpec(
            type=EventType.storm,
            minute=10,
            severity=5,
            duration_min=20,
        ),
    )
    assigned_gate = plan.assignments[0].gate
    ground = evaluate_policy_event(
        policy,
        EventSpec(
            type=EventType.ground_vehicle_failure,
            minute=9,
            resource_id="TUG-X",
            severity=3,
            duration_min=12,
            payload={"affected_gates": [assigned_gate]},
        ),
    )

    assert storm["matched"] is True
    assert ground["prepared_response"]["status"] == "solve_on_trigger"
    assert ground["prepared_response"]["assignments"] == []
    assert storm["action"] == "global_replan"
    assert ground["matched"] is True
    assert ground["action"] == "causal_local_repair"
    assert ground["matched_resource"] == assigned_gate
    assert ground["approved_envelope"] is True
    assert scenario.id


def test_unrelated_gate_event_keeps_the_approved_plan_under_monitoring():
    _, _, policy = _policy()
    decision = evaluate_policy_event(
        policy,
        EventSpec(
            type=EventType.gate_outage,
            minute=7,
            resource_id="UNUSED-GATE",
            severity=2,
            duration_min=10,
        ),
    )

    assert decision["matched"] is False
    assert decision["action"] == "continue_and_monitor"
    assert decision["solver"] == "none"


def test_approved_policy_automatically_replans_a_dynamic_gate_failure(tmp_path):
    service = SessionService(
        config=replace(settings, data_dir=tmp_path, llm_mode="off"),
        store=SessionStore(tmp_path / "policy-runtime.db"),
        streams=StreamManager(),
    )

    async def exercise():
        state = await service.create(
            SessionCreate(
                mode=Mode.sandbox,
                scenario_id="sandbox",
                data_mode=DataMode.synthetic,
                seed=2043,
                sandbox_flights=10,
            )
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
        approved = await service.approve(planned.id, planned.plans[0].id)
        assert approved.session.phase == Phase.executing
        assert approved.session.decision_lab["policy"]["approved"] is True
        previous_plan_id = approved.session.selected_plan_id
        selected = next(
            plan
            for plan in approved.session.plans
            if plan.id == previous_plan_id
        )
        failed_gate = selected.assignments[0].gate

        queued = await service.inject_event(
            state.id,
            EventRequest(
                event=EventSpec(
                    type=EventType.gate_outage,
                    minute=1,
                    resource_id=failed_gate,
                    severity=3,
                    duration_min=18,
                    payload={"source": "runtime_test"},
                )
            ),
        )
        assert queued.scenario.current_minute == 0
        advanced = await service.advance_execution(
            state.id,
            ExecutionAdvanceRequest(
                target_minute=1,
                expected_revision=queued.execution.revision,
                request_id="policy-gate-failure-at-t1",
            ),
        )
        recovered = advanced.session

        assert recovered.phase == Phase.monitoring
        assert recovered.decision_lab["policy_runtime"]["warm_start_flights"] > 0
        assert recovered.selected_plan_id != previous_plan_id
        assert recovered.decision_lab["policy_runtime"]["matched"] is True
        assert (
            recovered.decision_lab["policy_runtime"]["action"]
            == "causal_local_repair"
        )
        assert recovered.decision_lab["recourse"]["preserved_verified"] is True
        assert recovered.decision_lab["policy"]["approved"] is True
        selected_after = next(
            plan
            for plan in recovered.plans
            if plan.id == recovered.selected_plan_id
        )
        proof = validate_assignments(
            recovered.scenario,
            selected_after.assignments,
            recovered.intent,
        )
        assert not any(item.startswith("FAIL") for item in proof)
        assert any(
            "warm-start" in item
            for item in selected_after.constraint_proof
        )
        replay = service.replay(recovered.id)
        event_types = [event.type for event in replay]
        falsification = recovered.decision_lab["falsification"]
        assert falsification["status"] == "completed"
        assert falsification["plan_id"] == selected_after.id
        assert recovered.decision_lab["falsification_by_plan"][
            selected_after.id
        ] == falsification
        assert event_types.index("gate_outage") < event_types.index(
            "trusted_state_reconciled"
        ) < event_types.index("automatic_policy_replan")
        trusted = recovered.decision_lab["trusted_state"]
        assert trusted["changed_facts"]
        assert trusted["invalidated_constraints"]
        assert selected_after.evidence_basis["trusted_state_digest"] == trusted["digest"]
        auto = next(
            event
            for event in replay
            if event.type == "automatic_policy_replan"
        )
        assert auto.payload["safety_gate"] == "passed"
        assert auto.payload["strategy"] == "causal_local_repair"
        shadow = recovered.decision_lab["shadow_twin"]
        assert shadow["status"] == "mandatory_switch"
        assert shadow["decision"] == "switch"
        assert shadow["hard_gate"]["incumbent_operationally_valid"] is False
        assert auto.payload["shadow_twin"] == shadow
        assert auto.payload["switch_executed"] is True
        twin_trust = recovered.decision_lab["twin_trust"]
        assert twin_trust["status"] in {"trusted", "watch"}
        assert twin_trust["blocks_unattended_execution"] is False
        assert auto.payload["twin_trust"] == twin_trust["status"]
        assert any(trace.agent == "Twin Trust Sentinel" for trace in recovered.traces)

    asyncio.run(exercise())
