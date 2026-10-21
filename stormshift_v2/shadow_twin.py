"""Paired shadow-twin evaluation for statistically gated plan switching.

The incumbent and challenger are executed under identical stochastic worlds
(common random numbers). This reduces variance and prevents replanning on noise.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .optimizer import profile_weights, validate_assignments
from .schemas import OperatorIntent, PlanCandidate, PlanProfile, ScenarioSpec
from .simulation import SIMULATION_METHOD_VERSION, _single_run, build_passenger_graph


def _scoped_copy(plan: PlanCandidate, flight_ids: set[str]) -> PlanCandidate:
    scoped = plan.model_copy(deep=True)
    scoped.assignments = [
        item for item in scoped.assignments if item.flight_id in flight_ids
    ]
    scoped.actions = []
    return scoped


def _loss_components(
    outcome: Any,
    *,
    flight_count: int,
    passenger_count: int,
) -> dict[str, float]:
    mean_delay = (
        float(np.mean(list(outcome.delays.values())))
        if outcome.delays
        else 0.0
    )
    return {
        "delay": min(2.0, mean_delay / 30.0),
        "connections": min(1.0, float(outcome.missed) / max(1, passenger_count)),
        "fuel_risk": (
            sum(bool(value) for value in outcome.fuel_risks.values())
            / max(1, flight_count)
        ),
        "conflicts": min(1.0, float(outcome.conflicts) / max(1, flight_count)),
        "emissions": min(
            2.0,
            float(outcome.emissions) / max(1.0, flight_count * 30.0),
        ),
    }


def _weighted_loss(components: dict[str, float], weights: Any) -> float:
    return 100.0 * sum(
        float(components[key]) * float(getattr(weights, key))
        for key in (
            "delay",
            "connections",
            "fuel_risk",
            "conflicts",
            "emissions",
        )
    )


def _run_summary(outcomes: list[Any]) -> dict[str, float]:
    delays = [
        float(np.mean(list(item.delays.values()))) if item.delays else 0.0
        for item in outcomes
    ]
    return {
        "mean_delay_min": round(float(np.mean(delays)), 2),
        "mean_missed_connections": round(
            float(np.mean([item.missed for item in outcomes])), 2
        ),
        "fuel_risk_world_rate": round(
            float(np.mean([
                any(item.fuel_risks.values()) if item.fuel_risks else False
                for item in outcomes
            ])),
            4,
        ),
        "mean_queue_triggers": round(
            float(np.mean([item.conflicts for item in outcomes])), 2
        ),
        "mean_emissions_index": round(
            float(np.mean([item.emissions for item in outcomes])), 2
        ),
    }


def evaluate_shadow_switch(
    scenario: ScenarioSpec,
    incumbent: PlanCandidate,
    challenger: PlanCandidate,
    intent: OperatorIntent | None = None,
    runs: int = 32,
    switch_cost_points: float = 0.5,
) -> dict[str, Any]:
    """Decide whether a safe challenger has enough paired evidence to switch."""
    if runs < 4:
        raise ValueError("Shadow Twin 至少需要 4 个成对仿真世界")

    incumbent_ids = {item.flight_id for item in incumbent.assignments}
    challenger_ids = {item.flight_id for item in challenger.assignments}
    common_ids = incumbent_ids & challenger_ids
    challenger_only = sorted(challenger_ids - incumbent_ids)
    incumbent_only = sorted(incumbent_ids - challenger_ids)
    if not common_ids:
        return {
            "method": "paired common-random-number shadow policy improvement",
            "simulation_method_version": SIMULATION_METHOD_VERSION,
            "cache_policy": "both_sides_recomputed",
            "status": "safe_stop",
            "decision": "safe_stop",
            "runs": runs,
            "summary": "两个方案没有共同决策范围，禁止自动切换并转人工复核。",
            "caveat": "影子评估只比较共同覆盖的待处置航班。",
        }

    incumbent_scope = _scoped_copy(incumbent, common_ids)
    challenger_scope = _scoped_copy(challenger, common_ids)
    incumbent_proof = validate_assignments(
        scenario, incumbent_scope.assignments, intent
    )
    challenger_proof = validate_assignments(
        scenario, challenger.assignments, intent
    )
    incumbent_failures = [
        item for item in incumbent_proof if item.startswith("FAIL")
    ]
    challenger_failures = [
        item for item in challenger_proof if item.startswith("FAIL")
    ]
    incumbent_operationally_valid = (
        not incumbent_failures and not challenger_only
    )
    challenger_valid = not challenger_failures

    graph = build_passenger_graph(scenario)
    seeds = [
        scenario.seed * 1009 + (index + 101) * 7919
        for index in range(runs)
    ]
    incumbent_outcomes = [
        _single_run(scenario, incumbent_scope, graph, seed)
        for seed in seeds
    ]
    challenger_outcomes = [
        _single_run(scenario, challenger_scope, graph, seed)
        for seed in seeds
    ]

    flights = {item.id: item for item in scenario.flights}
    passenger_count = sum(
        max(0, flights[item].connections)
        for item in common_ids
        if item in flights
    )
    weights = profile_weights(
        PlanProfile.operator if intent else PlanProfile.balanced,
        intent,
    )
    incumbent_losses: list[float] = []
    challenger_losses: list[float] = []
    paired_gains: list[float] = []
    for incumbent_run, challenger_run in zip(
        incumbent_outcomes, challenger_outcomes
    ):
        incumbent_loss = _weighted_loss(
            _loss_components(
                incumbent_run,
                flight_count=len(common_ids),
                passenger_count=passenger_count,
            ),
            weights,
        )
        challenger_loss = _weighted_loss(
            _loss_components(
                challenger_run,
                flight_count=len(common_ids),
                passenger_count=passenger_count,
            ),
            weights,
        )
        incumbent_losses.append(incumbent_loss)
        challenger_losses.append(challenger_loss)
        paired_gains.append(incumbent_loss - challenger_loss)

    gain_array = np.asarray(paired_gains, dtype=float)
    bootstrap_rng = np.random.default_rng(scenario.seed * 65537 + runs)
    bootstrap_means = np.asarray([
        float(np.mean(bootstrap_rng.choice(gain_array, size=runs, replace=True)))
        for _ in range(1000)
    ])
    ci_low, ci_high = np.percentile(bootstrap_means, [2.5, 97.5])
    mean_gain = float(np.mean(gain_array))
    probability_beats_cost = float(
        np.mean(gain_array > switch_cost_points)
    )

    incumbent_summary = _run_summary(incumbent_outcomes)
    challenger_summary = _run_summary(challenger_outcomes)
    incumbent_fuel_unsafe = (
        incumbent_summary["fuel_risk_world_rate"] >= 0.20
    )

    if not challenger_valid:
        status = "safe_stop"
        decision = "safe_stop"
        reason = "影子候选没有通过硬约束，禁止切换并转人工处置。"
    elif not incumbent_operationally_valid or incumbent_fuel_unsafe:
        status = "mandatory_switch"
        decision = "switch"
        reason = (
            "原方案已被新事件破坏或没有覆盖新增对象；"
            "安全候选通过硬约束，因此必须切换。"
        )
    elif float(ci_low) > switch_cost_points:
        status = "evidence_switch"
        decision = "switch"
        reason = (
            "成对改善的 95% 置信下界高于切换成本，"
            "允许在已批准安全包络内切换。"
        )
    else:
        status = "keep_incumbent"
        decision = "keep"
        reason = (
            "新方案的改善证据不足以覆盖切换成本；"
            "保留当前方案并继续监控，避免重规划抖动。"
        )

    return {
        "method": "paired common-random-number shadow policy improvement",
        "status": status,
        "decision": decision,
        "runs": runs,
        "switch_cost_points": switch_cost_points,
        "scope": {
            "common_flight_ids": sorted(common_ids),
            "common_flights": len(common_ids),
            "passengers": passenger_count,
            "challenger_only_ids": challenger_only,
            "incumbent_only_ids": incumbent_only,
        },
        "hard_gate": {
            "incumbent_operationally_valid": incumbent_operationally_valid,
            "incumbent_failures": incumbent_failures,
            "incumbent_fuel_unsafe": incumbent_fuel_unsafe,
            "challenger_valid": challenger_valid,
            "challenger_failures": challenger_failures,
        },
        "incumbent": {
            "plan_id": incumbent.id,
            "title": incumbent.title,
            "mean_loss_points": round(float(np.mean(incumbent_losses)), 3),
            **incumbent_summary,
        },
        "challenger": {
            "plan_id": challenger.id,
            "title": challenger.title,
            "mean_loss_points": round(float(np.mean(challenger_losses)), 3),
            **challenger_summary,
        },
        "paired_gain": {
            "mean_points": round(mean_gain, 3),
            "ci95_low": round(float(ci_low), 3),
            "ci95_high": round(float(ci_high), 3),
            "probability_beats_cost": round(probability_beats_cost, 4),
        },
        "weights": weights.model_dump(),
        "reason": reason,
        "summary": (
            f"{runs} 个同随机世界成对评估："
            f"软目标净收益 {mean_gain:+.2f} 风险点，"
            f"95% CI [{float(ci_low):+.2f}, {float(ci_high):+.2f}]；"
            + ("执行切换" if decision == "switch" else "保持原方案")
        ),
        "caveat": (
            "Shadow Twin 只在当前仿真场景族内提供切换证据；"
            "它不绕过 CP-SAT 硬约束、Conformal Risk Shield 或人工授权。"
        ),
    }

