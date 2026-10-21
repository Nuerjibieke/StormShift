"""Optimized counterfactual Shapley attribution for airport recovery burden.

This module answers a narrow operational question: how much of the current
optimized recovery burden is associated with each modeled disruption family?
It is deliberately labelled structural counterfactual attribution, not
observational causal identification.
"""

from __future__ import annotations

import itertools
import math
from typing import Any, Iterable

from .optimizer import ACTIVE_STATES, profile_weights, solve_candidate
from .schemas import (
    EventSpec,
    EventType,
    OperatorIntent,
    PlanCandidate,
    ResourceStatus,
    ScenarioSpec,
    WeatherSpec,
)
from .simulation import simulate_candidate


WEATHER_EVENTS = {
    EventType.storm,
    EventType.runway_closed,
    EventType.runway_reopened,
}
DYNAMIC_EVENTS = {
    EventType.gate_outage,
    EventType.ground_vehicle_failure,
    EventType.medical_diversion,
}
METRIC_KEYS = (
    "risk_points",
    "mean_delay_min",
    "p90_delay_min",
    "missed_passengers",
    "queue_triggers",
    "fuel_risk_pct",
    "emissions_index",
)


def _dynamic_events(scenario: ScenarioSpec) -> list[EventSpec]:
    return [event for event in scenario.events if event.type in DYNAMIC_EVENTS]


def _dynamic_gate_ids(scenario: ScenarioSpec) -> set[str]:
    result: set[str] = set()
    for event in _dynamic_events(scenario):
        result.update(str(item) for item in event.payload.get("affected_gates", []))
        if event.type == EventType.gate_outage and event.resource_id:
            result.add(event.resource_id)
    return result


def _dynamic_flight_ids(scenario: ScenarioSpec) -> set[str]:
    result = {
        flight.id for flight in scenario.flights if flight.medical_priority
    }
    for event in _dynamic_events(scenario):
        if event.type != EventType.medical_diversion:
            continue
        result.add(event.resource_id or str(event.payload.get("flight_id", "MED901")))
    return result


def _frictionless_scenario(scenario: ScenarioSpec) -> ScenarioSpec:
    counterfactual = scenario.model_copy(deep=True)
    counterfactual.weather = WeatherSpec(
        severity=0,
        label="晴空反事实",
        visibility_km=15,
        closure_mean_min=0,
        closure_std_min=0,
        source="optimized counterfactual",
    )
    for runway in counterfactual.runways:
        runway.status = ResourceStatus.open
        runway.reopen_minute = None
        runway.reason = None
    for gate in counterfactual.gates:
        gate.status = ResourceStatus.available
        gate.occupied_by = None
        gate.available_minute = counterfactual.current_minute
    dynamic_flights = _dynamic_flight_ids(scenario)
    counterfactual.flights = [
        flight for flight in counterfactual.flights
        if flight.id not in dynamic_flights
    ]
    counterfactual.events = []
    return counterfactual


def _apply_weather(
    target: ScenarioSpec,
    source: ScenarioSpec,
) -> None:
    target.weather = source.weather.model_copy(deep=True)
    target.runways = [runway.model_copy(deep=True) for runway in source.runways]
    target.events.extend(
        event.model_copy(deep=True)
        for event in source.events
        if event.type in WEATHER_EVENTS
    )


def _apply_gate_pressure(
    target: ScenarioSpec,
    source: ScenarioSpec,
) -> None:
    """Restore standing gate pressure while excluding incident-time gate delays."""
    source_by_id = {gate.id: gate for gate in source.gates}
    ground_failure_gates = {
        str(gate_id)
        for event in _dynamic_events(source)
        if event.type == EventType.ground_vehicle_failure
        for gate_id in event.payload.get("affected_gates", [])
    }
    gate_outage_ids = {
        event.resource_id
        for event in _dynamic_events(source)
        if event.type == EventType.gate_outage and event.resource_id
    }
    for gate in target.gates:
        original = source_by_id[gate.id]
        if gate.id in gate_outage_ids:
            # The outage owns this state; do not also charge it to background pressure.
            continue
        gate.status = original.status
        gate.occupied_by = original.occupied_by
        if gate.id in ground_failure_gates:
            # Reconstruct the pre-incident state. The failure will restore its own delay.
            gate.available_minute = min(original.available_minute, source.current_minute)
        else:
            gate.available_minute = original.available_minute


def _apply_dynamic_incident(
    target: ScenarioSpec,
    source: ScenarioSpec,
) -> None:
    """Replay modeled event effects without importing unrelated background pressure."""
    target_by_gate = {gate.id: gate for gate in target.gates}
    source_by_gate = {gate.id: gate for gate in source.gates}
    source_by_flight = {flight.id: flight for flight in source.flights}

    for flight_id in _dynamic_flight_ids(source):
        if flight_id in source_by_flight and not any(
            flight.id == flight_id for flight in target.flights
        ):
            target.flights.append(source_by_flight[flight_id].model_copy(deep=True))

    for event in _dynamic_events(source):
        target.events.append(event.model_copy(deep=True))
        if event.type == EventType.gate_outage and event.resource_id:
            if event.resource_id in target_by_gate and event.resource_id in source_by_gate:
                source_gate = source_by_gate[event.resource_id]
                target_gate = target_by_gate[event.resource_id]
                target_gate.status = source_gate.status
                target_gate.occupied_by = source_gate.occupied_by
                target_gate.available_minute = source_gate.available_minute
        elif event.type == EventType.ground_vehicle_failure:
            unavailable_until = event.minute + max(1, event.duration_min)
            for gate_id in event.payload.get("affected_gates", []):
                gate = target_by_gate.get(str(gate_id))
                if gate is not None:
                    gate.available_minute = max(gate.available_minute, unavailable_until)


def _factor_specs(scenario: ScenarioSpec) -> list[dict[str, str]]:
    dynamic = _dynamic_events(scenario)
    specs: list[dict[str, str]] = []
    if (
        scenario.weather.severity > 0
        or any(runway.status != ResourceStatus.open for runway in scenario.runways)
        or any(event.type in WEATHER_EVENTS for event in scenario.events)
    ):
        specs.append({
            "id": "weather_runway",
            "label": "天气与跑道扰动",
            "label_en": "Weather & runway",
            "detail": "天气随机性、关闭跑道和容量收缩",
        })

    dynamic_gates = _dynamic_gate_ids(scenario)
    background_pressure = False
    for gate in scenario.gates:
        if gate.id in dynamic_gates and any(
            event.type == EventType.gate_outage
            and event.resource_id == gate.id
            for event in dynamic
        ):
            continue
        if (
            gate.status != ResourceStatus.available
            or gate.occupied_by is not None
            or (
                gate.available_minute > scenario.current_minute
                and gate.id not in dynamic_gates
            )
        ):
            background_pressure = True
            break
    if background_pressure:
        specs.append({
            "id": "gate_pressure",
            "label": "既有机位压力",
            "label_en": "Standing gate pressure",
            "detail": "已占用、保留或尚未释放的机位容量",
        })

    if dynamic or _dynamic_flight_ids(scenario):
        latest = max(dynamic, key=lambda item: item.minute, default=None)
        if latest and latest.type == EventType.ground_vehicle_failure:
            label = f"{latest.resource_id or '地面车辆'} 二次故障"
            detail = "故障造成的滑行扰动与受影响机位延后"
        elif latest and latest.type == EventType.gate_outage:
            label = f"{latest.resource_id or '机位'} 设备故障"
            detail = "机位或廊桥不可用造成的局部容量损失"
        elif latest and latest.type == EventType.medical_diversion:
            label = f"{latest.resource_id or 'MED'} 医疗备降"
            detail = "医疗优先航班插队及其资源占用"
        else:
            label = "动态运行事件"
            detail = "执行阶段新注入的优先级或资源扰动"
        specs.append({
            "id": "dynamic_incident",
            "label": label,
            "label_en": "Dynamic operational incident",
            "detail": detail,
        })
    return specs


def _coalition_scenario(
    scenario: ScenarioSpec,
    active_factors: Iterable[str],
) -> ScenarioSpec:
    active = set(active_factors)
    counterfactual = _frictionless_scenario(scenario)
    if "weather_runway" in active:
        _apply_weather(counterfactual, scenario)
    if "gate_pressure" in active:
        _apply_gate_pressure(counterfactual, scenario)
    if "dynamic_incident" in active:
        _apply_dynamic_incident(counterfactual, scenario)
    return counterfactual


def _metric_values(
    scenario: ScenarioSpec,
    candidate: PlanCandidate,
    intent: OperatorIntent | None,
    passenger_denominator: int,
    flight_denominator: int,
) -> dict[str, float]:
    report = candidate.report
    weights = profile_weights(candidate.profile, intent)
    normalized = {
        "delay": min(2.0, report.mean_delay_min / 30.0),
        "connections": min(2.0, report.missed_connections / max(1, passenger_denominator)),
        "fuel_risk": report.fuel_risk_probability,
        "conflicts": min(2.0, report.remaining_conflicts / max(1, flight_denominator)),
        "emissions": min(
            2.0,
            report.emissions_index / max(1, flight_denominator * 25),
        ),
    }
    risk_points = 100 * sum(
        float(getattr(weights, key)) * value
        for key, value in normalized.items()
    )
    return {
        "risk_points": risk_points,
        "mean_delay_min": float(report.mean_delay_min),
        "p90_delay_min": float(report.p90_delay_min),
        "missed_passengers": float(report.missed_connections),
        "queue_triggers": float(report.remaining_conflicts),
        "fuel_risk_pct": 100 * float(report.fuel_risk_probability),
        "emissions_index": float(report.emissions_index),
    }


def _penalty_metrics(
    passenger_denominator: int,
    flight_denominator: int,
) -> dict[str, float]:
    return {
        "risk_points": 200.0,
        "mean_delay_min": 180.0,
        "p90_delay_min": 180.0,
        "missed_passengers": float(passenger_denominator),
        "queue_triggers": float(flight_denominator),
        "fuel_risk_pct": 100.0,
        "emissions_index": float(flight_denominator * 50),
    }


def _world_value(
    scenario: ScenarioSpec,
    reference: PlanCandidate,
    intent: OperatorIntent | None,
    runs: int,
    passenger_denominator: int,
    flight_denominator: int,
) -> dict[str, Any]:
    candidate, failure = solve_candidate(
        scenario,
        reference.profile,
        intent,
        timeout_seconds=.25,
    )
    if candidate is None:
        reason = (
            failure.reasons[0]
            if failure is not None and failure.reasons
            else "反事实约束组合不可行"
        )
        return {
            "feasible": False,
            "reason": reason,
            "metrics": _penalty_metrics(passenger_denominator, flight_denominator),
        }
    candidate.report = simulate_candidate(scenario, candidate, runs=runs)
    return {
        "feasible": True,
        "reason": None,
        "metrics": _metric_values(
            scenario,
            candidate,
            intent,
            passenger_denominator,
            flight_denominator,
        ),
    }


def _coalition_key(factors: Iterable[str]) -> str:
    values = tuple(factors)
    return "+".join(values) if values else "frictionless"


def attribute_recovery_burden(
    scenario: ScenarioSpec,
    reference: PlanCandidate,
    intent: OperatorIntent | None = None,
    runs_per_coalition: int = 8,
) -> dict[str, Any]:
    """Re-optimize every factor coalition and compute exact Shapley contributions."""
    factor_specs = _factor_specs(scenario)
    factor_ids = [item["id"] for item in factor_specs]
    passenger_denominator = sum(
        max(0, flight.connections)
        for flight in scenario.flights
        if flight.state in ACTIVE_STATES
    )
    flight_denominator = sum(
        flight.state in ACTIVE_STATES for flight in scenario.flights
    )
    worlds: dict[frozenset[str], dict[str, Any]] = {}

    for size in range(len(factor_ids) + 1):
        for subset in itertools.combinations(factor_ids, size):
            coalition = frozenset(subset)
            world_scenario = _coalition_scenario(scenario, subset)
            worlds[coalition] = _world_value(
                world_scenario,
                reference,
                intent,
                max(2, runs_per_coalition),
                passenger_denominator,
                flight_denominator,
            )

    n = len(factor_ids)
    contribution_by_factor: dict[str, dict[str, float]] = {
        factor_id: {metric: 0.0 for metric in METRIC_KEYS}
        for factor_id in factor_ids
    }
    if n:
        denominator = math.factorial(n)
        for factor_id in factor_ids:
            others = [item for item in factor_ids if item != factor_id]
            for size in range(len(others) + 1):
                coefficient = (
                    math.factorial(size)
                    * math.factorial(n - size - 1)
                    / denominator
                )
                for subset in itertools.combinations(others, size):
                    without = frozenset(subset)
                    with_factor = without | {factor_id}
                    for metric in METRIC_KEYS:
                        marginal = (
                            worlds[with_factor]["metrics"][metric]
                            - worlds[without]["metrics"][metric]
                        )
                        contribution_by_factor[factor_id][metric] += (
                            coefficient * marginal
                        )

    baseline_metrics = worlds[frozenset()]["metrics"]
    full_metrics = worlds[frozenset(factor_ids)]["metrics"]
    total_risk_burden = full_metrics["risk_points"] - baseline_metrics["risk_points"]
    rows: list[dict[str, Any]] = []
    spec_by_id = {item["id"]: item for item in factor_specs}
    for factor_id in factor_ids:
        raw = contribution_by_factor[factor_id]
        risk_value = raw["risk_points"]
        share = (
            100 * risk_value / total_risk_burden
            if abs(total_risk_burden) > 1e-9
            else 0.0
        )
        rows.append({
            **spec_by_id[factor_id],
            "risk_points": round(risk_value, 2),
            "share_percent": round(share, 1),
            "mean_delay_min": round(raw["mean_delay_min"], 2),
            "p90_delay_min": round(raw["p90_delay_min"], 2),
            "missed_passengers": round(raw["missed_passengers"], 1),
            "queue_triggers": round(raw["queue_triggers"], 2),
            "fuel_risk_pct": round(raw["fuel_risk_pct"], 1),
            "emissions_index": round(raw["emissions_index"], 2),
        })
    rows.sort(key=lambda item: abs(float(item["risk_points"])), reverse=True)

    coalition_rows: list[dict[str, Any]] = []
    for coalition, result in sorted(
        worlds.items(),
        key=lambda item: (len(item[0]), _coalition_key(sorted(item[0]))),
    ):
        coalition_rows.append({
            "id": _coalition_key(sorted(coalition)),
            "active_factors": [
                factor_id for factor_id in factor_ids if factor_id in coalition
            ],
            "feasible": result["feasible"],
            "reason": result["reason"],
            "metrics": {
                key: round(float(value), 2)
                for key, value in result["metrics"].items()
            },
        })

    attributed_sum = sum(
        contribution_by_factor[factor_id]["risk_points"]
        for factor_id in factor_ids
    )
    additivity_error = attributed_sum - total_risk_burden
    top = next((item for item in rows if item["risk_points"] > 0), rows[0] if rows else None)
    if top is None:
        summary = "当前没有可归因的建模扰动。"
    elif total_risk_burden <= 0:
        summary = "当前扰动组合未增加综合恢复负担；请查看各因素的正负抵消。"
    else:
        summary = (
            f"{top['label']}贡献约 {top['risk_points']:.1f} 风险点，"
            f"是当前优化后恢复负担的首要来源。"
        )

    return {
        "method": "exact Shapley over re-optimized structural counterfactual coalitions",
        "status": (
            "completed"
            if all(item["feasible"] for item in worlds.values())
            else "partial"
        ),
        "profile": reference.profile.value,
        "runs_per_coalition": max(2, runs_per_coalition),
        "common_seed_schedule": True,
        "factor_count": n,
        "coalition_count": len(worlds),
        "passenger_denominator": passenger_denominator,
        "flight_denominator": flight_denominator,
        "baseline": {
            key: round(float(value), 2)
            for key, value in baseline_metrics.items()
        },
        "full": {
            key: round(float(value), 2)
            for key, value in full_metrics.items()
        },
        "total_burden": {
            key: round(
                float(full_metrics[key] - baseline_metrics[key]),
                2,
            )
            for key in METRIC_KEYS
        },
        "factors": rows,
        "coalitions": coalition_rows,
        "additivity": {
            "risk_delta_points": round(total_risk_burden, 4),
            "attributed_sum_points": round(attributed_sum, 4),
            "absolute_error": round(abs(additivity_error), 8),
            "passed": abs(additivity_error) < 1e-7,
        },
        "top_factor_id": top["id"] if top else None,
        "summary": summary,
        "interpretation": (
            "每个数表示在所有可能加入顺序中，该因素对重新优化后负担的平均边际贡献；"
            "负值表示该因素与其他约束组合后产生了缓解作用。"
        ),
        "caveat": (
            "这是合成数字孪生内的优化结构反事实归因，不是来自真实机场随机试验的因果识别；"
            "共同固定种子用于降低组合比较噪声。"
        ),
    }

