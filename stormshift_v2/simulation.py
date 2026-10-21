"""Stochastic airport execution using SimPy and a NetworkX passenger graph."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass

import networkx as nx
import numpy as np
import simpy

from .adaptive_envelope import build_adaptive_envelope
from .optimizer import TURNAROUND_MINUTES, empty_report, profile_weights, solve_candidate
from .twin_trust import assess_twin_trust, bernoulli_e_process
from .passenger import connection_buffer_minutes
from .risk_shield import assess_conformal_risk, conformal_landing_limits
from .schemas import (
    InfeasibilityReport,
    FlightAssignment,
    FlightState,
    OperatorIntent,
    PlanCandidate,
    PlanProfile,
    ScenarioSpec,
    SimulationReport,
)


SIMULATION_METHOD_VERSION = "entity-world-v1"
PREVIEW_METHOD_VERSION = "entity-world-v1+fixed-reservations-v1"


@dataclass(frozen=True)
class FlightShocks:
    approach: float
    taxi: float
    vehicle: float
    turnaround: float


@dataclass(frozen=True)
class WorldRealization:
    seed: int
    weather: float
    reopen: dict[str, float]
    flights: dict[str, FlightShocks]


def _world_rng(seed: int, entity: str, disturbance: str) -> np.random.Generator:
    # Python hash() varies by process. A semantic digest never depends on list order,
    # plan identity, branch consumption or another entity's existence.
    key = json.dumps([SIMULATION_METHOD_VERSION, int(seed), entity, disturbance],
                     ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    stable_seed = int.from_bytes(hashlib.sha256(key).digest()[:16], "big")
    return np.random.default_rng(stable_seed)


def realize_world(scenario: ScenarioSpec, seed: int) -> WorldRealization:
    """Sample all exogenous shocks before starting any competing SimPy process."""
    severity = scenario.weather.severity
    weather = max(0.0, float(_world_rng(seed, "airport", "weather").normal(
        severity * .35, max(.25, severity * .55))))
    reopen = {
        runway.id: (float(scenario.current_minute) if runway.reopen_minute is None
                    else max(float(scenario.current_minute), float(
                        _world_rng(seed, runway.id, "runway_reopen").normal(
                            runway.reopen_minute, max(1.0, scenario.weather.closure_std_min)))))
        for runway in sorted(scenario.runways, key=lambda item: item.id)
    }
    flights = {
        flight.id: FlightShocks(
            approach=max(0.0, float(_world_rng(seed, flight.id, "approach").normal(0, 1.1 + severity * .35))),
            taxi=max(0.0, float(_world_rng(seed, flight.id, "taxi").normal(0, .7 + severity * .18))),
            vehicle=float(_world_rng(seed, flight.id, "vehicle_failure").uniform(1.5, 4.0)),
            turnaround=max(0.0, float(_world_rng(seed, flight.id, "turnaround").normal(0, 2.2))),
        )
        for flight in sorted(scenario.flights, key=lambda item: item.id)
    }
    return WorldRealization(seed, weather, reopen, flights)


def report_metric(report: SimulationReport, name: str) -> float:
    """Unrounded metrics for decisions; legacy display values only as a fallback."""
    return float(report.raw_metrics.get(name, getattr(report, name)))


@dataclass
class _RunResult:
    delays: dict[str, float]
    landing_times: dict[str, float]
    gate_in_times: dict[str, float]
    gate_out_times: dict[str, float]
    fuel_risks: dict[str, bool]
    missed: int
    missed_by_flight: dict[str, int]
    conflicts: int
    emissions: float
    recovery: float


def build_passenger_graph(
    scenario: ScenarioSpec,
    connection_buffer_bonus: dict[str, float] | None = None,
) -> nx.DiGraph:
    """Create an explicit, inspectable passenger-transfer network."""
    bonuses = connection_buffer_bonus or {}
    graph = nx.DiGraph()
    for flight in sorted(scenario.flights, key=lambda item: item.id):
        graph.add_node(flight.id, kind="arrival")
        if not flight.connections:
            continue
        group_id = f"connection:{flight.id}"
        # Synthetic operations do not invent another real flight number; the edge is a passenger group.
        base_buffer = float(connection_buffer_minutes(scenario.seed, flight.id))
        intervention_bonus = max(0.0, float(bonuses.get(flight.id, 0.0)))
        buffer = base_buffer + intervention_bonus
        graph.add_node(
            group_id,
            kind="connection_group",
            buffer_min=buffer,
            base_buffer_min=base_buffer,
            intervention_bonus_min=intervention_bonus,
        )
        graph.add_edge(
            flight.id,
            group_id,
            passengers=flight.connections,
            buffer_min=buffer,
            base_buffer_min=base_buffer,
            intervention_bonus_min=intervention_bonus,
        )
    return graph


def _reservation_start(start: float, duration: float, intervals: list[tuple[float, float]]) -> float:
    """Earliest non-overlapping start in a sorted, immutable resource calendar."""
    for left, right in intervals:
        if start + duration <= left:
            break
        if start < right and start + duration > left:
            start = right
    return start


def _single_run(
    scenario: ScenarioSpec,
    candidate: PlanCandidate,
    graph: nx.DiGraph,
    seed: int,
    *,
    respect_committed: bool = False,
) -> _RunResult:
    world = realize_world(scenario, seed)
    env = simpy.Environment(initial_time=scenario.current_minute)
    runways = {item.id: simpy.PriorityResource(env, capacity=1) for item in scenario.runways}
    gates = {item.id: simpy.PriorityResource(env, capacity=1) for item in scenario.gates}
    flight_by_id = {item.id: item for item in scenario.flights}
    runway_by_id = {item.id: item for item in scenario.runways}
    gate_by_id = {item.id: item for item in scenario.gates}
    results: dict[str, dict[str, float | bool]] = {}
    conflict_count = 0
    common_weather_shock = world.weather
    actual_reopen = world.reopen
    calendars: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    if respect_committed:
        from .resource_calendar import validate_resource_calendar
        failures = validate_resource_calendar(scenario, scenario.committed_reservations)
        if failures:
            raise ValueError("; ".join(failures))
        assigned = {item.flight_id for item in candidate.assignments}
        if assigned & {item.flight_id for item in scenario.committed_reservations}:
            raise ValueError("Committed flights must not be simulated again")
        for row in scenario.committed_reservations:
            calendars[(row.kind, row.resource_id)].append((row.start_minute, row.end_minute))
        for rows in calendars.values():
            rows.sort()

    def occupy(kind, resource_id, resource, duration, priority):
        # Check again after acquiring: queueing can push an otherwise-valid interval
        # into a future committed reservation. Never move that immutable reservation.
        intervals = calendars[(kind, resource_id)]
        while True:
            start = _reservation_start(float(env.now), duration, intervals)
            yield env.timeout(max(0.0, start - env.now))
            with resource.request(priority=priority) as request:
                yield request
                start = _reservation_start(float(env.now), duration, intervals)
                if start <= env.now:
                    entered = float(env.now)
                    yield env.timeout(duration)
                    return entered
            # The context released the resource; wait without blocking other work.
            yield env.timeout(max(0.0, start - env.now))

    def execute(assignment):
        nonlocal conflict_count
        flight = flight_by_id[assignment.flight_id]
        runway = runway_by_id[assignment.runway]
        gate = gate_by_id[assignment.gate]
        shocks = world.flights[flight.id]
        approach_variance = shocks.approach
        target = max(
            assignment.landing_minute + common_weather_shock + approach_variance,
            actual_reopen[runway.id],
        )
        yield env.timeout(max(0, target - env.now))
        priority = (0 if flight.medical_priority else 1, assignment.landing_minute, flight.id)
        queued_at = env.now
        if respect_committed:
            actual_landing = yield env.process(occupy(
                "runway", runway.id, runways[runway.id], runway.separation_min, priority))
            if actual_landing - queued_at > .1:
                conflict_count += 1
        else:
            with runways[runway.id].request(priority=priority) as runway_request:
                yield runway_request
                if env.now - queued_at > .1:
                    conflict_count += 1
                actual_landing = float(env.now)
                yield env.timeout(runway.separation_min)

        taxi_noise = shocks.taxi
        if any(event.type.value == "ground_vehicle_failure" for event in scenario.events):
            taxi_noise += shocks.vehicle
        yield env.timeout(assignment.taxi_minutes + taxi_noise)
        if env.now < gate.available_minute:
            yield env.timeout(gate.available_minute - env.now)
        queued_at = env.now
        if respect_committed:
            gate_in = yield env.process(occupy(
                "gate", gate.id, gates[gate.id], 22 + shocks.turnaround, priority))
            if gate_in - queued_at > .1:
                conflict_count += 1
            gate_out = float(env.now)
        else:
            with gates[gate.id].request(priority=priority) as gate_request:
                yield gate_request
                gate_queue = float(env.now - queued_at)
                if gate_queue > .1:
                    conflict_count += 1
                gate_in = float(env.now)
                turnaround_noise = shocks.turnaround
                yield env.timeout(22 + turnaround_noise)
                gate_out = float(env.now)

        planned_gate = gate_by_id.get(flight.planned_gate, gate)
        scheduled_gate_in = flight.scheduled_arrival + planned_gate.taxi_minutes
        # Passenger and operational delay is measured at the gate, so taxi and gate queues matter.
        delay = max(0.0, gate_in - scheduled_gate_in)
        fuel_risk = actual_landing >= scenario.current_minute + flight.fuel_margin_min - 5
        emissions = flight.emissions_factor * (
            assignment.taxi_minutes + taxi_noise + delay * .58 + (4 if assignment.remote_stand else 0)
        )
        results[flight.id] = {
            "actual_landing": actual_landing,
            "delay": delay,
            "fuel_risk": fuel_risk,
            "emissions": emissions,
            "gate_in": gate_in,
            "gate_out": gate_out,
        }

    for assignment in sorted(candidate.assignments, key=lambda item: (
        not flight_by_id[item.flight_id].medical_priority, item.landing_minute, item.flight_id
    )):
        env.process(execute(assignment))
    env.run()

    results = dict(sorted(results.items()))
    missed = 0
    missed_by_flight: dict[str, int] = {}
    for flight_id, values in results.items():
        for _, _, edge in graph.out_edges(flight_id, data=True):
            buffer = float(edge["buffer_min"])
            delay = float(values["delay"])
            if delay > buffer:
                severity = min(1.0, .35 + (delay - buffer) / 25)
                group_missed = round(int(edge["passengers"]) * severity)
                missed += group_missed
                missed_by_flight[flight_id] = missed_by_flight.get(flight_id, 0) + group_missed

    return _RunResult(
        delays={key: float(value["delay"]) for key, value in results.items()},
        landing_times={key: float(value["actual_landing"]) for key, value in results.items()},
        gate_in_times={key: float(value["gate_in"]) for key, value in results.items()},
        gate_out_times={key: float(value["gate_out"]) for key, value in results.items()},
        fuel_risks={key: bool(value["fuel_risk"]) for key, value in results.items()},
        missed=missed,
        missed_by_flight=missed_by_flight,
        conflicts=conflict_count,
        emissions=math.fsum(float(value["emissions"]) for value in results.values()),
        recovery=max((float(value["gate_out"]) for value in results.values()), default=scenario.current_minute),
    )


def _conformal_radius(
    observed: list[float],
    point_prediction: float,
    coverage: float = .90,
) -> float:
    """One-sided split-conformal delay radius using the conservative higher rank."""
    if not observed:
        return 0.0
    scores = sorted(max(0.0, value - point_prediction) for value in observed)
    rank = math.ceil((len(scores) + 1) * coverage)
    return round(float(scores[min(len(scores) - 1, max(0, rank - 1))]), 2)


def simulate_candidate(
    scenario: ScenarioSpec,
    candidate: PlanCandidate,
    runs: int = 50,
    connection_buffer_bonus: dict[str, float] | None = None,
    *,
    respect_committed: bool = False,
) -> SimulationReport:
    if runs < 1:
        raise ValueError("Simulation requires at least one world")
    graph = build_passenger_graph(scenario, connection_buffer_bonus)
    outcomes = [
        _single_run(scenario, candidate, graph, scenario.seed * 1009 + index * 7919,
                    respect_committed=respect_committed)
        for index in range(runs)
    ]
    # Odd/even seeds create a deterministic, disjoint calibration/holdout split.
    # Aggregate plan metrics still use every world; only uncertainty coverage is held out.
    calibration_outcomes = outcomes[::2]
    holdout_outcomes = outcomes[1::2] or outcomes[-1:]
    run_delays = np.array([
        np.mean(list(item.delays.values())) if item.delays else 0.0 for item in outcomes
    ])
    p90 = float(np.percentile(run_delays, 90))
    tail = run_delays[run_delays >= p90]
    standard_error = float(np.std(run_delays, ddof=1) / math.sqrt(runs)) if runs > 1 else 0.0
    flight_ids = sorted(item.flight_id for item in candidate.assignments)
    assignment_by_id = {item.flight_id: item for item in candidate.assignments}
    per_flight: dict[str, dict[str, float]] = {}
    for flight_id in flight_ids:
        assignment = assignment_by_id[flight_id]
        delays = np.array([run.delays.get(flight_id, 0) for run in outcomes])
        missed_samples = np.array([
            run.missed_by_flight.get(flight_id, 0) for run in outcomes
        ])
        landing_times = [run.landing_times[flight_id] for run in calibration_outcomes]
        gate_in_times = [run.gate_in_times[flight_id] for run in calibration_outcomes]
        gate_out_times = [run.gate_out_times[flight_id] for run in calibration_outcomes]
        holdout_landing = [run.landing_times[flight_id] for run in holdout_outcomes]
        holdout_gate_in = [run.gate_in_times[flight_id] for run in holdout_outcomes]
        holdout_gate_out = [run.gate_out_times[flight_id] for run in holdout_outcomes]
        eldt_point = float(assignment.landing_minute)
        eibt_point = eldt_point + assignment.taxi_minutes
        tobt_point = eibt_point + TURNAROUND_MINUTES
        eldt_radius = _conformal_radius(landing_times, eldt_point)
        eibt_radius = _conformal_radius(gate_in_times, eibt_point)
        tobt_radius = _conformal_radius(gate_out_times, tobt_point)
        eldt_radius_95 = _conformal_radius(landing_times, eldt_point, .95)
        eibt_radius_95 = _conformal_radius(gate_in_times, eibt_point, .95)
        tobt_radius_95 = _conformal_radius(gate_out_times, tobt_point, .95)
        eldt_covered = [
            value <= eldt_point + eldt_radius for value in holdout_landing
        ]
        eibt_covered = [
            value <= eibt_point + eibt_radius for value in holdout_gate_in
        ]
        tobt_covered = [
            value <= tobt_point + tobt_radius for value in holdout_gate_out
        ]
        eldt_evidence = bernoulli_e_process(eldt_covered)
        eibt_evidence = bernoulli_e_process(eibt_covered)
        tobt_evidence = bernoulli_e_process(tobt_covered)
        per_flight[flight_id] = {
            "mean_delay": round(float(np.mean(delays)), 2),
            "p90_delay": round(float(np.percentile(delays, 90)), 2),
            "expected_missed_passengers": round(float(np.mean(missed_samples)), 2),
            "miss_probability": round(float(np.mean(missed_samples > 0)), 4),
            "p90_missed_passengers": round(float(np.percentile(missed_samples, 90)), 2),
            "fuel_risk_probability": round(
                float(np.mean([run.fuel_risks.get(flight_id, False) for run in outcomes])), 4
            ),
            "conformal_coverage": .90,
            "calibration_samples": float(len(calibration_outcomes)),
            "evaluation_samples": float(len(holdout_outcomes)),
            "split_independent": float(runs >= 2),
            "eldt_q90_radius": eldt_radius,
            "eibt_q90_radius": eibt_radius,
            "tobt_q90_radius": tobt_radius,
            "eldt_empirical_coverage": float(eldt_evidence["empirical_coverage"]),
            "eibt_empirical_coverage": float(eibt_evidence["empirical_coverage"]),
            "tobt_empirical_coverage": float(tobt_evidence["empirical_coverage"]),
            "eldt_exceedances": float(eldt_evidence["exceedances"]),
            "eibt_exceedances": float(eibt_evidence["exceedances"]),
            "tobt_exceedances": float(tobt_evidence["exceedances"]),
            "eldt_e_process": float(eldt_evidence["e_value"]),
            "eibt_e_process": float(eibt_evidence["e_value"]),
            "tobt_e_process": float(tobt_evidence["e_value"]),
            "eldt_e_process_max": float(eldt_evidence["max_e_value"]),
            "eibt_e_process_max": float(eibt_evidence["max_e_value"]),
            "tobt_e_process_max": float(tobt_evidence["max_e_value"]),
        }
    mean_delay = float(np.mean(run_delays))
    raw_metrics = {
        "mean_delay_min": mean_delay,
        "p90_delay_min": p90,
        "cvar_delay_min": float(np.mean(tail)) if len(tail) else p90,
        "missed_connections": float(np.mean([item.missed for item in outcomes])),
        "fuel_risk_probability": float(np.mean([any(item.fuel_risks.values()) for item in outcomes])),
        "remaining_conflicts": float(np.mean([item.conflicts for item in outcomes])),
        "emissions_index": float(np.mean([item.emissions for item in outcomes])),
        "recovery_minute": float(np.mean([item.recovery for item in outcomes])),
        "confidence_low": max(0, mean_delay - 1.96 * standard_error),
        "confidence_high": mean_delay + 1.96 * standard_error,
    }
    return SimulationReport(
        method_version=PREVIEW_METHOD_VERSION if respect_committed else SIMULATION_METHOD_VERSION,
        raw_metrics=raw_metrics,
        runs=runs,
        mean_delay_min=round(mean_delay, 2),
        p90_delay_min=round(p90, 2),
        cvar_delay_min=round(float(np.mean(tail)) if len(tail) else p90, 2),
        missed_connections=round(float(np.mean([item.missed for item in outcomes]))),
        fuel_risk_probability=round(
            float(np.mean([
                any(run.fuel_risks.values()) if run.fuel_risks else False
                for run in outcomes
            ])),
            4,
        ),
        remaining_conflicts=round(float(np.mean([item.conflicts for item in outcomes]))),
        emissions_index=round(float(np.mean([item.emissions for item in outcomes])), 2),
        recovery_minute=round(float(np.mean([item.recovery for item in outcomes])), 2),
        confidence_low=round(max(0, mean_delay - 1.96 * standard_error), 2),
        confidence_high=round(mean_delay + 1.96 * standard_error, 2),
        per_flight=per_flight,
    )


def report_score(
    scenario: ScenarioSpec,
    candidate: PlanCandidate,
    intent: OperatorIntent | None,
) -> float:
    report = candidate.report
    # Rank all strategies against one common operator/default utility function.
    # Profile-specific weights generate diversity; they must not bias comparison.
    weights = profile_weights(PlanProfile.operator if intent else PlanProfile.balanced, intent)
    passenger_total = max(1, sum(flight.connections for flight in scenario.flights))
    normalized = {
        "delay": min(2, report.mean_delay_min / 30),
        "connections": min(2, report.missed_connections / passenger_total),
        "fuel_risk": report.fuel_risk_probability,
        "conflicts": min(2, report.remaining_conflicts / max(1, len(candidate.assignments))),
        "emissions": min(2, report.emissions_index / max(1, len(candidate.assignments) * 25)),
    }
    return round(sum(getattr(weights, key) * value for key, value in normalized.items()) * 100, 3)


_PARETO_EPSILON = {
    "delay": 0.35,
    "connections": 1.0,
    "fuel_risk": 0.005,
    "conflicts": 0.0,
    "emissions": 0.25,
}


def _pareto_vector(candidate: PlanCandidate) -> dict[str, float]:
    report = candidate.report
    return {
        "delay": float(report.p90_delay_min),
        "connections": float(report.missed_connections),
        "fuel_risk": float(report.fuel_risk_probability),
        "conflicts": float(report.remaining_conflicts),
        "emissions": float(report.emissions_index),
    }


def _epsilon_dominates(left: PlanCandidate, right: PlanCandidate) -> bool:
    left_values = _pareto_vector(left)
    right_values = _pareto_vector(right)
    no_worse = all(
        left_values[key] <= right_values[key] + _PARETO_EPSILON[key]
        for key in _PARETO_EPSILON
    )
    strictly_better = any(
        left_values[key] < right_values[key] - _PARETO_EPSILON[key]
        for key in _PARETO_EPSILON
    )
    return no_worse and strictly_better


def _outcome_target_key(
    candidate: PlanCandidate,
    intent: OperatorIntent | None,
) -> tuple[int, float]:
    """Lexicographically prefer plans satisfying confirmed probabilistic service targets."""
    if intent is None or intent.needs_confirmation:
        return (0, 0.0)
    scoped = sum(
        max(0, int(row.get("transfer_passengers", 0) or 0))
        for row in candidate.risk_shield.get("rows", [])
    )
    scoped = max(1, scoped)
    on_time_rate = max(0.0, 1.0 - candidate.report.missed_connections / scoped)
    tail_rate = max(
        0.0,
        float(candidate.risk_shield.get("connection_passengers_exposed", 0) or 0) / scoped,
    )
    gaps: list[float] = []
    if intent.minimum_connection_rate is not None:
        gaps.append(max(0.0, intent.minimum_connection_rate - on_time_rate))
    if intent.maximum_missed_connections is not None:
        gaps.append(
            max(0.0, candidate.report.missed_connections - intent.maximum_missed_connections)
            / scoped
        )
    if intent.maximum_tail_exposure_rate is not None:
        gaps.append(max(0.0, tail_rate - intent.maximum_tail_exposure_rate))
    failed = sum(gap > 1e-9 for gap in gaps)
    return failed, round(sum(gaps), 6)


def rank_pareto_candidates(
    candidates: list[PlanCandidate],
    intent: OperatorIntent | None = None,
) -> list[PlanCandidate]:
    """Rank safety-permission tiers first, then optimize within that tier's frontier."""
    if not candidates:
        return []
    vectors = {candidate.id: _pareto_vector(candidate) for candidate in candidates}
    frontier = [
        candidate for candidate in candidates
        if not any(
            other.id != candidate.id and _epsilon_dominates(other, candidate)
            for other in candidates
        )
    ]
    weights = profile_weights(PlanProfile.operator if intent else PlanProfile.balanced, intent)
    weight_map = {key: float(getattr(weights, key)) for key in _PARETO_EPSILON}
    weight_total = sum(weight_map.values()) or 1.0
    minima = {key: min(row[key] for row in vectors.values()) for key in _PARETO_EPSILON}
    maxima = {key: max(row[key] for row in vectors.values()) for key in _PARETO_EPSILON}
    frontier_ids = {candidate.id for candidate in frontier}

    for candidate in candidates:
        normalized = {
            key: (vectors[candidate.id][key] - minima[key]) / max(1e-9, maxima[key] - minima[key])
            for key in _PARETO_EPSILON
        }
        candidate.pareto_optimal = candidate.id in frontier_ids
        candidate.ideal_distance = round(math.sqrt(sum(
            weight_map[key] / weight_total * normalized[key] ** 2
            for key in _PARETO_EPSILON
        )), 4)

    guard_rank = {"clear": 0, "watch": 1, "escalate": 2}
    best_guard_rank = min(
        guard_rank.get(str(item.risk_shield.get("status", "watch")), 1)
        for item in candidates
    )
    guard_eligible = [
        item for item in candidates
        if guard_rank.get(str(item.risk_shield.get("status", "watch")), 1)
        == best_guard_rank
    ]
    eligible = [
        candidate for candidate in guard_eligible
        if not any(
            other.id != candidate.id and _epsilon_dominates(other, candidate)
            for other in guard_eligible
        )
    ]
    recommended = min(
        eligible,
        key=lambda item: (*_outcome_target_key(item, intent), item.ideal_distance, item.score, item.id),
    )
    return [
        recommended,
        *sorted(
            (candidate for candidate in candidates if candidate.id != recommended.id),
            key=lambda item: (
                guard_rank.get(str(item.risk_shield.get("status", "watch")), 1),
                not item.pareto_optimal,
                *_outcome_target_key(item, intent),
                item.ideal_distance, item.score, item.id,
            ),
        ),
    ]


def select_representative_candidates(
    ranked: list[PlanCandidate],
    limit: int = 3,
) -> list[PlanCandidate]:
    """Keep the knee plus passenger and emissions extremes before filling by rank."""
    if limit <= 0 or not ranked:
        return []
    selected = [ranked[0]]
    selected_ids = {ranked[0].id}
    for objective in ("connections", "emissions"):
        extreme = min(
            ranked,
            key=lambda item: (_pareto_vector(item)[objective], item.ideal_distance, item.id),
        )
        if extreme.id not in selected_ids:
            selected.append(extreme)
            selected_ids.add(extreme.id)
    for candidate in ranked:
        if len(selected) >= limit:
            break
        if candidate.id not in selected_ids:
            selected.append(candidate)
            selected_ids.add(candidate.id)
    return selected[:limit]


def pareto_summary(candidates: list[PlanCandidate]) -> dict[str, object]:
    rows = []
    for candidate in candidates:
        metrics = _pareto_vector(candidate)
        rows.append({
            "id": candidate.id,
            "title": candidate.title,
            "profile": candidate.profile.value,
            "pareto_optimal": candidate.pareto_optimal,
            "ideal_distance": candidate.ideal_distance,
            "score": candidate.score,
            "risk_shield_status": candidate.risk_shield.get("status", "watch"),
            **metrics,
        })
    return {
        "method": "conformal-risk-tier + epsilon-pareto + weighted-ideal-point",
        "epsilon": dict(_PARETO_EPSILON),
        "recommended_id": candidates[0].id if candidates else None,
        "frontier_ids": [candidate.id for candidate in candidates if candidate.pareto_optimal],
        "points": rows,
    }


def generate_ranked_candidates(
    scenario: ScenarioSpec,
    profiles: list[PlanProfile],
    intent: OperatorIntent | None = None,
    runs: int = 50,
    frozen_assignments: list[FlightAssignment] | None = None,
    hint_assignments: list[FlightAssignment] | None = None,
) -> tuple[list[PlanCandidate], InfeasibilityReport | None]:
    candidates: list[PlanCandidate] = []
    last_failure: InfeasibilityReport | None = None
    unique_profiles = list(dict.fromkeys(profiles))[:4]
    if intent and PlanProfile.operator not in unique_profiles:
        unique_profiles = [PlanProfile.operator, *unique_profiles][:4]

    requested_resilient = PlanProfile.resilient in unique_profiles
    regular_profiles = [
        profile for profile in unique_profiles if profile != PlanProfile.resilient
    ]
    # A resilient-only API request still needs one simulated residual bank before
    # the second CP-SAT pass can reserve calibrated timing slack.
    if requested_resilient and not regular_profiles:
        regular_profiles = [PlanProfile.balanced]

    signatures: set[tuple] = set()
    for profile in regular_profiles:
        candidate, failure = solve_candidate(
            scenario,
            profile,
            intent,
            frozen_assignments=frozen_assignments or [],
            hint_assignments=hint_assignments or [],
        )
        if candidate is None:
            last_failure = failure
            continue
        signature = tuple(
            (item.flight_id, item.runway, item.landing_minute, item.gate)
            for item in candidate.assignments
        )
        if signature in signatures:
            continue
        signatures.add(signature)
        candidate.report = simulate_candidate(scenario, candidate, runs=runs)
        candidate.score = report_score(scenario, candidate, intent)
        candidate.risk_shield = assess_conformal_risk(scenario, candidate)
        candidates.append(candidate)

    if requested_resilient and candidates:
        calibration = min(
            candidates,
            key=lambda item: (
                item.report.fuel_risk_probability,
                item.report.p90_delay_min,
                item.id,
            ),
        )
        landing_limits = conformal_landing_limits(scenario, calibration)
        resilient, failure = solve_candidate(
            scenario,
            PlanProfile.resilient,
            intent,
            frozen_assignments=frozen_assignments or [],
            latest_landing_by_flight=landing_limits,
            hint_assignments=hint_assignments or [],
        )
        if resilient is None:
            last_failure = failure
        else:
            signature = tuple(
                (item.flight_id, item.runway, item.landing_minute, item.gate)
                for item in resilient.assignments
            )
            if signature not in signatures:
                signatures.add(signature)
                resilient.report = simulate_candidate(scenario, resilient, runs=runs)
                resilient.score = report_score(scenario, resilient, intent)
                resilient.risk_shield = assess_conformal_risk(scenario, resilient)
                resilient.risk_shield["calibration_plan_id"] = calibration.id
                resilient.risk_shield["landing_limits"] = landing_limits
                resilient.risk_shield["two_stage_solver"] = (
                    "pilot simulation residuals -> conformal reserve -> CP-SAT"
                )
                candidates.append(resilient)

    if requested_resilient and candidates:
        shield_rank = {"clear": 0, "watch": 1, "escalate": 2}
        adaptive_reference = min(
            candidates,
            key=lambda item: (
                shield_rank.get(str(item.risk_shield.get("status", "watch")), 1),
                item.report.fuel_risk_probability,
                item.report.p90_delay_min,
                item.id,
            ),
        )
        twin_trust = assess_twin_trust(adaptive_reference)
        adaptive_envelope = build_adaptive_envelope(
            scenario,
            adaptive_reference,
            twin_trust,
        )
        adaptive_plan_id: str | None = None
        if adaptive_envelope["solver_allowed"]:
            adaptive, failure = solve_candidate(
                scenario,
                PlanProfile.resilient,
                intent,
                frozen_assignments=frozen_assignments or [],
                latest_landing_by_flight=adaptive_envelope["landing_limits"],
                turnaround_buffer_by_flight=adaptive_envelope.get(
                    "turnaround_buffer_by_flight", {}
                ),
                hint_assignments=hint_assignments or [],
            )
            if adaptive is None:
                last_failure = failure
                adaptive_envelope["status"] = "infeasible"
                adaptive_envelope["summary"] += (
                    " 收紧后的模型不可行，因此保留原候选并要求人工处置。"
                )
            else:
                adaptive.report = simulate_candidate(scenario, adaptive, runs=runs)
                adaptive.score = report_score(scenario, adaptive, intent)
                adaptive.risk_shield = assess_conformal_risk(scenario, adaptive)
                adaptive.title = "漂移自适应护航"
                adaptive.explanation = (
                    "序贯覆盖监测触发局部关注后，按失配里程碑使用 95% conformal"
                    "时间上界和显式工程余量重新约束 CP-SAT；飞机动作仍完全来自求解器。"
                )
                adaptive.constraint_proof.append(
                    "PASS 漂移响应按里程碑将 95% conformal 上界与工程余量写入 CP-SAT"
                )
                adaptive_signature = tuple(
                    (item.flight_id, item.runway, item.landing_minute, item.gate)
                    for item in adaptive.assignments
                )
                reference_by_flight = {
                    item.flight_id: item for item in adaptive_reference.assignments
                }
                changed = sum(
                    (
                        item.runway,
                        item.landing_minute,
                        item.gate,
                    )
                    != (
                        reference_by_flight[item.flight_id].runway,
                        reference_by_flight[item.flight_id].landing_minute,
                        reference_by_flight[item.flight_id].gate,
                    )
                    for item in adaptive.assignments
                    if item.flight_id in reference_by_flight
                )
                if adaptive_signature in signatures:
                    equivalent = next(
                        item
                        for item in candidates
                        if tuple(
                            (
                                assignment.flight_id,
                                assignment.runway,
                                assignment.landing_minute,
                                assignment.gate,
                            )
                            for assignment in item.assignments
                        ) == adaptive_signature
                    )
                    adaptive_plan_id = equivalent.id
                    adaptive_envelope["status"] = "applied_equivalent"
                else:
                    signatures.add(adaptive_signature)
                    candidates.append(adaptive)
                    adaptive_plan_id = adaptive.id
                    adaptive_envelope["status"] = "applied"
                adaptive_envelope["selected_plan_id"] = adaptive_plan_id
                adaptive_envelope["changed_assignments"] = changed
                adaptive_envelope["post_shield_status"] = (
                    adaptive.risk_shield.get("status", "watch")
                )
                adaptive_envelope["summary"] += (
                    f" 求解完成，调整 {changed} 架航班；"
                    f"复核结果 {str(adaptive.risk_shield.get('status', 'watch')).upper()}。"
                )
        for candidate in candidates:
            candidate.risk_shield["adaptive_envelope"] = {
                **adaptive_envelope,
                "applies_to_candidate": candidate.id == adaptive_plan_id,
            }

    candidates = rank_pareto_candidates(candidates, intent)
    selected = select_representative_candidates(candidates, limit=3)
    for index, candidate in enumerate(selected, start=1):
        candidate.rank = index
        shield_status = str(candidate.risk_shield.get("status", "watch"))
        if index == 1:
            frontier_clause = (
                "该候选位于全局 ε-Pareto 前沿"
                if candidate.pareto_optimal
                else "该候选以更高执行安全许可优先于全局 Pareto 方案"
            )
            candidate.explanation = (
                f"{candidate.explanation} {frontier_clause}，并在当前最低风险许可等级"
                f"（{shield_status.upper()}）中最接近加权理想点（距离 {candidate.ideal_distance:.3f}）；"
                f"{runs} 次固定种子仿真后，平均延误 {candidate.report.mean_delay_min:.1f} 分钟，"
                f"P90 {candidate.report.p90_delay_min:.1f} 分钟。"
            )
            candidate.why_not = [
                (
                    f"{other.title}的 Risk Shield 为 "
                    f"{str(other.risk_shield.get('status', 'watch')).upper()}，"
                    f"理想点距离 {other.ideal_distance:.3f}"
                    if other.pareto_optimal
                    else f"{other.title}在全部关键目标上被另一安全候选 ε-Pareto 支配"
                )
                for other in selected if other.id != candidate.id
            ][:2]
        else:
            candidate.why_not = [
                "该候选保留为可选权衡点，但不属于当前风险许可等级下的推荐折中点"
            ]
    return selected, None if selected else last_failure

def fcfs_baseline(scenario: ScenarioSpec) -> PlanCandidate:
    """Build a transparent, safety-aware FCFS heuristic without global optimization."""
    active = sorted(
        (flight for flight in scenario.flights if flight.state in {
            FlightState.inbound, FlightState.approach, FlightState.holding,
        }),
        key=lambda flight: (
            not flight.medical_priority,
            flight.scheduled_arrival,
            flight.id,
        ),
    )
    runways = [
        runway for runway in scenario.runways
        if runway.status.value == "open" or runway.reopen_minute is not None
    ]
    gates = [
        gate for gate in scenario.gates
        if gate.status.value not in {"reserved", "closed"}
        and (
            gate.status.value != "blocked"
            or scenario.current_minute < gate.available_minute < 180
        )
    ]
    if not active or not runways or not gates:
        return PlanCandidate(
            profile=PlanProfile.balanced,
            title="安全规则 · FCFS",
            assignments=[],
            actions=["没有可执行的 FCFS 基线"],
            report=empty_report(),
            score=0,
            explanation="按到达顺序执行基础安全规则，不调用全局优化。",
        )

    runway_ready = {
        runway.id: (
            scenario.current_minute
            if runway.status.value == "open"
            else int(runway.reopen_minute or scenario.current_minute)
        )
        for runway in runways
    }
    gate_ready = {
        gate.id: max(scenario.current_minute, gate.available_minute)
        for gate in gates
    }
    assignments: list[FlightAssignment] = []
    for flight in active:
        options: list[tuple[int, bool, bool, int, str, str]] = []
        for runway in runways:
            for gate in gates:
                landing = max(
                    scenario.current_minute,
                    flight.scheduled_arrival,
                    runway_ready[runway.id],
                    gate_ready[gate.id] - gate.taxi_minutes,
                )
                options.append((
                    landing,
                    gate.id != flight.planned_gate,
                    gate.remote,
                    gate.taxi_minutes,
                    runway.id,
                    gate.id,
                ))
        landing, _, _, _, runway_id, gate_id = min(options)
        runway = next(item for item in runways if item.id == runway_id)
        gate = next(item for item in gates if item.id == gate_id)
        runway_ready[runway.id] = landing + runway.separation_min
        gate_ready[gate.id] = (
            landing + gate.taxi_minutes + TURNAROUND_MINUTES
        )
        assignments.append(FlightAssignment(
            flight_id=flight.id,
            runway=runway.id,
            landing_minute=landing,
            gate=gate.id,
            hold_minutes=max(0, landing - flight.scheduled_arrival),
            taxi_minutes=gate.taxi_minutes,
            remote_stand=gate.remote,
        ))
    return PlanCandidate(
        profile=PlanProfile.balanced,
        title="安全规则 · FCFS",
        assignments=assignments,
        actions=[
            "按医疗优先与到达顺序选择最早安全跑道/机位，不做全局目标优化"
        ],
        report=empty_report(),
        score=0,
        explanation=(
            "透明的安全规则基线：满足已知资源可用时间与占用间隔，"
            "但不进行全局权衡。"
        ),
    )


# Backward-compatible private name used by the robustness regression tests.
_fcfs_baseline = fcfs_baseline


def counterfactual_summary(
    scenario: ScenarioSpec,
    recommended: PlanCandidate,
    user_plan: PlanCandidate | None = None,
) -> list[dict[str, float | str]]:
    baseline = fcfs_baseline(scenario)
    baseline.report = simulate_candidate(scenario, baseline, runs=30)
    rows: list[dict[str, float | str]] = [{
        "name": "无优化 · FCFS",
        "mean_delay": baseline.report.mean_delay_min,
        "p90_delay": baseline.report.p90_delay_min,
        "missed": baseline.report.missed_connections,
        "fuel_risk": baseline.report.fuel_risk_probability,
        "source": "30-run-simpy-baseline",
    }]
    if user_plan is not None:
        rows.append({
            "name": "用户方案",
            "mean_delay": user_plan.report.mean_delay_min,
            "p90_delay": user_plan.report.p90_delay_min,
            "missed": user_plan.report.missed_connections,
            "fuel_risk": user_plan.report.fuel_risk_probability,
            "source": "user-plan-simulation",
        })
    rows.append({
        "name": "推荐方案",
        "mean_delay": recommended.report.mean_delay_min,
        "p90_delay": recommended.report.p90_delay_min,
        "missed": recommended.report.missed_connections,
        "fuel_risk": recommended.report.fuel_risk_probability,
        "source": f"{recommended.report.runs}-run-simpy-candidate",
    })
    return rows
