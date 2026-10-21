from stormshift_v2.schemas import OperatorIntent, PlanCandidate, PlanProfile, SimulationReport
from stormshift_v2.simulation import pareto_summary, rank_pareto_candidates, select_representative_candidates


def _plan(
    plan_id: str,
    title: str,
    p90: float,
    missed: int,
    fuel: float,
    emissions: float,
    score: float,
) -> PlanCandidate:
    return PlanCandidate(
        id=plan_id,
        profile=PlanProfile.balanced,
        title=title,
        assignments=[],
        actions=[],
        report=SimulationReport(
            runs=10,
            mean_delay_min=p90 - 2,
            p90_delay_min=p90,
            cvar_delay_min=p90 + 2,
            missed_connections=missed,
            fuel_risk_probability=fuel,
            remaining_conflicts=0,
            emissions_index=emissions,
            recovery_minute=40,
            confidence_low=p90 - 3,
            confidence_high=p90 - 1,
        ),
        score=score,
    )


def test_epsilon_pareto_rejects_a_superficially_low_score_dominated_plan():
    balanced = _plan("balanced", "均衡", 20, 20, .02, 100, 12)
    passenger = _plan("passenger", "旅客", 24, 5, .02, 110, 14)
    dominated = _plan("dominated", "伪低分", 26, 25, .03, 120, 1)

    ranked = rank_pareto_candidates([balanced, passenger, dominated])
    evidence = pareto_summary(ranked)

    assert ranked[0].pareto_optimal is True
    assert ranked[0].id != dominated.id
    assert dominated.pareto_optimal is False
    assert set(evidence["frontier_ids"]) == {"balanced", "passenger"}
    assert evidence["recommended_id"] == ranked[0].id


def test_ideal_point_distance_is_deterministic_and_bounded():
    candidates = [
        _plan("delay", "延误优先", 18, 22, .02, 108, 10),
        _plan("connections", "转机优先", 23, 4, .02, 114, 11),
        _plan("green", "低碳", 22, 15, .02, 92, 12),
    ]

    first = rank_pareto_candidates([item.model_copy(deep=True) for item in candidates])
    second = rank_pareto_candidates([item.model_copy(deep=True) for item in candidates])

    assert [(item.id, item.ideal_distance) for item in first] == [
        (item.id, item.ideal_distance) for item in second
    ]
    assert all(0 <= item.ideal_distance <= 1 for item in first)


def test_representative_subset_preserves_passenger_and_emissions_extremes():
    ranked = [
        _plan("knee", "膝点", 20, 10, .02, 100, 1),
        _plan("near", "近邻", 19, 12, .02, 105, 2),
        _plan("passenger", "转机极值", 24, 2, .02, 115, 3),
        _plan("green", "排放极值", 23, 9, .02, 80, 4),
    ]

    selected = select_representative_candidates(ranked, limit=3)
    assert [item.id for item in selected] == ["knee", "passenger", "green"]


def test_confirmed_outcome_contract_precedes_ideal_point_tie_breaking():
    fast = _plan("fast", "低延误但错失较多", 17, 10, .01, 100, 8)
    protected = _plan("protected", "保护转机", 22, 2, .01, 112, 11)
    for candidate in (fast, protected):
        candidate.risk_shield = {
            "status": "clear",
            "rows": [{"transfer_passengers": 100}],
            "connection_passengers_exposed": 10,
        }
    intent = OperatorIntent(
        raw_text="转机成功率至少95%，平均错失转机不超过5人",
        minimum_connection_rate=.95,
        maximum_missed_connections=5,
        needs_confirmation=False,
    )

    ranked = rank_pareto_candidates([fast, protected], intent)

    assert ranked[0].id == "protected"


def test_execution_permission_precedes_global_pareto_dominance():
    globally_dominant = _plan("fast-risky", "快但需人工", 18, 2, .01, 90, 4)
    executable = _plan("safe-tier", "可执行稳健", 23, 8, .02, 110, 10)
    globally_dominant.risk_shield = {"status": "escalate"}
    executable.risk_shield = {"status": "watch"}

    ranked = rank_pareto_candidates([globally_dominant, executable])

    assert globally_dominant.pareto_optimal is True
    assert executable.pareto_optimal is False
    assert ranked[0].id == "safe-tier"
