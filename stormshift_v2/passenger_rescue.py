"""Passenger reaccommodation after an airport recovery schedule is selected.

The flight plan controls scarce runway and gate resources. This module handles
the downstream passenger problem with a lexicographic capacitated min-cost flow:
first maximize same-day reaccommodation, then minimize robust connection wait,
partner-airline use, and intervention cost.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

import networkx as nx

from .schemas import PlanCandidate, ScenarioSpec


DESTINATIONS = ("LHR", "SFO", "SYD", "DXB")
MIN_ROBUST_CONNECTION_MIN = 12
STRANDED_BASE_COST = 100_000


def _stable_int(seed: int, *parts: str, modulo: int) -> int:
    material = ":".join((str(seed), *parts)).encode("utf-8")
    return int(hashlib.sha256(material).hexdigest()[:12], 16) % modulo


def _risk_allocations(
    scenario: ScenarioSpec,
    plan: PlanCandidate,
) -> dict[str, int]:
    """Largest-remainder allocation that preserves the aggregate missed count."""
    flights = {flight.id: flight for flight in scenario.flights}
    assignment_ids = [
        assignment.flight_id
        for assignment in plan.assignments
        if assignment.flight_id in flights and flights[assignment.flight_id].connections
    ]
    target = min(
        sum(flights[flight_id].connections for flight_id in assignment_ids),
        max(0, int(plan.report.missed_connections)),
    )
    if not assignment_ids or not target:
        return {}

    weights = {
        flight_id: max(
            0.0,
            float(
                plan.report.per_flight.get(flight_id, {}).get(
                    "expected_missed_passengers", 0.0
                )
            ),
        )
        for flight_id in assignment_ids
    }
    if sum(weights.values()) <= 0:
        exposed = {
            str(row.get("flight_id"))
            for row in plan.risk_shield.get("rows", [])
            if row.get("transfer_exposed")
        }
        weights = {
            flight_id: float(flights[flight_id].connections)
            if flight_id in exposed else 0.0
            for flight_id in assignment_ids
        }
    if sum(weights.values()) <= 0:
        weights = {
            flight_id: float(flights[flight_id].connections)
            for flight_id in assignment_ids
        }

    total_weight = sum(weights.values())
    raw = {
        flight_id: target * weight / total_weight
        for flight_id, weight in weights.items()
    }
    allocated = {
        flight_id: min(
            flights[flight_id].connections,
            math.floor(value),
        )
        for flight_id, value in raw.items()
    }
    remaining = target - sum(allocated.values())
    order = sorted(
        assignment_ids,
        key=lambda flight_id: (
            raw[flight_id] - math.floor(raw[flight_id]),
            weights[flight_id],
            flight_id,
        ),
        reverse=True,
    )
    while remaining > 0:
        progressed = False
        for flight_id in order:
            if allocated[flight_id] >= flights[flight_id].connections:
                continue
            allocated[flight_id] += 1
            remaining -= 1
            progressed = True
            if remaining == 0:
                break
        if not progressed:
            break
    return {
        flight_id: passengers
        for flight_id, passengers in allocated.items()
        if passengers > 0
    }


def _destination(scenario: ScenarioSpec, flight_id: str) -> str:
    return DESTINATIONS[
        _stable_int(scenario.seed, flight_id, "destination", modulo=len(DESTINATIONS))
    ]


def _build_options(
    scenario: ScenarioSpec,
    destinations: set[str],
) -> list[dict[str, Any]]:
    waves = (
        (38, "同航司下一班", "same_airline", 5, 9),
        (68, "联盟伙伴航班", "alliance_partner", 8, 12),
        (108, "晚间恢复波次", "late_wave", 11, 16),
    )
    options: list[dict[str, Any]] = []
    for destination in sorted(destinations):
        for index, (offset, label, kind, minimum, spread) in enumerate(waves, start=1):
            capacity = minimum + _stable_int(
                scenario.seed,
                destination,
                str(index),
                "capacity",
                modulo=spread,
            )
            options.append({
                "id": f"RCV-{destination}-{index}",
                "destination": destination,
                "route": f"NHI → {destination}",
                "departure_minute": scenario.current_minute + offset,
                "capacity": capacity,
                "kind": kind,
                "label": label,
                "synthetic": True,
            })
    return options


def optimize_passenger_rescue(
    scenario: ScenarioSpec,
    plan: PlanCandidate,
) -> dict[str, Any]:
    """Build and solve the auditable post-plan passenger continuity flow."""
    flights = {flight.id: flight for flight in scenario.flights}
    assignments = {item.flight_id: item for item in plan.assignments}
    allocations = _risk_allocations(scenario, plan)
    tail_exposed = int(
        plan.risk_shield.get("connection_passengers_exposed", 0) or 0
    )
    at_risk = sum(allocations.values())
    if not at_risk:
        return {
            "method": "lexicographic robust capacitated min-cost flow",
            "status": "not_needed",
            "source_plan_id": plan.id,
            "at_risk_passengers": 0,
            "reaccommodated_passengers": 0,
            "remaining_stranded": 0,
            "same_day_recovery_rate": 1.0,
            "tail_watch_only": tail_exposed,
            "mean_extra_wait_min": 0.0,
            "p90_extra_wait_min": 0.0,
            "estimated_intervention_cost": 0,
            "partner_airline_passengers": 0,
            "groups": [],
            "options": [],
            "flows": [],
            "constraints": {
                "flow_conservation": True,
                "seat_capacity": True,
                "robust_connection_window": True,
            },
            "summary": "当前方案没有产生预计错失转机旅客，无需启动改签流。",
            "caveat": "后续航班与座位容量为合成演练数据，不代表真实库存。",
        }

    group_rows: list[dict[str, Any]] = []
    for flight_id, passengers in allocations.items():
        assignment = assignments[flight_id]
        metrics = plan.report.per_flight.get(flight_id, {})
        upper_gate_in = (
            float(assignment.landing_minute)
            + assignment.taxi_minutes
            + float(metrics.get("eibt_q90_radius", 0.0))
        )
        group_rows.append({
            "id": f"group:{flight_id}",
            "flight_id": flight_id,
            "destination": _destination(scenario, flight_id),
            "at_risk_passengers": passengers,
            "original_connections": flights[flight_id].connections,
            "miss_probability": float(metrics.get("miss_probability", 0.0)),
            "upper_gate_in_90": round(upper_gate_in, 1),
            "minimum_rebook_departure": round(
                upper_gate_in + MIN_ROBUST_CONNECTION_MIN,
                1,
            ),
        })

    options = _build_options(
        scenario,
        {row["destination"] for row in group_rows},
    )
    graph = nx.DiGraph()
    source, sink, care = "source", "sink", "care"
    graph.add_node(source, demand=-at_risk)
    graph.add_node(sink, demand=at_risk)
    graph.add_node(care, demand=0)
    graph.add_edge(care, sink, capacity=at_risk, weight=0)

    option_by_id = {row["id"]: row for row in options}
    for option in options:
        graph.add_node(option["id"], demand=0)
        graph.add_edge(
            option["id"],
            sink,
            capacity=int(option["capacity"]),
            weight=0,
        )

    for group in group_rows:
        group_id = group["id"]
        passengers = int(group["at_risk_passengers"])
        graph.add_node(group_id, demand=0)
        graph.add_edge(source, group_id, capacity=passengers, weight=0)
        vulnerability = round(
            group["miss_probability"] * 800
            + min(300, group["original_connections"] * 3)
        )
        graph.add_edge(
            group_id,
            care,
            capacity=passengers,
            weight=STRANDED_BASE_COST + vulnerability,
        )
        for option in options:
            if option["destination"] != group["destination"]:
                continue
            if option["departure_minute"] < group["minimum_rebook_departure"]:
                continue
            wait = option["departure_minute"] - group["upper_gate_in_90"]
            partner_penalty = {
                "same_airline": 0,
                "alliance_partner": 260,
                "late_wave": 100,
            }[option["kind"]]
            tie_break = _stable_int(
                scenario.seed,
                group["flight_id"],
                option["id"],
                modulo=17,
            )
            cost = round(wait * 10) + partner_penalty + tie_break
            graph.add_edge(
                group_id,
                option["id"],
                capacity=passengers,
                weight=cost,
            )

    flow = nx.min_cost_flow(graph)
    flows: list[dict[str, Any]] = []
    option_allocated = {option["id"]: 0 for option in options}
    wait_samples: list[float] = []
    partner_passengers = 0
    estimated_cost = 0
    for group in group_rows:
        group_flow = flow[group["id"]]
        rescued = 0
        stranded = int(group_flow.get(care, 0))
        for option_id, passengers in group_flow.items():
            passengers = int(passengers)
            if not passengers or option_id not in option_by_id:
                continue
            option = option_by_id[option_id]
            rescued += passengers
            option_allocated[option_id] += passengers
            wait = round(
                option["departure_minute"] - group["upper_gate_in_90"],
                1,
            )
            wait_samples.extend([wait] * passengers)
            if option["kind"] == "alliance_partner":
                partner_passengers += passengers
                estimated_cost += passengers * 115
            elif option["kind"] == "late_wave":
                estimated_cost += passengers * 75
            else:
                estimated_cost += passengers * 40
            flows.append({
                "from_flight": group["flight_id"],
                "to_option": option_id,
                "destination": group["destination"],
                "passengers": passengers,
                "departure_minute": option["departure_minute"],
                "robust_wait_min": wait,
                "kind": option["kind"],
                "label": option["label"],
            })
        estimated_cost += stranded * 280
        group["reaccommodated"] = rescued
        group["remaining_stranded"] = stranded
        group["service_rate"] = round(
            rescued / max(1, group["at_risk_passengers"]),
            4,
        )

    for option in options:
        option["allocated"] = option_allocated[option["id"]]
        option["remaining_seats"] = option["capacity"] - option["allocated"]

    reaccommodated = sum(row["reaccommodated"] for row in group_rows)
    stranded = at_risk - reaccommodated
    wait_samples.sort()
    p90_index = max(0, math.ceil(.9 * len(wait_samples)) - 1)
    mean_wait = (
        sum(wait_samples) / len(wait_samples) if wait_samples else 0.0
    )
    p90_wait = wait_samples[p90_index] if wait_samples else 0.0
    capacity_ok = all(
        option["allocated"] <= option["capacity"] for option in options
    )
    window_ok = all(
        flow_row["departure_minute"]
        >= next(
            group["minimum_rebook_departure"]
            for group in group_rows
            if group["flight_id"] == flow_row["from_flight"]
        )
        for flow_row in flows
    )
    conservation_ok = (
        sum(row["reaccommodated"] + row["remaining_stranded"] for row in group_rows)
        == at_risk
    )
    status = "ready" if stranded == 0 else "capacity_shortfall"
    if stranded:
        summary = (
            f"容量流已为 {reaccommodated}/{at_risk} 名预计错失旅客锁定同日替代，"
            f"仍有 {stranded} 人进入人工关怀与候补。"
        )
    else:
        summary = (
            f"容量流已为全部 {at_risk} 名预计错失旅客找到满足 90% 到达上界"
            "与最短衔接时间的同日替代。"
        )
    actions = [
        (
            f"{row['from_flight']} → {row['to_option']}："
            f"{row['passengers']} 人，T+{row['departure_minute']}，"
            f"稳健等待 {row['robust_wait_min']:.1f} 分钟"
        )
        for row in sorted(
            flows,
            key=lambda item: (
                item["departure_minute"],
                item["from_flight"],
                item["to_option"],
            ),
        )
    ]

    return {
        "method": "lexicographic robust capacitated min-cost flow",
        "status": status,
        "source_plan_id": plan.id,
        "at_risk_passengers": at_risk,
        "reaccommodated_passengers": reaccommodated,
        "remaining_stranded": stranded,
        "same_day_recovery_rate": round(
            reaccommodated / max(1, at_risk),
            4,
        ),
        "tail_watch_only": max(0, tail_exposed - at_risk),
        "mean_extra_wait_min": round(mean_wait, 1),
        "p90_extra_wait_min": round(p90_wait, 1),
        "estimated_intervention_cost": estimated_cost,
        "partner_airline_passengers": partner_passengers,
        "groups": group_rows,
        "options": options,
        "flows": flows,
        "actions": actions,
        "constraints": {
            "flow_conservation": conservation_ok,
            "seat_capacity": capacity_ok,
            "robust_connection_window": window_ok,
        },
        "counterfactual": {
            "without_rescue_stranded": at_risk,
            "with_rescue_stranded": stranded,
            "stranding_avoided": reaccommodated,
        },
        "summary": summary,
        "source": (
            "incoming risk from seeded SimPy + 90% conformal EIBT; "
            "synthetic onward inventory"
        ),
        "caveat": (
            "替代航班、目的地、座位和费用为可复现实验数据；真实部署必须接入航司"
            "PSS/库存与最低衔接时间规则，Agent 不可自行出票。"
        ),
    }

