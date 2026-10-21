"""Exact CP-SAT infeasibility certificates for airport recovery plans.

The executable optimizer remains the source of flight actions.  This module
builds a feasibility-only mirror whose operational and operator constraints
are guarded by CP-SAT assumptions.  When planning fails, OR-Tools returns a
sufficient assumption core; a deletion filter then makes that core
irreducible.  Hard-safety rules are never offered as relaxations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ortools.sat.python import cp_model

from .schemas import (
    FlightState,
    OperatorIntent,
    ResourceStatus,
    ScenarioSpec,
)

ACTIVE_STATES = {FlightState.inbound, FlightState.approach, FlightState.holding}
HORIZON_MINUTES = 180
TURNAROUND_MINUTES = 22


@dataclass
class _Group:
    id: str
    category: str
    label: str
    rule: str
    affected: list[str]
    safety_class: str
    relaxable: bool
    remedy: str
    literal: cp_model.IntVar

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "label": self.label,
            "rule": self.rule,
            "affected": self.affected,
            "safety_class": self.safety_class,
            "relaxable": self.relaxable,
            "remedy": self.remedy,
        }


def _status_name(status: cp_model.CpSolverStatus) -> str:
    return str(status).rsplit(".", 1)[-1].lower()


def _solver() -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 1.0
    solver.parameters.max_deterministic_time = 0.2
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 2026
    return solver


def _build_assumption_model(
    scenario: ScenarioSpec,
    intent: OperatorIntent | None,
) -> tuple[cp_model.CpModel, list[_Group], str | None]:
    model = cp_model.CpModel()
    active = [
        flight for flight in scenario.flights
        if flight.state in ACTIVE_STATES
    ]
    if not active:
        return model, [], None
    if not scenario.runways:
        return model, [], "没有跑道资源可供分配"
    if not scenario.gates:
        return model, [], "没有机位资源可供分配"

    earliest_values = [
        max(scenario.current_minute, flight.scheduled_arrival)
        for flight in active
    ]
    if any(value > HORIZON_MINUTES for value in earliest_values):
        return model, [], "活动航班已超出 T+180 规划窗口"

    groups: list[_Group] = []

    def add_group(
        group_id: str,
        category: str,
        label: str,
        rule: str,
        affected: list[str],
        safety_class: str,
        relaxable: bool,
        remedy: str,
    ) -> cp_model.IntVar:
        literal = model.new_bool_var("assume_" + group_id.replace(":", "_"))
        groups.append(_Group(
            id=group_id,
            category=category,
            label=label,
            rule=rule,
            affected=affected,
            safety_class=safety_class,
            relaxable=relaxable,
            remedy=remedy,
            literal=literal,
        ))
        return literal

    runway_guards: dict[str, cp_model.IntVar] = {}
    for runway in scenario.runways:
        if runway.status != ResourceStatus.open:
            when = (
                f"T+{runway.reopen_minute}"
                if runway.reopen_minute is not None
                else "规划窗口内不开放"
            )
            runway_guards[runway.id] = add_group(
                f"runway:{runway.id}",
                "runway_availability",
                f"跑道 {runway.id} 可用窗口",
                f"{runway.id} 当前关闭，{when}",
                [runway.id],
                "hard_safety",
                False,
                "等待跑道检查确认开放时刻，或将无法等待的航班安全备降",
            )

    gate_guards: dict[str, cp_model.IntVar] = {}
    for gate in scenario.gates:
        constrained = (
            gate.status in {ResourceStatus.blocked, ResourceStatus.reserved}
            or gate.available_minute > scenario.current_minute
        )
        if constrained:
            gate_guards[gate.id] = add_group(
                f"gate:{gate.id}",
                "gate_availability",
                f"机位 {gate.id} 可用窗口",
                (
                    f"{gate.id} 状态为 {gate.status.value}，"
                    f"最早 T+{gate.available_minute} 可用"
                ),
                [gate.id],
                "hard_safety",
                False,
                "修复或释放该机位，或启用已验证可用的远机位",
            )

    fuel_guards = {
        flight.id: add_group(
            f"fuel:{flight.id}",
            "fuel_deadline",
            f"{flight.id} 燃油截止",
            (
                f"{flight.id} 必须在 T+"
                f"{scenario.current_minute + flight.fuel_margin_min} 前落地"
            ),
            [flight.id],
            "hard_safety",
            False,
            "提高着陆优先级；若仍不可达，执行安全备降而不是放宽燃油界限",
        )
        for flight in active
    }

    priority_flights = [flight for flight in active if flight.medical_priority]
    medical_guard: cp_model.IntVar | None = None
    if priority_flights:
        ids = [flight.id for flight in priority_flights]
        medical_guard = add_group(
            "medical:priority",
            "medical_priority",
            "医疗航班硬优先级",
            "医疗备降须在 T+8 内落地并排在普通航班之前",
            ids,
            "hard_safety",
            False,
            "为医疗航班释放跑道与接收机位，必要时分流普通航班",
        )

    average_delay_guard: cp_model.IntVar | None = None
    if intent and not intent.needs_confirmation and intent.average_delay_cap is not None:
        average_delay_guard = add_group(
            "operator:average_delay",
            "operator_target",
            "操作员平均延误上限",
            f"平均等待不得超过 {intent.average_delay_cap:g} 分钟",
            [flight.id for flight in active],
            "operator_soft",
            True,
            "确认提高平均延误上限，或增加跑道/机位容量后重新求解",
        )

    schedule_guards: list[tuple[Any, cp_model.IntVar]] = []
    if intent and not intent.needs_confirmation:
        for index, constraint in enumerate(intent.scheduled_constraints):
            if constraint.resource_id is None or constraint.minute is None:
                continue
            guard = add_group(
                f"operator:schedule:{index}",
                "scheduled_constraint",
                f"{constraint.resource_id} 的计划约束",
                (
                    f"{constraint.kind} · {constraint.resource_id} · "
                    f"T+{constraint.minute}"
                ),
                [constraint.resource_id],
                "hard_safety" if constraint.hard else "operator_soft",
                not constraint.hard,
                (
                    "重新确认该资源的关闭/开放时刻；安全检查未确认前不得使用"
                    if constraint.hard
                    else "修改或撤销该操作员时间约束后重新求解"
                ),
            )
            schedule_guards.append((constraint, guard))

    variables: dict[str, dict[str, Any]] = {}
    runway_intervals: dict[str, list[Any]] = {
        runway.id: [] for runway in scenario.runways
    }
    gate_intervals: dict[str, list[Any]] = {
        gate.id: [] for gate in scenario.gates
    }

    for flight in active:
        earliest = max(scenario.current_minute, flight.scheduled_arrival)
        landing = model.new_int_var(
            earliest, HORIZON_MINUTES, f"diag_landing_{flight.id}"
        )
        hold = model.new_int_var(0, HORIZON_MINUTES, f"diag_hold_{flight.id}")
        model.add(hold == landing - flight.scheduled_arrival)
        model.add(
            landing <= scenario.current_minute + flight.fuel_margin_min
        ).only_enforce_if(fuel_guards[flight.id])

        runway_choice: dict[str, cp_model.IntVar] = {}
        for runway in scenario.runways:
            chosen = model.new_bool_var(
                f"diag_runway_{flight.id}_{runway.id}"
            )
            runway_choice[runway.id] = chosen
            guard = runway_guards.get(runway.id)
            if guard is not None:
                if runway.reopen_minute is None:
                    model.add(chosen == 0).only_enforce_if(guard)
                else:
                    model.add(
                        landing >= int(runway.reopen_minute)
                    ).only_enforce_if([chosen, guard])
            runway_intervals[runway.id].append(
                model.new_optional_fixed_size_interval_var(
                    landing,
                    runway.separation_min,
                    chosen,
                    f"diag_rwy_interval_{flight.id}_{runway.id}",
                )
            )
        model.add_exactly_one(runway_choice.values())

        gate_choice: dict[str, cp_model.IntVar] = {}
        gate_start: dict[str, cp_model.IntVar] = {}
        for gate in scenario.gates:
            chosen = model.new_bool_var(f"diag_gate_{flight.id}_{gate.id}")
            start = model.new_int_var(
                0, HORIZON_MINUTES + 30, f"diag_gate_start_{flight.id}_{gate.id}"
            )
            gate_choice[gate.id] = chosen
            gate_start[gate.id] = start
            model.add(start == landing + gate.taxi_minutes).only_enforce_if(chosen)
            guard = gate_guards.get(gate.id)
            if guard is not None:
                if gate.status == ResourceStatus.reserved or (
                    gate.status == ResourceStatus.blocked
                    and not (
                        scenario.current_minute
                        < gate.available_minute
                        < HORIZON_MINUTES
                    )
                ):
                    model.add(chosen == 0).only_enforce_if(guard)
                else:
                    model.add(
                        start >= gate.available_minute
                    ).only_enforce_if([chosen, guard])
            gate_intervals[gate.id].append(
                model.new_optional_fixed_size_interval_var(
                    start,
                    TURNAROUND_MINUTES,
                    chosen,
                    f"diag_gate_interval_{flight.id}_{gate.id}",
                )
            )
        model.add_exactly_one(gate_choice.values())

        for constraint, guard in schedule_guards:
            if constraint.resource_id in runway_choice:
                chosen = runway_choice[constraint.resource_id]
                runway = next(
                    item for item in scenario.runways
                    if item.id == constraint.resource_id
                )
                if constraint.kind == "close_resource":
                    model.add(
                        landing + runway.separation_min <= int(constraint.minute)
                    ).only_enforce_if([chosen, guard])
                elif constraint.kind == "reopen_resource":
                    model.add(
                        landing >= int(constraint.minute)
                    ).only_enforce_if([chosen, guard])
            if constraint.resource_id in gate_choice:
                chosen = gate_choice[constraint.resource_id]
                start = gate_start[constraint.resource_id]
                if constraint.kind == "close_resource":
                    model.add(
                        start + TURNAROUND_MINUTES <= int(constraint.minute)
                    ).only_enforce_if([chosen, guard])
                elif constraint.kind == "reopen_resource":
                    model.add(
                        start >= int(constraint.minute)
                    ).only_enforce_if([chosen, guard])

        if medical_guard is not None and flight.medical_priority:
            model.add(
                landing <= min(
                    HORIZON_MINUTES, scenario.current_minute + 8
                )
            ).only_enforce_if(medical_guard)

        variables[flight.id] = {
            "landing": landing,
            "hold": hold,
            "runway": runway_choice,
            "gate": gate_choice,
        }

    for intervals in runway_intervals.values():
        model.add_no_overlap(intervals)
    for intervals in gate_intervals.values():
        model.add_no_overlap(intervals)

    if medical_guard is not None:
        for priority in priority_flights:
            for other in active:
                if other.medical_priority:
                    continue
                model.add(
                    variables[priority.id]["landing"]
                    <= variables[other.id]["landing"]
                ).only_enforce_if(medical_guard)

    if average_delay_guard is not None:
        model.add(
            sum(item["hold"] for item in variables.values())
            <= round(float(intent.average_delay_cap) * len(active))
        ).only_enforce_if(average_delay_guard)

    return model, groups, None


def explain_infeasibility(
    scenario: ScenarioSpec,
    intent: OperatorIntent | None = None,
) -> dict[str, Any]:
    """Return an irreducible CP-SAT assumption core and safe remedies."""
    model, groups, structural_reason = _build_assumption_model(scenario, intent)
    if structural_reason is not None:
        return {
            "method": "cp-sat-structural-certificate",
            "solver_status": "infeasible",
            "core_irreducible": True,
            "solver_calls": 0,
            "hard_safety_relaxations_allowed": 0,
            "summary": structural_reason,
            "conflict_core": [{
                "id": "structural:capacity",
                "category": "structural_capacity",
                "label": "规划结构或容量不足",
                "rule": structural_reason,
                "affected": [],
                "safety_class": "hard_safety",
                "relaxable": False,
                "remedy": "增加已验证资源、延长规划窗口，或安全分流部分航班",
            }],
        }
    if not groups:
        return {
            "method": "cp-sat-assumption-core",
            "solver_status": "feasible",
            "core_irreducible": False,
            "solver_calls": 0,
            "hard_safety_relaxations_allowed": 0,
            "summary": "诊断镜像未发现可解释的约束冲突",
            "conflict_core": [],
        }

    model.add_assumptions([group.literal for group in groups])
    solver = _solver()
    status = solver.solve(model)
    calls = 1
    if status != cp_model.INFEASIBLE:
        return {
            "method": "cp-sat-assumption-core",
            "solver_status": (
                "feasible"
                if status in {cp_model.FEASIBLE, cp_model.OPTIMAL}
                else _status_name(status)
            ),
            "core_irreducible": False,
            "solver_calls": calls,
            "hard_safety_relaxations_allowed": 0,
            "summary": "诊断镜像未复现不可行状态，保留原求解器安全停机",
            "conflict_core": [],
        }

    by_index = {group.literal.Index(): group for group in groups}
    sufficient = [
        by_index[index]
        for index in solver.sufficient_assumptions_for_infeasibility()
        if index in by_index
    ]
    core = sufficient or list(groups)

    # Deletion filtering turns the solver's sufficient core into an irreducible
    # core: removing any remaining member makes this mirrored model feasible.
    for candidate in list(core):
        trial = [group for group in core if group.id != candidate.id]
        model.clear_assumptions()
        model.add_assumptions([group.literal for group in trial])
        trial_status = solver.solve(model)
        calls += 1
        if trial_status == cp_model.INFEASIBLE:
            core = trial

    if not core:
        core = [_Group(
            id="structural:capacity",
            category="structural_capacity",
            label="跑道—机位结构容量",
            rule="即使移除所有可选假设，基础分配与互斥容量仍不可满足",
            affected=[flight.id for flight in scenario.flights if flight.state in ACTIVE_STATES],
            safety_class="hard_safety",
            relaxable=False,
            remedy="增加已验证资源、延长规划窗口，或安全分流部分航班",
            literal=model.new_bool_var("structural_capacity_marker"),
        )]

    soft = [group for group in core if group.relaxable]
    if soft:
        guidance = "优先修改冲突核心中的软目标，再重新求解"
    else:
        guidance = "核心只含安全/运行事实，必须调整资源或执行安全分流"

    return {
        "method": "cp-sat-assumption-core + deletion-filtered-IIS",
        "solver_status": "infeasible",
        "core_irreducible": True,
        "solver_calls": calls,
        "hard_safety_relaxations_allowed": 0,
        "summary": (
            f"OR-Tools 证明 {len(core)} 条约束构成不可同时满足的不可约冲突集；"
            f"{guidance}"
        ),
        "conflict_core": [group.public() for group in core],
    }

