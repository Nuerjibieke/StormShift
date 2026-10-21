"""Formal argumentation over conflicting multi-agent operational claims.

The graph is a deterministic, replayable safety layer: claims attack incompatible
claims about the same fact, stronger evidence defeats weaker evidence, and the
grounded extension determines which arguments may enter the solver.  It does not
claim that an accepted source is objectively true.
"""

from __future__ import annotations

import json
import math
from itertools import combinations
from typing import Any

import networkx as nx


FRESHNESS_SLA_MIN = 10
_TIE_EPSILON = 0.005


def _value_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _source_authority(source: str) -> float:
    normalized = source.lower()
    if "confirmed incident report" in normalized or "emergency desk" in normalized:
        return 1.0
    if any(
        token in normalized
        for token in (
            "airport operations database",
            "ground operations",
            "airline occ",
            "runway inspection",
        )
    ):
        return .95
    if "weather" in normalized:
        return .90
    if "a-cdm milestone cache" in normalized:
        return .72
    if "filed flight-plan snapshot" in normalized:
        return .68
    return .80


def _strength(claim: dict[str, Any]) -> tuple[float, dict[str, float]]:
    authority = _source_authority(str(claim.get("source", "")))
    age = max(0.0, float(claim.get("age_min", 0.0)))
    freshness = math.exp(-age / 20.0)
    confidence = min(1.0, max(0.0, float(claim.get("confidence", 0.0))))
    total = .55 * authority + .25 * freshness + .20 * confidence
    return round(total, 6), {
        "authority": round(authority, 3),
        "freshness": round(freshness, 3),
        "confidence": round(confidence, 3),
    }


def _grounded_extension(graph: nx.DiGraph) -> set[str]:
    """Compute Dung's least fixed point of the characteristic function."""
    accepted: set[str] = set()
    while True:
        next_set: set[str] = set()
        for argument in graph.nodes:
            attackers = set(graph.predecessors(argument))
            defended = all(
                any(graph.has_edge(defender, attacker) for defender in accepted)
                for attacker in attackers
            )
            if defended:
                next_set.add(str(argument))
        if next_set == accepted:
            return accepted
        accepted = next_set


def resolve_operational_claims(
    claims: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a grounded, conflict-free solver admission decision."""
    graph = nx.DiGraph()
    strengths: dict[str, float] = {}
    components: dict[str, dict[str, float]] = {}
    claim_by_id = {str(claim["id"]): claim for claim in claims}

    for claim_id, claim in claim_by_id.items():
        total, parts = _strength(claim)
        strengths[claim_id] = total
        components[claim_id] = parts
        graph.add_node(claim_id)

    by_key: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        by_key.setdefault(str(claim["key"]), []).append(claim)

    edges: list[dict[str, Any]] = []
    conflict_keys: list[str] = []
    for key, group in sorted(by_key.items()):
        values = {_value_key(item.get("value")) for item in group}
        if len(values) <= 1:
            continue
        conflict_keys.append(key)
        for left, right in combinations(sorted(group, key=lambda item: str(item["id"])), 2):
            if _value_key(left.get("value")) == _value_key(right.get("value")):
                continue
            left_id = str(left["id"])
            right_id = str(right["id"])
            gap = strengths[left_id] - strengths[right_id]
            if abs(gap) <= _TIE_EPSILON:
                directions = ((left_id, right_id), (right_id, left_id))
                basis = "evidential tie; neither claim defeats the other"
            elif gap > 0:
                directions = ((left_id, right_id),)
                basis = "higher authority, freshness, and confidence score"
            else:
                directions = ((right_id, left_id),)
                basis = "higher authority, freshness, and confidence score"
            for source_id, target_id in directions:
                graph.add_edge(source_id, target_id)
                edges.append({
                    "from": source_id,
                    "to": target_id,
                    "fact_key": key,
                    "basis": basis,
                    "score_gap": round(
                        strengths[source_id] - strengths[target_id],
                        4,
                    ),
                })

    grounded = _grounded_extension(graph)
    stale_ids = {
        claim_id
        for claim_id, claim in claim_by_id.items()
        if bool(claim.get("critical", True))
        and float(claim.get("age_min", 0)) > FRESHNESS_SLA_MIN
    }
    accepted = grounded - stale_ids
    rejected = {
        str(target)
        for source in accepted
        for target in graph.successors(source)
    } | stale_ids
    undecided = set(graph.nodes) - accepted - rejected
    unresolved_keys = {
        str(claim_by_id[claim_id]["key"])
        for claim_id in undecided
        if bool(claim_by_id[claim_id].get("critical", True))
    }
    accepted_keys = {
        str(claim_by_id[claim_id]["key"])
        for claim_id in accepted
    }
    unresolved_keys.update(
        key
        for key, group in by_key.items()
        if any(bool(item.get("critical", True)) for item in group)
        and key not in accepted_keys
    )
    unresolved_keys = sorted(unresolved_keys)
    internal_attacks = [
        (source, target)
        for source, target in graph.edges
        if source in accepted and target in accepted
    ]
    verdicts: dict[str, str] = {
        claim_id: (
            "accepted"
            if claim_id in accepted
            else "rejected"
            if claim_id in rejected
            else "undecided"
        )
        for claim_id in graph.nodes
    }
    nodes = [
        {
            "id": claim_id,
            "fact_key": str(claim["key"]),
            "value": claim.get("value"),
            "source": str(claim.get("source", "")),
            "age_min": int(claim.get("age_min", 0)),
            "authority": components[claim_id]["authority"],
            "freshness": components[claim_id]["freshness"],
            "confidence": components[claim_id]["confidence"],
            "strength": strengths[claim_id],
            "verdict": verdicts[claim_id],
        }
        for claim_id, claim in sorted(claim_by_id.items())
    ]

    status = "blocked" if unresolved_keys or internal_attacks else "resolved"
    summary = (
        f"Grounded semantics 接受 {len(accepted)} 条、击败 {len(rejected)} 条、"
        f"保留 {len(undecided)} 条未决声明；"
        + (
            f"{len(unresolved_keys)} 个关键事实必须人工确认"
            if unresolved_keys
            else "接受集合无内部冲突，可进入约束编译"
        )
    )
    return {
        "method": (
            "Dung grounded argumentation + evidence-strength defeat graph "
            "(NetworkX)"
        ),
        "status": status,
        "claim_count": len(claims),
        "conflict_keys": conflict_keys,
        "attack_count": len(edges),
        "accepted_ids": sorted(accepted),
        "rejected_ids": sorted(rejected),
        "undecided_ids": sorted(undecided),
        "unresolved_keys": unresolved_keys,
        "conflict_free": not internal_attacks,
        "nodes": nodes,
        "edges": edges,
        "summary": summary,
        "caveat": (
            "形式化论证保证选择规则可追溯且接受集合无内部攻击；"
            "它不能证明数据源本身真实，现场权威和新鲜度策略仍需治理。"
        ),
    }
