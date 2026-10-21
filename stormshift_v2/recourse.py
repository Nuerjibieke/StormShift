"""Causal resource graph for stability-preserving disruption recourse."""

from __future__ import annotations

from typing import Any

import networkx as nx

from .schemas import EventSpec, FlightAssignment, PlanCandidate, ScenarioSpec


def _node(kind: str, value: str) -> str:
    return f"{kind}:{value}"


def build_operational_graph(
    scenario: ScenarioSpec,
    plan: PlanCandidate,
    event: EventSpec,
) -> nx.DiGraph:
    """Build a small, inspectable dependency graph for the current plan."""
    graph = nx.DiGraph()
    resource_id = event.resource_id or event.type.value
    resource = _node("resource", resource_id)
    graph.add_node(resource, kind="resource", label=resource_id)

    zone_name = str(event.payload.get("zone", "affected zone"))
    zone = _node("zone", zone_name)
    graph.add_node(zone, kind="zone", label=zone_name)
    graph.add_edge(resource, zone, relation="disabled_in")

    affected_gates = {
        str(value) for value in event.payload.get("affected_gates", [])
    }
    affected_runways = {
        str(value) for value in event.payload.get("affected_runways", [])
    }
    for gate in scenario.gates:
        gate_node = _node("gate", gate.id)
        graph.add_node(gate_node, kind="gate", label=gate.id)
        if gate.id in affected_gates:
            graph.add_edge(zone, gate_node, relation="restricts")
    for runway in scenario.runways:
        runway_node = _node("runway", runway.id)
        graph.add_node(runway_node, kind="runway", label=runway.id)
        if runway.id in affected_runways:
            graph.add_edge(zone, runway_node, relation="restricts")

    flight_by_id = {flight.id: flight for flight in scenario.flights}
    for assignment in plan.assignments:
        flight = flight_by_id[assignment.flight_id]
        flight_node = _node("flight", assignment.flight_id)
        graph.add_node(
            flight_node,
            kind="flight",
            label=assignment.flight_id,
            connections=flight.connections,
            landing_minute=assignment.landing_minute,
        )
        graph.add_edge(_node("gate", assignment.gate), flight_node, relation="serves")
        graph.add_edge(_node("runway", assignment.runway), flight_node, relation="sequences")
        if flight.connections:
            group = _node("connection", assignment.flight_id)
            graph.add_node(group, kind="connection", label=f"{flight.connections} pax")
            graph.add_edge(flight_node, group, relation="protects")
    return graph


def analyze_recourse_impact(
    scenario: ScenarioSpec,
    plan: PlanCandidate,
    event: EventSpec,
) -> dict[str, Any]:
    """Return the smallest future assignment set causally exposed to an event."""
    graph = build_operational_graph(scenario, plan, event)
    resource_id = event.resource_id or event.type.value
    source = _node("resource", resource_id)
    failure_minute = event.minute
    pending = [
        assignment
        for assignment in plan.assignments
        if assignment.landing_minute > failure_minute
    ]
    pending_ids = {assignment.flight_id for assignment in pending}
    affected_ids: list[str] = []
    paths: list[list[str]] = []
    for target, data in graph.nodes(data=True):
        if data.get("kind") != "flight":
            continue
        flight_id = str(data["label"])
        if flight_id not in pending_ids or not nx.has_path(graph, source, target):
            continue
        affected_ids.append(flight_id)
        path = nx.shortest_path(graph, source, target)
        paths.append([str(graph.nodes[item].get("label", item)) for item in path])

    # An event with no explicit topology must fail safe: re-open the future scope.
    if not affected_ids and pending:
        affected_ids = [assignment.flight_id for assignment in pending]
        paths = [[resource_id, "unresolved dependency", flight_id] for flight_id in affected_ids]

    affected = set(affected_ids)
    preserved_ids = [
        assignment.flight_id for assignment in pending
        if assignment.flight_id not in affected
    ]
    dependency_edges = [
        {
            "from": str(graph.nodes[left].get("label", left)),
            "to": str(graph.nodes[right].get("label", right)),
            "relation": str(data.get("relation", "depends_on")),
        }
        for left, right, data in graph.edges(data=True)
        if nx.has_path(graph, source, left)
    ]
    return {
        "strategy": "causal_local_repair",
        "trigger_resource": resource_id,
        "zone": str(event.payload.get("zone", "affected zone")),
        "affected_flight_ids": affected_ids,
        "preserved_flight_ids": preserved_ids,
        "affected_count": len(affected_ids),
        "frozen_count": len(preserved_ids),
        "pending_count": len(pending),
        "preservation_rate": round(100 * len(preserved_ids) / max(1, len(pending))),
        "dependency_paths": paths,
        "dependency_edges": dependency_edges,
        "boundary_proof": "未受影响航班的跑道、机位和落地时刻被固定为原批准方案",
    }


def frozen_assignments(
    plan: PlanCandidate,
    impact: dict[str, Any],
) -> list[FlightAssignment]:
    preserved = set(impact.get("preserved_flight_ids", []))
    return [
        assignment.model_copy(deep=True)
        for assignment in plan.assignments
        if assignment.flight_id in preserved
    ]


def summarize_repair(
    previous: PlanCandidate,
    repaired: PlanCandidate,
    impact: dict[str, Any],
) -> dict[str, Any]:
    """Attach auditable schedule-churn metrics after the repair solve."""
    before = {item.flight_id: item for item in previous.assignments}
    changed: list[str] = []
    for item in repaired.assignments:
        original = before.get(item.flight_id)
        if original is None:
            changed.append(item.flight_id)
            continue
        if (
            original.runway,
            original.landing_minute,
            original.gate,
        ) != (
            item.runway,
            item.landing_minute,
            item.gate,
        ):
            changed.append(item.flight_id)
    result = dict(impact)
    result["source_plan_id"] = previous.id
    result["replacement_plan_id"] = repaired.id
    result["changed_flight_ids"] = changed
    result["changed_count"] = len(changed)
    result["preserved_verified"] = all(
        flight_id not in changed for flight_id in impact.get("preserved_flight_ids", [])
    )
    result["summary"] = (
        f"因果图锁定 {impact.get('frozen_count', 0)} 架未受影响航班，"
        f"仅开放 {impact.get('affected_count', 0)} 架进入局部重规划"
    )
    return result

