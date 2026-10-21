from stormshift_v2.agent_arbitration import arbitrate_agent_proposals
from stormshift_v2.agents import run_recovery_workflow
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.schemas import PlanProfile


def _workflow():
    return run_recovery_workflow(
        storm_scenario(),
        [PlanProfile.balanced, PlanProfile.resilient],
        runs=12,
    )


def test_specialists_emit_structured_proposals_and_arbiter_routes_evidence():
    result = _workflow()

    assert len(result["proposals"]) == 4
    assert {item.agent for item in result["proposals"]} == {
        "Weather Agent",
        "Runway Agent",
        "Gate & Ground Agent",
        "Airline & Passenger Agent",
    }
    assert all(item.tool_evidence for item in result["proposals"])
    audit = result["arbitration"]
    assert audit["method"].startswith("typed cross-agent interface contracts")
    assert audit["proposal_count"] == 4
    assert audit["checks_total"] == 4
    weather_contract = next(
        item for item in audit["checks"] if item["id"] == "weather_to_runway"
    )
    assert weather_contract["status"] == "aligned"
    assert "未承载" in weather_contract["evidence"]
    assert audit["recommended_query_id"] is None
    assert audit["next_action"] == "human_approval"
    assert audit["hidden_reasoning"] is False
    assert any(
        item.agent == "Cross-Agent Contract Arbiter"
        for item in result["traces"]
    )


def test_resolved_observation_closes_the_weather_agent_issue():
    unresolved = run_recovery_workflow(
        storm_scenario(),
        [PlanProfile.balanced],
        runs=12,
    )
    resolved = run_recovery_workflow(
        storm_scenario(),
        [PlanProfile.balanced],
        runs=12,
        resolved_query_ids=["weather_reopen_nowcast"],
    )

    before = next(
        item for item in unresolved["arbitration"]["checks"]
        if item["id"] == "weather_to_runway"
    )
    after = next(
        item for item in resolved["arbitration"]["checks"]
        if item["id"] == "weather_to_runway"
    )
    assert before["status"] == "needs_evidence"
    assert before["query_id"] == "weather_reopen_nowcast"
    assert after["status"] == "resolved_watch"
    assert after["query_id"] is None
    assert resolved["arbitration"]["recommended_query_id"] != "weather_reopen_nowcast"

    # Risk facts remain true after observation; only the arbiter decides whether
    # another query is required. Do not repeat an already resolved instruction.
    for result in (unresolved, resolved):
        shield = result["candidates"][0].risk_shield
        assert shield["blocks_unattended_execution"] is True
        assert shield["runway_clearance"]["status"] == "conditional"
        assert "仍有未按预计开放的风险" in shield["summary"]
        assert "人工审批不替代执行时的跑道可用性复核" in shield["summary"]
        assert "必须先取得" not in shield["summary"]


def test_cross_agent_hard_contract_violation_blocks_execution():
    result = _workflow()
    broken = result["candidates"][0].model_copy(deep=True)
    broken.assignments[0].gate = "UNKNOWN"
    audit = arbitrate_agent_proposals(
        storm_scenario(),
        result["proposals"],
        broken,
    )

    gate_contract = next(
        item for item in audit["checks"]
        if item["id"] == "runway_to_gate"
    )
    assert gate_contract["status"] == "blocked"
    assert gate_contract["hard_block"] is True
    assert audit["status"] == "blocked"
    assert audit["next_action"] == "stop_and_repair"

