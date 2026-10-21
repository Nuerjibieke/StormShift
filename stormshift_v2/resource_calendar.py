"""Committed runway and gate intervals shared by planning and verification.

Reservations represent facts that have already been committed by the execution
engine. They are not editable plan suggestions. Candidate assignments are
converted with authoritative scenario values before the two calendars are
checked together.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping

from .schemas import FlightAssignment, PlanCandidate, ResourceReservation, ScenarioSpec


DEFAULT_TURNAROUND_MINUTES = 22


def intervals_overlap(
    left_start: int,
    left_end: int,
    right_start: int,
    right_end: int,
) -> bool:
    """Return whether two left-closed, right-open intervals overlap."""
    return left_start < right_end and right_start < left_end


def assignment_reservations(
    scenario: ScenarioSpec,
    assignments: Iterable[FlightAssignment],
    *,
    source_plan_id: str | None = None,
    revision: int = 0,
    committed_minute: int | None = None,
    turnaround_buffer_by_flight: Mapping[str, int] | None = None,
) -> list[ResourceReservation]:
    """Build intervals from authoritative runway and gate values."""
    runway_by_id = {item.id: item for item in scenario.runways}
    gate_by_id = {item.id: item for item in scenario.gates}
    buffers = turnaround_buffer_by_flight or {}
    result: list[ResourceReservation] = []
    commit_at = (
        scenario.current_minute
        if committed_minute is None
        else committed_minute
    )
    prefix = source_plan_id or "candidate"

    for item in assignments:
        runway = runway_by_id.get(item.runway)
        gate = gate_by_id.get(item.gate)
        if runway is None or gate is None:
            continue
        gate_start = item.landing_minute + gate.taxi_minutes
        gate_duration = DEFAULT_TURNAROUND_MINUTES + max(
            0, int(buffers.get(item.flight_id, 0))
        )
        result.extend([
            ResourceReservation(
                id=f"{prefix}:r{revision}:{item.flight_id}:runway",
                kind="runway",
                resource_id=runway.id,
                flight_id=item.flight_id,
                start_minute=item.landing_minute,
                end_minute=item.landing_minute + runway.separation_min,
                source_plan_id=source_plan_id,
                revision=revision,
                committed_minute=commit_at,
            ),
            ResourceReservation(
                id=f"{prefix}:r{revision}:{item.flight_id}:gate",
                kind="gate",
                resource_id=gate.id,
                flight_id=item.flight_id,
                start_minute=gate_start,
                end_minute=gate_start + gate_duration,
                source_plan_id=source_plan_id,
                revision=revision,
                committed_minute=commit_at,
            ),
        ])
    return result


def effective_turnaround_buffers(
    plan: PlanCandidate,
) -> dict[str, int]:
    """Return only the robust buffers actually solved into this plan."""
    if not isinstance(plan.risk_shield, dict):
        return {}
    envelope = plan.risk_shield.get("adaptive_envelope", {})
    if not isinstance(envelope, dict) or not envelope.get(
        "applies_to_candidate", False
    ):
        return {}
    raw = envelope.get("turnaround_buffer_by_flight", {})
    if not isinstance(raw, dict):
        return {}
    return {
        str(flight_id): max(0, int(value))
        for flight_id, value in raw.items()
    }


def validate_resource_calendar(
    scenario: ScenarioSpec,
    reservations: Iterable[ResourceReservation],
) -> list[str]:
    """Validate identity, uniqueness and overlap of committed intervals.

    Current resource status is intentionally not consulted: a later outage
    must not retroactively invalidate an operation committed before the outage.
    """
    rows = list(reservations)
    runway_ids = {item.id for item in scenario.runways}
    gate_ids = {item.id for item in scenario.gates}
    flight_ids = {item.id for item in scenario.flights}
    failures: list[str] = []

    def add(message: str) -> None:
        if message not in failures:
            failures.append(message)

    seen_ids: set[str] = set()
    by_flight_kind: dict[tuple[str, str], list[ResourceReservation]] = (
        defaultdict(list)
    )
    by_resource: dict[tuple[str, str], list[ResourceReservation]] = (
        defaultdict(list)
    )

    for row in rows:
        if row.id in seen_ids:
            add(f"FAIL 重复资源预留 ID：{row.id}")
        seen_ids.add(row.id)
        known_resources = runway_ids if row.kind == "runway" else gate_ids
        if row.resource_id not in known_resources:
            add(
                f"FAIL {row.flight_id} 预留了未知{row.kind}资源 "
                f"{row.resource_id}"
            )
        if row.flight_id not in flight_ids:
            add(f"FAIL 资源预留引用未知航班 {row.flight_id}")
        if row.committed_minute > row.start_minute:
            add(
                f"FAIL {row.flight_id} 的 {row.resource_id} 预留在操作开始后才提交"
            )
        by_flight_kind[(row.flight_id, row.kind)].append(row)
        by_resource[(row.kind, row.resource_id)].append(row)

    for (flight_id, kind), group in by_flight_kind.items():
        if len(group) > 1:
            add(f"FAIL {flight_id} 存在多个已提交的{kind}资源预留")

    for (kind, resource_id), group in by_resource.items():
        ordered = sorted(
            group,
            key=lambda item: (
                item.start_minute,
                item.end_minute,
                item.flight_id,
                item.id,
            ),
        )
        active: ResourceReservation | None = None
        for row in ordered:
            if active is not None and intervals_overlap(
                active.start_minute,
                active.end_minute,
                row.start_minute,
                row.end_minute,
            ):
                add(
                    f"FAIL {kind} {resource_id} 被 "
                    f"{active.flight_id}[{active.start_minute},{active.end_minute})/"
                    f"{row.flight_id}[{row.start_minute},{row.end_minute}) 重复占用"
                )
            if active is None or row.end_minute > active.end_minute:
                active = row

    return failures


def validate_assignment_calendar(
    scenario: ScenarioSpec,
    assignments: Iterable[FlightAssignment],
    *,
    committed_reservations: Iterable[ResourceReservation] | None = None,
    turnaround_buffer_by_flight: Mapping[str, int] | None = None,
) -> list[str]:
    """Check a candidate future against the immutable committed prefix."""
    assignment_list = list(assignments)
    committed = list(
        scenario.committed_reservations
        if committed_reservations is None
        else committed_reservations
    )
    committed_flights = {item.flight_id for item in committed}
    failures = validate_resource_calendar(scenario, committed)
    for flight_id in sorted(
        {item.flight_id for item in assignment_list} & committed_flights
    ):
        failures.append(
            f"FAIL {flight_id} 已有提交记录，不能再次进入候选计划"
        )
    future = assignment_reservations(
        scenario,
        assignment_list,
        turnaround_buffer_by_flight=turnaround_buffer_by_flight,
    )
    failures.extend(validate_resource_calendar(scenario, [*committed, *future]))
    return list(dict.fromkeys(failures))
