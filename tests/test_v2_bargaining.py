from stormshift_v2.bargaining import nash_bargaining
from stormshift_v2.schemas import PlanProfile
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.simulation import generate_ranked_candidates


def test_nash_bargaining_is_bounded_and_candidate_preserving():
    plans, failure = generate_ranked_candidates(
        storm_scenario(),
        [PlanProfile.balanced, PlanProfile.passenger, PlanProfile.green],
        runs=8,
    )
    assert failure is None and len(plans) >= 2

    result = nash_bargaining(plans)

    assert result["status"] == "compromise_found"
    assert result["recommended_id"] in {plan.id for plan in plans}
    assert {item["id"] for item in result["candidates"]} == {
        plan.id for plan in plans
    }
    assert sum(item["weight"] for item in result["stakeholders"]) == 1
    assert result["candidates"][0]["id"] == result["recommended_id"]
    assert result["candidates"][0]["bargaining_rank"] == 1
    for candidate in result["candidates"]:
        assert 0.05 <= candidate["minimum_utility"] <= 1
        assert 0.05 <= candidate["nash_welfare"] <= 1
        assert all(
            0.05 <= utility <= 1
            for utility in candidate["utilities"].values()
        )
        assert candidate["hard_constraints_passed"] is True


def test_single_safe_candidate_has_full_normalized_utility():
    plans, failure = generate_ranked_candidates(
        storm_scenario(),
        [PlanProfile.balanced],
        runs=4,
    )
    assert failure is None and len(plans) == 1

    result = nash_bargaining(plans)
    candidate = result["candidates"][0]

    assert result["recommendation_changed"] is False
    assert candidate["nash_welfare"] == 1
    assert candidate["minimum_utility"] == 1
    assert set(candidate["utilities"].values()) == {1}
