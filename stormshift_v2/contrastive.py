"""Per-flight contrastive explanations backed by constrained re-optimization.

The output contains observable solver/simulation evidence, never hidden model reasoning.
"""

from __future__ import annotations

from typing import Any

from .optimizer import ACTIVE_STATES, solve_candidate, validate_assignments
from .schemas import (
    FlightAssignment,
    FlightState,
    OperatorIntent,
    PlanCandidate,
    ResourceStatus,
    ScenarioSpec,
)
from .simulation import simulate_candidate


def _reconstruct_plan_scope(
    scenario: ScenarioSpec,
    plan: PlanCandidate,
) -> ScenarioSpec:
    scoped = scenario.model_copy(deep=True)
    planned_ids = {item.flight_id for item in plan.assignments}
    for flight in scoped.flights:
        if flight.id in planned_ids:
            if flight.state not in ACTIVE_STATES:
                flight.state = FlightState.holding
        elif flight.state in ACTIVE_STATES:
            flight.state = FlightState.landed
    return scoped


def _constraint_witnesses(
    scenario: ScenarioSpec,
    plan: PlanCandidate,
    assignment: FlightAssignment,
) -> list[dict[str, Any]]:
    flight = next(item for item in scenario.flights if item.id == assignment.flight_id)
    runway = next(item for item in scenario.runways if item.id == assignment.runway)
    gate = next(item for item in scenario.gates if item.id == assignment.gate)
    runway_open = (
        scenario.current_minute
        if runway.status == ResourceStatus.open
        else runway.reopen_minute
    )
    runway_sequence = sorted(
        (
            item for item in plan.assignments
            if item.runway == assignment.runway and item.flight_id != assignment.flight_id
        ),
        key=lambda item: item.landing_minute,
    )
    previous = next(
        (item for item in reversed(runway_sequence) if item.landing_minute < assignment.landing_minute),
        None,
    )
    following = next(
        (item for item in runway_sequence if item.landing_minute > assignment.landing_minute),
        None,
    )
    gate_sequence = sorted(
        (
            item for item in plan.assignments
            if item.gate == assignment.gate and item.flight_id != assignment.flight_id
        ),
        key=lambda item: item.landing_minute + item.taxi_minutes,
    )
    predecessor = next(
        (
            item for item in reversed(gate_sequence)
            if item.landing_minute + item.taxi_minutes < assignment.landing_minute + assignment.taxi_minutes
        ),
        None,
    )
    fuel_deadline = scenario.current_minute + flight.fuel_margin_min
    if runway.status == ResourceStatus.open:
        runway_detail = (
            f"{runway.id} 持续开放；本航班落地 T+{assignment.landing_minute}"
        )
    elif runway_open is not None:
        runway_detail = f"{runway.id} 从 T+{runway_open} 起恢复可用；本航班落地 T+{assignment.landing_minute}"
    else:
        runway_detail = f"{runway.id} 在规划窗口内持续关闭"
    separation_parts = []
    if previous:
        separation_parts.append(
            f"前序 {previous.flight_id} T+{previous.landing_minute}"
        )
    if following:
        separation_parts.append(
            f"后序 {following.flight_id} T+{following.landing_minute}"
        )
    separation_detail = (
        "；".join(separation_parts) + f"；最小间隔 {runway.separation_min} 分钟"
        if separation_parts
        else f"该跑道当前无相邻落地；最小间隔 {runway.separation_min} 分钟"
    )
    gate_detail = (
        f"前序 {predecessor.flight_id} 完成周转后释放 {gate.id}"
        if predecessor
        else f"{gate.id} 从 T+{gate.available_minute} 起可用"
    )
    return [
        {
            "id": "runway_availability",
            "label": "跑道开放窗口",
            "status": "pass",
            "hard": True,
            "detail": runway_detail,
        },
        {
            "id": "runway_separation",
            "label": "前后机安全间隔",
            "status": "pass",
            "hard": True,
            "detail": separation_detail,
        },
        {
            "id": "gate_turnaround",
            "label": "机位唯一与周转",
            "status": "pass",
            "hard": True,
            "detail": gate_detail,
        },
        {
            "id": "fuel_deadline",
            "label": "燃油运行截止",
            "status": "pass",
            "hard": True,
            "detail": (
                f"截止 T+{fuel_deadline}；计划 T+{assignment.landing_minute}，"
                f"剩余 {fuel_deadline - assignment.landing_minute} 分钟"
            ),
        },
        {
            "id": "passenger_priority",
            "label": "转机影响",
            "status": "context",
            "hard": False,
            "detail": f"{flight.connections} 名转机旅客进入目标函数",
        },
    ]


def _candidate_variants(
    scenario: ScenarioSpec,
    assignment: FlightAssignment,
) -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = []
    alternate_runway = next(
        (item for item in scenario.runways if item.id != assignment.runway),
        None,
    )
    if alternate_runway:
        variants.append({
            "id": "alternate_runway",
            "label": f"改走 {alternate_runway.id}",
            "field": "runway",
            "from": assignment.runway,
            "to": alternate_runway.id,
            "forced": assignment.model_copy(update={"runway": alternate_runway.id}),
        })

    flight = next(item for item in scenario.flights if item.id == assignment.flight_id)
    gate_candidates = [
        item for item in scenario.gates
        if item.id != assignment.gate and item.status != ResourceStatus.reserved
    ]
    preferred_gate = next(
        (item for item in gate_candidates if item.id == flight.planned_gate),
        None,
    )
    alternate_gate = preferred_gate or min(
        gate_candidates,
        key=lambda item: (item.taxi_minutes, item.available_minute, item.id),
        default=None,
    )
    if alternate_gate:
        variants.append({
            "id": "alternate_gate",
            "label": f"改停 {alternate_gate.id}",
            "field": "gate",
            "from": assignment.gate,
            "to": alternate_gate.id,
            "forced": assignment.model_copy(update={
                "gate": alternate_gate.id,
                "taxi_minutes": alternate_gate.taxi_minutes,
                "remote_stand": alternate_gate.remote,
            }),
        })

    earliest = max(scenario.current_minute, flight.scheduled_arrival)
    fuel_deadline = scenario.current_minute + flight.fuel_margin_min
    if assignment.landing_minute > earliest:
        target_minute = max(earliest, assignment.landing_minute - 3)
        variant_id = "earlier_slot"
        label = f"提前到 T+{target_minute}"
    elif assignment.landing_minute + 3 <= fuel_deadline:
        target_minute = assignment.landing_minute + 3
        variant_id = "later_slot"
        label = f"延后到 T+{target_minute}"
    else:
        target_minute = None
        variant_id = ""
        label = ""
    if target_minute is not None and target_minute != assignment.landing_minute:
        variants.append({
            "id": variant_id,
            "label": label,
            "field": "landing_minute",
            "from": assignment.landing_minute,
            "to": target_minute,
            "forced": assignment.model_copy(update={
                "landing_minute": target_minute,
                "hold_minutes": target_minute - flight.scheduled_arrival,
            }),
        })
    return variants[:3]


def _precheck(
    scenario: ScenarioSpec,
    variant: dict[str, Any],
) -> list[str]:
    forced: FlightAssignment = variant["forced"]
    evidence: list[str] = []
    if variant["field"] == "runway":
        runway = next(item for item in scenario.runways if item.id == forced.runway)
        open_minute = (
            scenario.current_minute
            if runway.status == ResourceStatus.open
            else runway.reopen_minute
        )
        if open_minute is None:
            evidence.append(f"{runway.id} 在规划窗口内持续关闭")
        elif forced.landing_minute < open_minute:
            evidence.append(
                f"{runway.id} 到 T+{open_minute} 才开放，不能在 T+{forced.landing_minute} 使用"
            )
    if variant["field"] == "gate":
        gate = next(item for item in scenario.gates if item.id == forced.gate)
        gate_in = forced.landing_minute + gate.taxi_minutes
        if gate.status == ResourceStatus.reserved:
            evidence.append(f"{gate.id} 是保留资源，不进入普通分配池")
        if gate_in < gate.available_minute:
            evidence.append(
                f"{gate.id} 到 T+{gate.available_minute} 才可用，预计进位为 T+{gate_in}"
            )
    return evidence


def build_flight_contrastive_explanation(
    scenario: ScenarioSpec,
    plan: PlanCandidate,
    flight_id: str,
    intent: OperatorIntent | None = None,
    simulation_runs: int = 12,
) -> dict[str, Any]:
    assignment = next(
        (item for item in plan.assignments if item.flight_id == flight_id),
        None,
    )
    if assignment is None:
        raise KeyError(flight_id)
    scoped = _reconstruct_plan_scope(scenario, plan)
    baseline_report = simulate_candidate(scoped, plan, runs=simulation_runs)
    alternatives: list[dict[str, Any]] = []

    for variant in _candidate_variants(scoped, assignment):
        forced: FlightAssignment = variant.pop("forced")
        precheck = _precheck(scoped, {**variant, "forced": forced})
        candidate, failure = solve_candidate(
            scoped,
            plan.profile,
            intent,
            timeout_seconds=.28,
            frozen_assignments=[forced],
        )
        if candidate is None:
            failure_evidence = (
                list(failure.reasons)
                if failure and failure.reasons
                else ["强制替代后 CP-SAT 证明无可行解"]
            )
            alternatives.append({
                **variant,
                "status": "infeasible",
                "verdict": "硬约束阻止该替代",
                "changed_flights": None,
                "delta": None,
                "evidence": list(dict.fromkeys([*precheck, *failure_evidence]))[:3],
            })
            continue

        proof = validate_assignments(scoped, candidate.assignments, intent)
        if any(item.startswith("FAIL") for item in proof):
            alternatives.append({
                **variant,
                "status": "infeasible",
                "verdict": "独立安全检查拒绝该替代",
                "changed_flights": None,
                "delta": None,
                "evidence": proof[:3],
            })
            continue

        candidate.report = simulate_candidate(scoped, candidate, runs=simulation_runs)
        baseline_by_id = {item.flight_id: item for item in plan.assignments}
        candidate_by_id = {item.flight_id: item for item in candidate.assignments}
        changed_flights = sum(
            baseline_by_id.get(item_id) != candidate_by_id.get(item_id)
            for item_id in set(baseline_by_id) | set(candidate_by_id)
        )
        objective_delta = round(candidate.score - plan.score, 2)
        alternatives.append({
            **variant,
            "status": "feasible",
            "verdict": f"可执行，但需要联动重排 {changed_flights} 架航班",
            "changed_flights": changed_flights,
            "delta": {
                "objective": objective_delta,
                "mean_delay_min": round(
                    candidate.report.mean_delay_min - baseline_report.mean_delay_min, 2
                ),
                "p90_delay_min": round(
                    candidate.report.p90_delay_min - baseline_report.p90_delay_min, 2
                ),
                "missed_connections": (
                    candidate.report.missed_connections
                    - baseline_report.missed_connections
                ),
                "emissions_index": round(
                    candidate.report.emissions_index - baseline_report.emissions_index, 2
                ),
            },
            "evidence": [
                f"CP-SAT 重新求解后仍满足全部硬约束",
                f"同一组 {simulation_runs} 个随机世界进行成对仿真",
            ],
        })

    blocked = sum(item["status"] == "infeasible" for item in alternatives)
    feasible = len(alternatives) - blocked
    return {
        "method": "forced-action CP-SAT re-optimization + paired-seed SimPy",
        "flight_id": flight_id,
        "plan_id": plan.id,
        "scope_flights": len(plan.assignments),
        "simulation_runs": simulation_runs,
        "baseline": {
            "runway": assignment.runway,
            "gate": assignment.gate,
            "landing_minute": assignment.landing_minute,
            "mean_delay_min": baseline_report.mean_delay_min,
            "p90_delay_min": baseline_report.p90_delay_min,
            "missed_connections": baseline_report.missed_connections,
            "emissions_index": baseline_report.emissions_index,
        },
        "witnesses": _constraint_witnesses(scoped, plan, assignment),
        "alternatives": alternatives,
        "summary": (
            f"验证 {len(alternatives)} 个强制替代：{blocked} 个被硬约束阻止，"
            f"{feasible} 个可行但需要重排或承担额外目标代价。"
        ),
        "safety_statement": (
            "这是对可执行动作的反事实重求解，不是 LLM 生成的理由；"
            "任何替代都必须再次通过跑道、间隔、机位和燃油硬约束。"
        ),
    }
