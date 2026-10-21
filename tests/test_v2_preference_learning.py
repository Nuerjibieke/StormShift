import asyncio
from dataclasses import replace

import pytest

from stormshift_v2.config import settings
from stormshift_v2.preference_learning import learn_operator_preferences
from stormshift_v2.preference_learning import encode_preference_observation
from stormshift_v2.schemas import (
    DataMode,
    Mode,
    Phase,
    PlanProfile,
    PreferenceRequest,
    SessionState,
)
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.service import SessionService
from stormshift_v2.simulation import generate_ranked_candidates
from stormshift_v2.store import SessionStore
from stormshift_v2.stream import StreamManager


def _plans():
    scenario = storm_scenario()
    plans, failure = generate_ranked_candidates(
        scenario,
        [PlanProfile.balanced, PlanProfile.passenger, PlanProfile.green],
        runs=4,
    )
    assert failure is None and len(plans) >= 2
    return scenario, plans


def test_particle_posterior_is_deterministic_bounded_and_candidate_preserving():
    scenario, plans = _plans()

    first = learn_operator_preferences(scenario, plans)
    second = learn_operator_preferences(scenario, plans)

    assert first["method"].startswith("4096-particle Bayesian Bradley-Terry")
    assert first["recommended_id"] in {plan.id for plan in plans}
    assert first["query"] == second["query"]
    assert first["posterior"] == second["posterior"]
    assert sum(row["mean"] for row in first["posterior"]["weights"]) == pytest.approx(1, abs=1e-3)
    assert sum(row["optimal_probability"] for row in first["candidates"]) == pytest.approx(1, abs=1e-3)
    for row in first["posterior"]["weights"]:
        assert 0 <= row["low90"] <= row["mean"] <= row["high90"] <= 1
    assert first["query"]["expected_information_bits"] > 0


def test_answer_updates_posterior_and_never_repeats_the_same_pair():
    scenario, plans = _plans()
    initial = learn_operator_preferences(scenario, plans)
    query = initial["query"]
    comparison = {
        "left_plan_id": query["left_plan_id"],
        "right_plan_id": query["right_plan_id"],
        "preferred_plan_id": query["left_plan_id"],
    }

    updated = learn_operator_preferences(scenario, plans, comparisons=[comparison])

    before = {
        row["id"]: row["mean"] for row in initial["posterior"]["weights"]
    }
    after = {
        row["id"]: row["mean"] for row in updated["posterior"]["weights"]
    }
    assert updated["comparison_count"] == 1
    assert any(abs(after[key] - before[key]) > .005 for key in before)
    if updated["query"] is not None:
        old_pair = {query["left_plan_id"], query["right_plan_id"]}
        new_pair = {
            updated["query"]["left_plan_id"],
            updated["query"]["right_plan_id"],
        }
        assert new_pair != old_pair


def test_service_persists_preference_evidence_without_changing_safe_actions(tmp_path):
    scenario, plans = _plans()
    initial = learn_operator_preferences(scenario, plans)
    query = initial["query"]
    state = SessionState(
        mode=Mode.guided,
        data_mode=DataMode.synthetic,
        scenario=scenario,
        phase=Phase.awaiting_approval,
        plans=plans,
        decision_lab={"preference_learning": initial},
    )
    store = SessionStore(tmp_path / "sessions.db")
    service = SessionService(
        config=replace(settings, data_dir=tmp_path),
        store=store,
        streams=StreamManager(),
    )
    store.save_session(state)
    before = {
        plan.id: [item.model_dump() for item in plan.assignments]
        for plan in plans
    }

    result = asyncio.run(service.submit_preference(
        state.id,
        PreferenceRequest(
            left_plan_id=query["left_plan_id"],
            right_plan_id=query["right_plan_id"],
            preferred_plan_id=query["left_plan_id"],
        ),
    ))

    after = {
        plan.id: [item.model_dump() for item in plan.assignments]
        for plan in result.plans
    }
    assert after == before
    assert result.decision_lab["preference_learning"]["comparison_count"] == 1
    assert result.traces[-1].agent == "Preference Elicitation Agent"
    events = store.replay(state.id)
    assert events[-1].type == "preference_feedback"
    assert events[-1].payload["hard_safety_rules_changed"] == 0
    assert store.verify_event_chain(state.id)["status"] == "verified"



def test_approval_records_human_override_of_learned_recommendation(tmp_path):
    scenario, plans = _plans()
    learned = learn_operator_preferences(scenario, plans)
    override = next(plan for plan in plans if plan.id != learned["recommended_id"])
    state = SessionState(
        mode=Mode.scenarios,
        data_mode=DataMode.synthetic,
        scenario=scenario,
        phase=Phase.awaiting_approval,
        plans=plans,
        decision_lab={"preference_learning": learned},
    )
    store = SessionStore(tmp_path / "sessions.db")
    service = SessionService(
        config=replace(settings, data_dir=tmp_path),
        store=store,
        streams=StreamManager(),
    )
    store.save_session(state)
    for plan in plans:
        store.save_plan(state.id, plan)

    response = asyncio.run(service.approve(state.id, override.id))

    preference = response.session.decision_lab["preference_learning"]
    assert preference["approved_plan_id"] == override.id
    assert preference["human_override"] is True
    assert "覆盖" in preference["approval_note"]
    approval = next(
        event for event in response.execution_events
        if event.type == "plan_approved"
    )
    assert approval.payload["preference_recommended_id"] == learned["recommended_id"]
    assert approval.payload["human_preference_override"] is True


def test_portable_cross_session_memory_is_revocable_and_never_changes_rules(tmp_path):
    scenario, plans = _plans()
    initial = learn_operator_preferences(scenario, plans)
    query = initial["query"]
    encoded = encode_preference_observation(
        plans,
        left_plan_id=query["left_plan_id"],
        right_plan_id=query["right_plan_id"],
        preferred_plan_id=query["left_plan_id"],
    )
    state = SessionState(
        mode=Mode.guided,
        data_mode=DataMode.synthetic,
        scenario=scenario,
        phase=Phase.awaiting_approval,
        plans=plans,
    )
    store = SessionStore(tmp_path / "sessions.db")
    store.save_session(state)
    observation_id = store.save_preference_observation(
        session_id=state.id,
        scenario_id=scenario.id,
        winner_vector=encoded["winner_vector"],
        loser_vector=encoded["loser_vector"],
        winner_title=encoded["winner_title"],
        loser_title=encoded["loser_title"],
        metadata={"objective_order": encoded["objective_order"]},
    )

    history = store.list_preference_observations(exclude_session_id="future-session")
    remembered = learn_operator_preferences(
        scenario,
        plans,
        historical_observations=history,
    )
    baseline = {row["id"]: row["mean"] for row in initial["posterior"]["weights"]}
    posterior = {row["id"]: row["mean"] for row in remembered["posterior"]["weights"]}
    assert remembered["historical_comparison_count"] == 1
    assert remembered["session_comparison_count"] == 0
    assert any(abs(posterior[key] - baseline[key]) > .005 for key in baseline)

    service = SessionService(
        config=replace(settings, data_dir=tmp_path),
        store=store,
        streams=StreamManager(),
    )
    reset = asyncio.run(service.reset_preference_memory())
    assert reset == {
        "status": "revoked",
        "revoked": 1,
        "hard_safety_rules_changed": 0,
        "message": reset["message"],
    }
    assert store.list_preference_observations() == []
    with store._connect() as db:
        audit = db.execute(
            "SELECT revoked FROM preference_observations WHERE id=?",
            (observation_id,),
        ).fetchone()
    assert audit["revoked"] == 1
