import asyncio
from dataclasses import replace

from stormshift_v2.config import settings
from stormshift_v2.information_value import recommend_observation
from stormshift_v2.robustness import evaluate_adaptive_horizon, evaluate_candidate_regret
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.schemas import (
    DataMode,
    Mode,
    ObservationRequest,
    Phase,
    PlanProfile,
    PlanRequest,
    SessionCreate,
)
from stormshift_v2.service import SessionService
from stormshift_v2.simulation import generate_ranked_candidates
from stormshift_v2.store import SessionStore
from stormshift_v2.stream import StreamManager


def test_value_of_information_is_explicit_and_never_generates_actions():
    scenario = storm_scenario()
    plans, failure = generate_ranked_candidates(
        scenario,
        [
            PlanProfile.balanced,
            PlanProfile.passenger,
            PlanProfile.resilient,
            PlanProfile.green,
        ],
        runs=12,
    )
    assert failure is None
    robustness = evaluate_adaptive_horizon(scenario, plans[0], runs=4)
    regret = evaluate_candidate_regret(scenario, plans, runs=4)

    result = recommend_observation(scenario, plans[0], robustness, regret)

    assert result["method"].startswith("myopic value-of-perfect-information")
    assert sum(result["assumed_world_priors"].values()) == 1
    assert result["recommended_query_id"] in {
        "weather_reopen_nowcast",
        "critical_gate_readiness",
    }
    assert len(result["queries"]) == 2
    assert any(item["safety_relevant"] for item in result["queries"])
    assert all("assignments" not in item and "actions" not in item for item in result["queries"])
    assert "CP-SAT" in result["caveat"]


def test_active_observation_updates_evidence_and_replans(tmp_path):
    async def run():
        config = replace(
            settings,
            data_dir=tmp_path,
            cache_dir=tmp_path / "cache",
            llm_mode="off",
            voice_mode="off",
        )
        service = SessionService(
            config=config,
            store=SessionStore(tmp_path / "sessions.db"),
            streams=StreamManager(),
        )
        state = await service.create(SessionCreate(
            mode=Mode.guided,
            scenario_id="storm",
            data_mode=DataMode.synthetic,
            seed=2026,
        ))
        planned = await service.plan(state.id, PlanRequest())
        information = planned.decision_lab["information_value"]
        query_id = information["recommended_query_id"]

        observed = await service.acquire_observation(
            state.id,
            ObservationRequest(query_id=query_id),
        )

        assert observed.phase == Phase.incident
        assert observed.plans == []
        assert query_id in observed.data_snapshot["resolved_observation_ids"]
        assert observed.data_snapshot["active_observations"][-1]["query_id"] == query_id
        assert service.replay(state.id)[-1].type == "active_observation_acquired"

        replanned = await service.plan(state.id, PlanRequest(replan=True))
        refreshed = replanned.decision_lab["information_value"]
        resolved_row = next(
            item for item in refreshed["queries"] if item["id"] == query_id
        )

        assert replanned.phase == Phase.awaiting_approval
        assert replanned.plans
        assert resolved_row["resolved"] is True
        assert query_id in refreshed["resolved_query_ids"]
        assert refreshed["recommended_query_id"] != query_id
        assert any(trace.agent == "Active Sensing Advisor" for trace in replanned.traces)

    asyncio.run(run())
