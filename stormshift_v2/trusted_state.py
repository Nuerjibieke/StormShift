"""Evidence reconciliation and a verifiable solver-input contract.

The module deliberately separates raw operational claims from facts that are
allowed to enter CP-SAT.  It is deterministic so the complete decision basis
can be replayed without an LLM or an external service.
"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from .argumentation import FRESHNESS_SLA_MIN, resolve_operational_claims
from .schemas import DataMode, EventSpec, EventType, ResourceStatus, ScenarioSpec


def _clock_label(minute: int) -> str:
    return f"T{minute:+d}"


def _fact(
    fact_id: str,
    key: str,
    value: Any,
    *,
    source: str,
    observed_minute: int,
    now: int,
    confidence: float,
    status: str = "confirmed",
    solver_eligible: bool = True,
    critical: bool = True,
    note: str = "",
) -> dict[str, Any]:
    return {
        "id": fact_id,
        "key": key,
        "value": value,
        "source": source,
        "observed_minute": observed_minute,
        "observed_at": _clock_label(observed_minute),
        "age_min": max(0, now - observed_minute),
        "confidence": round(confidence, 2),
        "status": status,
        "solver_eligible": solver_eligible,
        "critical": critical,
        "note": note,
    }


def _event_keys(event: EventSpec | None) -> set[str]:
    if event is None:
        return set()
    if event.type in {EventType.gate_outage, EventType.ground_vehicle_failure}:
        gate_ids = {
            str(value) for value in event.payload.get("affected_gates", [])
        }
        if event.resource_id and event.type == EventType.gate_outage:
            gate_ids.add(event.resource_id)
        return {
            item
            for gate_id in gate_ids
            for item in (
                f"gate.{gate_id}.status",
                f"gate.{gate_id}.available_minute",
            )
        }
    if event.type in {EventType.runway_closed, EventType.runway_reopened}:
        return {
            f"runway.{event.resource_id}.status",
            f"runway.{event.resource_id}.reopen_minute",
        }
    if event.type == EventType.medical_diversion:
        flight_id = event.resource_id or str(event.payload.get("flight_id", "MED901"))
        return {
            f"flight.{flight_id}.medical_priority",
            f"flight.{flight_id}.fuel_margin_min",
        }
    return set()


def _event_source(event: EventSpec | None, key: str, fallback: str) -> str:
    if event is None or key not in _event_keys(event):
        return fallback
    return f"Confirmed incident report · {event.type.value} · {event.id}"


def build_trusted_operational_state(
    scenario: ScenarioSpec,
    data_mode: DataMode | str,
    *,
    previous: dict[str, Any] | None = None,
    event: EventSpec | None = None,
) -> dict[str, Any]:
    """Reconcile raw claims and compile the exact facts allowed into CP-SAT."""
    now = scenario.current_minute
    mode = data_mode.value if isinstance(data_mode, DataMode) else str(data_mode)
    claims: list[dict[str, Any]] = []
    resolution_log: list[dict[str, Any]] = []

    claims.append(_fact(
        "weather:severity:current",
        "weather.severity",
        scenario.weather.severity,
        source=scenario.weather.source or "Weather Cell",
        observed_minute=now,
        now=now,
        confidence=.91 if mode == DataMode.live.value else .98,
        note="Weather severity used by risk and capacity models.",
    ))

    for runway in scenario.runways:
        status_key = f"runway.{runway.id}.status"
        reopen_key = f"runway.{runway.id}.reopen_minute"
        claims.append(_fact(
            f"runway:{runway.id}:status",
            status_key,
            runway.status.value,
            source=_event_source(event, status_key, "Airport Operations Database"),
            observed_minute=now,
            now=now,
            confidence=.99,
            note="Authoritative operational runway state.",
        ))
        claims.append(_fact(
            f"runway:{runway.id}:reopen",
            reopen_key,
            runway.reopen_minute,
            source=_event_source(event, reopen_key, "Runway Inspection + Weather Cell"),
            observed_minute=now,
            now=now,
            confidence=.94 if runway.reopen_minute is not None else .99,
            note="Earliest runway use boundary; null means no forecast boundary.",
        ))

    for gate in scenario.gates:
        status_key = f"gate.{gate.id}.status"
        available_key = f"gate.{gate.id}.available_minute"
        if scenario.id == "storm" and gate.id == "A2":
            claims.append(_fact(
                "gate:A2:availability:aodb",
                available_key,
                15,
                source="A-CDM milestone cache",
                observed_minute=-8,
                now=now,
                confidence=.76,
                status="conflicted_excluded",
                solver_eligible=False,
                note="Older target conflicts with the ground-handler readiness report.",
            ))
            resolution_log.extend([
                {
                    "action": "query",
                    "target": "Gate & Ground Agent",
                    "fact_key": available_key,
                    "message": "A-CDM says T+15 while ground operations report T+28.",
                },
                {
                    "action": "confirm",
                    "target": "Ground Handler",
                    "fact_key": available_key,
                    "accepted_value": gate.available_minute,
                    "message": f"Ground Handler confirms A2 ready at T+{gate.available_minute}.",
                },
                {
                    "action": "abstain",
                    "target": "Constraint Compiler",
                    "fact_key": available_key,
                    "rejected_value": 15,
                    "message": "The conflicting cached value is retained for audit but excluded from CP-SAT.",
                },
            ])
        claims.append(_fact(
            f"gate:{gate.id}:status",
            status_key,
            gate.status.value,
            source=_event_source(event, status_key, "Ground Operations"),
            observed_minute=now,
            now=now,
            confidence=.99,
            note="Confirmed stand state.",
        ))
        claims.append(_fact(
            f"gate:{gate.id}:availability",
            available_key,
            gate.available_minute,
            source=_event_source(event, available_key, "Ground Operations"),
            observed_minute=now,
            now=now,
            confidence=.98,
            note="Confirmed earliest in-block availability.",
        ))

    for flight in scenario.flights:
        fuel_key = f"flight.{flight.id}.fuel_margin_min"
        if scenario.id == "storm" and flight.id == "SK218":
            claims.append(_fact(
                "flight:SK218:fuel:flight-plan",
                fuel_key,
                38,
                source="Filed flight-plan snapshot",
                observed_minute=-14,
                now=now,
                confidence=.69,
                status="stale_excluded",
                solver_eligible=False,
                note="Age exceeds the exercise freshness SLA; current OCC report supersedes it.",
            ))
            resolution_log.append({
                "action": "abstain",
                "target": "Constraint Compiler",
                "fact_key": fuel_key,
                "rejected_value": 38,
                "message": "Stale filed fuel margin excluded; use the current Airline OCC report.",
            })
        claims.append(_fact(
            f"flight:{flight.id}:fuel:occ",
            fuel_key,
            flight.fuel_margin_min,
            source="Airline OCC current report",
            observed_minute=now,
            now=now,
            confidence=.97,
            note="Remaining airborne fuel clock; converted to an absolute landing deadline.",
        ))
        if flight.medical_priority:
            priority_key = f"flight.{flight.id}.medical_priority"
            claims.append(_fact(
                f"flight:{flight.id}:medical",
                priority_key,
                True,
                source=_event_source(event, priority_key, "Airport Emergency Desk"),
                observed_minute=now,
                now=now,
                confidence=1.0,
                note="Non-overridable emergency-priority fact.",
            ))

    argumentation = resolve_operational_claims(claims)
    accepted_ids = set(argumentation["accepted_ids"])
    rejected_ids = set(argumentation["rejected_ids"])
    for claim in claims:
        claim_id = str(claim["id"])
        if claim_id in accepted_ids:
            claim["status"] = "confirmed"
            claim["solver_eligible"] = True
        elif claim_id in rejected_ids:
            claim["status"] = (
                "stale_excluded"
                if float(claim.get("age_min", 0)) > FRESHNESS_SLA_MIN
                else "conflicted_excluded"
            )
            claim["solver_eligible"] = False
        else:
            claim["status"] = "disputed_unresolved"
            claim["solver_eligible"] = False
    selected_claims = [item for item in claims if item["solver_eligible"]]
    solver_values = {item["key"]: item["value"] for item in selected_claims}
    compiled_constraints: list[dict[str, Any]] = []
    for runway in scenario.runways:
        if runway.status == ResourceStatus.closed:
            compiled_constraints.append({
                "id": f"runway-window:{runway.id}",
                "kind": "hard_safety",
                "rule": (
                    f"{runway.id} cannot be assigned before T+{runway.reopen_minute}"
                    if runway.reopen_minute is not None
                    else f"{runway.id} cannot be assigned"
                ),
                "source_fact_ids": [
                    f"runway:{runway.id}:status",
                    f"runway:{runway.id}:reopen",
                ],
                "status": "compiled",
            })
    for gate in scenario.gates:
        compiled_constraints.append({
            "id": f"gate-window:{gate.id}",
            "kind": "hard_operational",
            "rule": f"{gate.id} earliest in-block T+{gate.available_minute}",
            "source_fact_ids": [
                f"gate:{gate.id}:status",
                f"gate:{gate.id}:availability",
            ],
            "status": "compiled",
        })
    for flight in scenario.flights:
        compiled_constraints.append({
            "id": f"fuel-deadline:{flight.id}",
            "kind": "hard_safety",
            "rule": (
                f"{flight.id} landing deadline T+"
                f"{scenario.current_minute + flight.fuel_margin_min}"
            ),
            "source_fact_ids": [f"flight:{flight.id}:fuel:occ"],
            "status": "compiled",
        })
        if flight.medical_priority:
            compiled_constraints.append({
                "id": f"medical-priority:{flight.id}",
                "kind": "hard_safety",
                "rule": f"{flight.id} receives non-overridable priority",
                "source_fact_ids": [f"flight:{flight.id}:medical"],
                "status": "compiled",
            })

    previous_values = (previous or {}).get("solver_input_values", {})
    changed_facts = [
        {
            "key": key,
            "before": previous_values.get(key),
            "after": value,
            "cause": event.type.value if event else "new_snapshot",
        }
        for key, value in solver_values.items()
        if key in previous_values and previous_values[key] != value
    ]
    impacted_keys = _event_keys(event)
    fuel_clock = event.payload.get("fuel_clock", {}) if event else {}
    impacted_keys.update(
        f"flight.{item['flight_id']}.fuel_margin_min"
        for item in fuel_clock.get("updates", [])
    )
    invalidated_constraints = []
    for item in compiled_constraints:
        source_keys = {
            claim["key"]
            for claim in selected_claims
            if claim["id"] in item["source_fact_ids"]
        }
        if source_keys & impacted_keys:
            invalidated_constraints.append({
                "id": item["id"],
                "reason": event.type.value if event else "input_changed",
                "changed_fact_keys": sorted(source_keys & impacted_keys),
                "status": "invalidated_then_recompiled",
            })

    stale = [item for item in claims if item["status"] == "stale_excluded"]
    conflicts = [item for item in claims if item["status"] == "conflicted_excluded"]
    confidence_values = [float(item["confidence"]) for item in selected_claims]
    digest = sha256(
        json.dumps(solver_values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    critical_keys = {
        str(item["key"]) for item in claims if bool(item.get("critical", True))
    }
    selected_keys = {str(item["key"]) for item in selected_claims}
    state_status = "blocked" if argumentation["status"] == "blocked" else "ready"
    quality = {
        "raw_claims": len(claims),
        "solver_facts": len(selected_claims),
        "confirmed": len(selected_claims),
        "detected_conflicts": len(conflicts),
        "resolved_conflicts": len(conflicts),
        "stale_excluded": len(stale),
        "unresolved_critical": len(argumentation["unresolved_keys"]),
        "minimum_confidence": round(min(confidence_values), 2) if confidence_values else 0,
        "solver_coverage": round(
            len(selected_keys & critical_keys) / len(critical_keys),
            4,
        ) if critical_keys else 1.0,
    }
    if state_status == "blocked":
        summary = (
            f"Formal argumentation left {quality['unresolved_critical']} critical "
            "facts undecided; solver compilation abstained pending confirmation."
        )
    elif event and (changed_facts or invalidated_constraints):
        summary = (
            f"{event.type.value} changed {len(changed_facts)} trusted facts; "
            f"{len(invalidated_constraints)} dependent constraints were invalidated "
            "and recompiled before CP-SAT."
        )
    else:
        summary = (
            f"{quality['detected_conflicts']} conflict and "
            f"{quality['stale_excluded']} stale claim excluded by grounded "
            f"argumentation; {quality['solver_facts']} confirmed facts compiled "
            "for CP-SAT."
        )
    return {
        "method": (
            "typed evidence reconciliation + grounded argumentation + "
            "deterministic constraint compilation"
        ),
        "status": state_status,
        "snapshot_minute": now,
        "data_mode": mode,
        "summary": summary,
        "quality": quality,
        "claims": claims,
        "argumentation": argumentation,
        "resolution_log": resolution_log,
        "solver_input_values": solver_values,
        "compiled_constraints": compiled_constraints,
        "changed_facts": changed_facts,
        "invalidated_constraints": invalidated_constraints,
        "blocking_issues": [
            {
                "type": "unresolved_argument",
                "fact_key": key,
                "action": "query_source_or_request_human_confirmation",
            }
            for key in argumentation["unresolved_keys"]
        ],
        "digest": digest,
        "pipeline": [
            {"stage": "role_agents", "status": "completed", "output": f"{len(claims)} raw claims"},
            {"stage": "argumentation_graph", "status": argumentation["status"], "output": f"{argumentation['attack_count']} attacks"},
            {"stage": "evidence_coordinator", "status": "completed" if state_status == "ready" else "blocked", "output": f"{len(conflicts)} conflicts resolved"},
            {"stage": "trusted_operational_state", "status": state_status, "output": f"{len(selected_claims)} solver facts"},
            {"stage": "constraint_compiler", "status": "completed" if state_status == "ready" else "blocked", "output": f"{len(compiled_constraints)} constraints"},
            {"stage": "cp_sat", "status": "next" if state_status == "ready" else "blocked", "output": f"input {digest[:10]}"},
        ],
        "caveat": (
            "Synthetic exercise evidence unless the session explicitly uses live "
            "or imported data. Formal acceptance does not prove source truth."
        ),
    }


def apply_trusted_operational_state(
    scenario: ScenarioSpec,
    trusted_state: dict[str, Any],
) -> ScenarioSpec:
    """Materialize a solver copy exclusively from selected evidence values."""
    result = scenario.model_copy(deep=True)
    values = trusted_state.get("solver_input_values", {})
    if "weather.severity" in values:
        result.weather.severity = int(values["weather.severity"])
    for runway in result.runways:
        status_key = f"runway.{runway.id}.status"
        reopen_key = f"runway.{runway.id}.reopen_minute"
        if status_key in values:
            runway.status = ResourceStatus(str(values[status_key]))
        if reopen_key in values:
            runway.reopen_minute = values[reopen_key]
    for gate in result.gates:
        status_key = f"gate.{gate.id}.status"
        available_key = f"gate.{gate.id}.available_minute"
        if status_key in values:
            gate.status = ResourceStatus(str(values[status_key]))
        if available_key in values:
            gate.available_minute = int(values[available_key])
    for flight in result.flights:
        fuel_key = f"flight.{flight.id}.fuel_margin_min"
        priority_key = f"flight.{flight.id}.medical_priority"
        if fuel_key in values:
            flight.fuel_margin_min = int(values[fuel_key])
        if priority_key in values:
            flight.medical_priority = bool(values[priority_key])
    return result


def plan_evidence_basis(trusted_state: dict[str, Any]) -> dict[str, Any]:
    quality = trusted_state["quality"]
    return {
        "status": trusted_state["status"],
        "trusted_state_digest": trusted_state["digest"],
        "snapshot_minute": trusted_state["snapshot_minute"],
        "confirmed_facts": quality["solver_facts"],
        "resolved_conflicts": quality["resolved_conflicts"],
        "stale_claims_excluded": quality["stale_excluded"],
        "minimum_confidence": quality["minimum_confidence"],
        "compiled_constraints": len(trusted_state["compiled_constraints"]),
        "unresolved_critical": quality["unresolved_critical"],
    }

