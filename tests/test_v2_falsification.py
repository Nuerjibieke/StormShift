from stormshift_v2.falsification import discover_minimal_counterexamples
from stormshift_v2.optimizer import solve_candidate
from stormshift_v2.schemas import PlanProfile
from stormshift_v2.scenarios import storm_scenario


def _resilient_plan():
    scenario = storm_scenario()
    plan, failure = solve_candidate(scenario, PlanProfile.resilient)
    assert failure is None
    assert plan is not None
    return scenario, plan


def test_bounded_falsifier_finds_minimal_witness_and_safe_repair():
    scenario, plan = _resilient_plan()

    result = discover_minimal_counterexamples(scenario, plan)

    assert result["status"] == "completed"
    assert result["categories_tested"] == 4
    assert result["witness_count"] == 4
    assert result["evaluations"] > 4
    assert result["nearest_witness_id"] in {
        "runway_closure",
        "gate_outage",
        "separation_increase",
        "fuel_margin_loss",
    }

    witnesses = [axis for axis in result["axes"] if axis["status"] == "witness"]
    assert len(witnesses) == 4
    for witness in witnesses:
        assert witness["delta"] >= 1
        assert witness["failures"]
        assert witness["minimality"] == {
            "baseline_passed": True,
            "predecessor_delta": witness["delta"] - 1,
            "predecessor_passed": True,
            "witness_failed": True,
        }

    nearest = next(
        axis for axis in witnesses
        if axis["id"] == result["nearest_witness_id"]
    )
    assert nearest["repair"]["status"] in {
        "safe_repair",
        "manual_required",
    }
    if nearest["repair"]["available"]:
        assert nearest["repair"]["hard_constraints_passed"] is True
        assert nearest["repair"]["changed_count"] == len(
            nearest["repair"]["changed_flights"]
        )
        assert all(
            item.startswith("PASS")
            for item in nearest["repair"]["proof"]
        )


def test_bounded_falsifier_is_deterministic_and_does_not_mutate_inputs():
    scenario, plan = _resilient_plan()
    scenario_before = scenario.model_dump(mode="json")
    plan_before = plan.model_dump(mode="json")

    left = discover_minimal_counterexamples(scenario, plan)
    right = discover_minimal_counterexamples(scenario, plan)

    def signature(result):
        return [
            (
                axis["id"],
                axis["status"],
                axis["target_id"],
                axis["delta"],
                tuple(axis["failures"]),
                axis["minimality"]["predecessor_delta"],
            )
            for axis in result["axes"]
        ]

    assert signature(left) == signature(right)
    assert left["nearest_witness_id"] == right["nearest_witness_id"]
    assert left["evaluations"] == right["evaluations"]
    assert scenario.model_dump(mode="json") == scenario_before
    assert plan.model_dump(mode="json") == plan_before


def test_bounded_falsifier_skips_empty_execution_scope():
    scenario, plan = _resilient_plan()
    plan.assignments = []

    result = discover_minimal_counterexamples(scenario, plan)

    assert result["status"] == "not_applicable"
    assert result["categories_tested"] == 0
    assert result["witness_count"] == 0
    assert result["axes"] == []


def test_monotone_search_matches_every_integer_threshold():
    from stormshift_v2.falsification import _first_failed_delta
    for limit in (0, 1, 2, 4, 10, 30, 45):
        for threshold in range(1, limit + 2):
            calls = set()
            def probe(delta):
                assert 1 <= delta <= limit
                calls.add(delta)
                return delta >= threshold
            expected = threshold if threshold <= limit else None
            assert _first_failed_delta(probe, limit, monotone=True) == expected
            assert _first_failed_delta(lambda x: x >= threshold, limit, monotone=False) == expected
            if limit == 45 and threshold == 46:
                assert len(calls) <= 8


def test_linear_search_preserves_nonmonotone_interior_failures():
    from stormshift_v2.falsification import _first_failed_delta
    assert _first_failed_delta(lambda delta: delta == 3, 45, monotone=False) == 3


def test_search_reports_resource_specific_limits():
    from stormshift_v2.falsification import _search_axis
    scenario, plan = _resilient_plan()
    axis, stressed = _search_axis(
        scenario, plan, None, axis_id="bounded_test", label="test", label_en="test",
        targets=["short", "long"], maximum_delta=45,
        target_limit=lambda _base, target: 2 if target == "short" else 5,
        mutate=lambda *_args: None, describe=lambda *_args: ("unused", "unused"),
        monotone=True,
    )
    assert stressed is None
    assert axis["status"] == "lower_bound"
    assert axis["target_search_limits"] == {"long": 5, "short": 2}
    assert axis["minimality"]["predecessor_delta"] == 2


def test_monotone_and_linear_airport_results_match():
    from stormshift_v2.scenarios import sandbox_scenario, gate_outage_scenario, medical_scenario
    scenarios = [storm_scenario(), gate_outage_scenario(), medical_scenario()]
    scenarios += [sandbox_scenario(seed=seed, flight_count=count)
                  for seed, count in ((2030, 10), (2031, 20), (2032, 20))]
    saved_evaluations = 0
    for scenario in scenarios:
        for profile in (PlanProfile.balanced, PlanProfile.resilient):
            plan, failure = solve_candidate(scenario, profile)
            assert failure is None and plan is not None
            before = scenario.model_dump(mode="json"), plan.model_dump(mode="json")
            fast = discover_minimal_counterexamples(scenario, plan)
            linear = discover_minimal_counterexamples(scenario, plan, search_strategy="linear")
            assert fast["status"] == linear["status"]
            assert fast["nearest_witness_id"] == linear["nearest_witness_id"]
            assert fast["margin_tier"] == linear["margin_tier"]
            for left, right in zip(fast["axes"], linear["axes"], strict=True):
                for key in ("id", "status", "target_id", "delta", "failures",
                            "minimality", "target_search_limits"):
                    assert left[key] == right[key], (scenario.id, profile, key)
                for key in ("status", "available", "changed_flights", "changed_count",
                            "hard_constraints_passed", "proof"):
                    assert left["repair"][key] == right["repair"][key]
            saved_evaluations += linear["evaluations"] - fast["evaluations"]
            assert before == (scenario.model_dump(mode="json"), plan.model_dump(mode="json"))
    assert saved_evaluations > 0
