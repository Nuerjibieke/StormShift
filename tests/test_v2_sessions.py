import asyncio
from dataclasses import replace

from stormshift_v2.config import settings
from stormshift_v2.optimizer import solve_candidate
from stormshift_v2.schemas import (
    AgentTrace,
    DataMode,
    Mode,
    Phase,
    PlanProfile,
    SessionState,
)
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.service import SessionService
from stormshift_v2.store import SessionStore
from stormshift_v2.stream import StreamManager


def test_clone_preserves_a_persisted_decision_snapshot(tmp_path):
    scenario = storm_scenario()
    plan, failure = solve_candidate(scenario, PlanProfile.balanced)
    assert failure is None and plan is not None
    plan.rank = 1

    store = SessionStore(tmp_path / "sessions.db")
    service = SessionService(
        config=replace(settings, data_dir=tmp_path),
        store=store,
        streams=StreamManager(),
    )
    source = SessionState(
        mode=Mode.guided,
        data_mode=DataMode.synthetic,
        scenario=scenario,
        phase=Phase.awaiting_approval,
        plans=[plan],
        selected_plan_id=plan.id,
        traces=[
            AgentTrace(
                agent="Safety Critic",
                role="safety",
                status="completed",
                summary="Hard constraints verified.",
                tool="validate_assignments",
            )
        ],
        llm_tier="fast",
        decision_lab={"explanation": {"summary": "Auditable snapshot"}},
    )
    store.save_session(source)
    store.save_plan(source.id, plan)

    cloned = asyncio.run(service.clone(source.id))
    persisted = store.get_session(cloned.id)

    assert persisted is not None
    assert cloned.id != source.id
    assert persisted.phase == Phase.awaiting_approval
    assert persisted.selected_plan_id == plan.id
    assert [item.id for item in persisted.plans] == [plan.id]
    assert persisted.traces[0].agent == "Safety Critic"
    assert persisted.llm_tier == "fast"
    assert persisted.decision_lab["explanation"] == source.decision_lab["explanation"]
    integrity = persisted.decision_lab["event_integrity"]
    assert integrity["status"] == "verified"
    assert integrity["event_count"] == 1

    events = store.replay(cloned.id)
    assert len(events) == 1
    assert events[0].type == "session_cloned"
    assert events[0].payload["source_session_id"] == source.id
