"""Constraint-safe airport recovery planning with OR-Tools CP-SAT."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ortools.sat.python import cp_model

from .infeasibility import explain_infeasibility
from .passenger import connection_buffer_minutes, risk_adjusted_connection_buffer_minutes
from .resource_calendar import (
    DEFAULT_TURNAROUND_MINUTES,
    validate_assignment_calendar,
)
from .runway_assurance import runway_planning_ready_minute, runway_p90_ready_minute
from .schemas import (
    FlightAssignment,
    FlightState,
    InfeasibilityReport,
    ObjectiveWeights,
    OperatorIntent,
    PlanCandidate,
    PlanProfile,
    RelaxationOption,
    ResourceReservation,
    ResourceStatus,
    ScenarioSpec,
    SimulationReport,
)

ACTIVE_STATES = {FlightState.inbound, FlightState.approach, FlightState.holding}
TURNAROUND_MINUTES = DEFAULT_TURNAROUND_MINUTES
HORIZON_MINUTES = 180
PROFILE_SOLVER_SEED_OFFSET = {
    PlanProfile.balanced: 0,
    PlanProfile.passenger: 1,
    PlanProfile.capacity: 2,
    PlanProfile.green: 3,
    PlanProfile.operator: 4,
    PlanProfile.resilient: 5,
}



@dataclass
class _Vars:
    landing: cp_model.IntVar
    hold: cp_model.IntVar
    runway: dict[str, cp_model.BoolVar]
    gate: dict[str, cp_model.BoolVar]


def empty_report() -> SimulationReport:
    return SimulationReport(
        runs=0,
        mean_delay_min=0,
        p90_delay_min=0,
        cvar_delay_min=0,
        missed_connections=0,
        fuel_risk_probability=0,
        remaining_conflicts=0,
        emissions_index=0,
        recovery_minute=0,
        confidence_low=0,
        confidence_high=0,
    )


def profile_weights(profile: PlanProfile, intent: OperatorIntent | None) -> ObjectiveWeights:
    presets = {
        PlanProfile.passenger: ObjectiveWeights(delay=.22, connections=.50, fuel_risk=.16, conflicts=.08, emissions=.04),
        PlanProfile.capacity: ObjectiveWeights(delay=.56, connections=.17, fuel_risk=.15, conflicts=.08, emissions=.04),
        PlanProfile.resilient: ObjectiveWeights(delay=.12, connections=.10, fuel_risk=.70, conflicts=.05, emissions=.03),
        PlanProfile.green: ObjectiveWeights(delay=.05, connections=0, fuel_risk=.03, conflicts=.02, emissions=.90),
    }
    if profile == PlanProfile.operator and intent:
        return intent.weights.normalized()
    if profile == PlanProfile.balanced and intent:
        return intent.weights.normalized()
    return presets.get(profile, ObjectiveWeights()).normalized()


def _usable_runways(scenario: ScenarioSpec) -> list:
    return [
        runway for runway in scenario.runways
        if runway.status == ResourceStatus.open
        or (runway.status == ResourceStatus.closed and runway.reopen_minute is not None)
    ]


def _usable_gates(scenario: ScenarioSpec) -> list:
    return [
        gate
        for gate in scenario.gates
        if gate.status != ResourceStatus.reserved
        and (
            gate.status != ResourceStatus.blocked
            or scenario.current_minute < gate.available_minute < HORIZON_MINUTES
        )
    ]


def diagnose_infeasibility(
    scenario: ScenarioSpec, intent: OperatorIntent | None = None,
) -> InfeasibilityReport:
    active = [flight for flight in scenario.flights if flight.state in ACTIVE_STATES]
    runways, gates = _usable_runways(scenario), _usable_gates(scenario)
    reasons: list[str] = []
    relaxations: list[str] = []
    options: list[RelaxationOption] = []
    if active and not runways:
        reasons.append("所有跑道在规划窗口内均不可用")
        relaxations.append("延长规划窗口，或明确一条跑道的预计开放时刻")
    if active and not gates:
        reasons.append("规划窗口内没有可用机位或远机位")
        relaxations.append("开放一个远机位，或给出故障机位的恢复时刻")
    earliest_runway = min(
        (scenario.current_minute if r.status == ResourceStatus.open else int(r.reopen_minute or HORIZON_MINUTES) for r in runways),
        default=HORIZON_MINUTES,
    )
    fuel_failures = [
        f.id for f in active
        if scenario.current_minute + f.fuel_margin_min < earliest_runway
    ]
    if fuel_failures:
        reasons.append("燃油截止早于首条跑道可用时刻：" + "、".join(fuel_failures))
        relaxations.append("将相关航班安全备降；燃油硬约束不可放宽")
    if not reasons:
        reasons.append("跑道间隔、机位周转、燃油截止与操作员限制组合后无可行解")
        relaxations += [
            "撤销平均延误上限等软限制",
            "启用远机位或延长机位可用窗口",
            "将低燃油航班安全备降",
        ]
    if active and not runways:
        options.append(RelaxationOption(
            id="declare_runway_recovery",
            label="添加跑道恢复假设",
            effect="为一条关闭跑道设置 T+20 的预计恢复时刻；跑道不会被立即开放",
        ))
    if active and not gates:
        options.append(RelaxationOption(
            id="enable_remote_stand",
            label="释放备用机位",
            effect="将一个远机位或最晚可用机位加入恢复资源池",
        ))
    if fuel_failures:
        options.append(RelaxationOption(
            id="divert_fuel_critical",
            label="安全备降低燃油航班",
            effect="仅移出无法等到首条跑道开放的低燃油航班",
        ))
    if intent and intent.average_delay_cap is not None:
        options.append(RelaxationOption(
            id="remove_delay_cap",
            label="撤销平均延误上限",
            effect="只移除操作员设置的软上限，不改变任何安全硬约束",
        ))
    if not options:
        options.append(RelaxationOption(
            id="enable_remote_stand",
            label="释放备用机位",
            effect="增加一个可用机位以解除跑道—机位组合冲突",
        ))
    certificate = explain_infeasibility(scenario, intent)
    return InfeasibilityReport(
        reasons=reasons, relaxations=relaxations, options=options,
        **certificate,
    )


def solve_candidate(
    scenario: ScenarioSpec,
    profile: PlanProfile,
    intent: OperatorIntent | None = None,
    timeout_seconds: float = .45,
    frozen_assignments: Iterable[FlightAssignment] = (),
    latest_landing_by_flight: dict[str, int] | None = None,
    turnaround_buffer_by_flight: dict[str, int] | None = None,
    hint_assignments: Iterable[FlightAssignment] = (),
) -> tuple[PlanCandidate | None, InfeasibilityReport | None]:
    committed_flight_ids = {
        item.flight_id for item in scenario.committed_reservations
    }
    active = [
        flight
        for flight in scenario.flights
        if flight.state in ACTIVE_STATES
        and flight.id not in committed_flight_ids
    ]
    if not active:
        proof = validate_assignments(
            scenario,
            [],
            intent,
            turnaround_buffer_by_flight=turnaround_buffer_by_flight,
        )
        return PlanCandidate(
            profile=profile,
            title=_title(profile),
            assignments=[],
            actions=["当前没有需要重新调度的进港航班"],
            report=empty_report(),
            score=0,
            feasible=not any(row.startswith("FAIL") for row in proof),
            constraint_proof=proof,
            explanation="机场当前无需恢复调度。",
        ), None

    runways, gates = _usable_runways(scenario), _usable_gates(scenario)
    if not runways or not gates:
        return None, diagnose_infeasibility(scenario, intent)

    active_ids = {flight.id for flight in active}
    frozen_by_id = {
        item.flight_id: item for item in frozen_assignments if item.flight_id in active_ids
    }
    hint_by_id = {
        item.flight_id: item for item in hint_assignments if item.flight_id in active_ids
    }
    hinted_flights: set[str] = set()
    model = cp_model.CpModel()
    variables: dict[str, _Vars] = {}
    runway_intervals = {runway.id: [] for runway in runways}
    gate_intervals = {gate.id: [] for gate in gates}
    for index, reservation in enumerate(scenario.committed_reservations):
        interval = model.new_fixed_size_interval_var(
            reservation.start_minute,
            reservation.end_minute - reservation.start_minute,
            f"committed_{reservation.kind}_{reservation.resource_id}_{index}",
        )
        if (
            reservation.kind == "runway"
            and reservation.resource_id in runway_intervals
        ):
            runway_intervals[reservation.resource_id].append(interval)
        elif (
            reservation.kind == "gate"
            and reservation.resource_id in gate_intervals
        ):
            gate_intervals[reservation.resource_id].append(interval)
    terms: list[cp_model.LinearExpr] = []
    weights = profile_weights(profile, intent)
    weight_int = {key: max(1, round(value * 1000)) for key, value in weights.model_dump().items()}

    for flight in active:
        earliest = max(scenario.current_minute, flight.scheduled_arrival)
        latest = min(
            HORIZON_MINUTES,
            scenario.current_minute + flight.fuel_margin_min,
            (latest_landing_by_flight or {}).get(flight.id, HORIZON_MINUTES),
        )
        if earliest > latest:
            return None, diagnose_infeasibility(scenario, intent)
        landing = model.new_int_var(earliest, latest, f"landing_{flight.id}")
        hold = model.new_int_var(0, HORIZON_MINUTES, f"hold_{flight.id}")
        model.add(hold == landing - flight.scheduled_arrival)

        runway_choice: dict[str, cp_model.BoolVar] = {}
        for runway in runways:
            chosen = model.new_bool_var(f"runway_{flight.id}_{runway.id}")
            runway_choice[runway.id] = chosen
            open_minute = runway_planning_ready_minute(scenario, runway.id, profile)
            open_minute = int(open_minute if open_minute is not None else HORIZON_MINUTES)
            model.add(landing >= open_minute).only_enforce_if(chosen)
            runway_intervals[runway.id].append(
                model.new_optional_fixed_size_interval_var(
                    landing, runway.separation_min, chosen, f"rwy_interval_{flight.id}_{runway.id}"
                )
            )
            if intent and not intent.needs_confirmation:
                for constraint in intent.scheduled_constraints:
                    if constraint.resource_id != runway.id or constraint.minute is None:
                        continue
                    if constraint.kind == "close_resource":
                        model.add(landing + runway.separation_min <= constraint.minute).only_enforce_if(chosen)
                    elif constraint.kind == "reopen_resource":
                        model.add(landing >= constraint.minute).only_enforce_if(chosen)
        model.add_exactly_one(runway_choice.values())

        planned_taxi = next(
            (gate.taxi_minutes for gate in scenario.gates if gate.id == flight.planned_gate),
            min(gate.taxi_minutes for gate in gates),
        )
        gate_choice: dict[str, cp_model.BoolVar] = {}
        for gate in gates:
            chosen = model.new_bool_var(f"gate_{flight.id}_{gate.id}")
            gate_choice[gate.id] = chosen
            start = model.new_int_var(0, HORIZON_MINUTES + 30, f"gate_start_{flight.id}_{gate.id}")
            model.add(start == landing + gate.taxi_minutes).only_enforce_if(chosen)
            model.add(start >= gate.available_minute).only_enforce_if(chosen)
            robust_turnaround = TURNAROUND_MINUTES + max(
                0,
                int((turnaround_buffer_by_flight or {}).get(flight.id, 0)),
            )
            gate_intervals[gate.id].append(
                model.new_optional_fixed_size_interval_var(
                    start, robust_turnaround, chosen, f"gate_interval_{flight.id}_{gate.id}"
                )
            )
            if intent and not intent.needs_confirmation:
                for constraint in intent.scheduled_constraints:
                    if constraint.resource_id != gate.id or constraint.minute is None:
                        continue
                    if constraint.kind == "close_resource":
                        model.add(start + TURNAROUND_MINUTES <= constraint.minute).only_enforce_if(chosen)
                    elif constraint.kind == "reopen_resource":
                        model.add(start >= constraint.minute).only_enforce_if(chosen)
        fixed = frozen_by_id.get(flight.id)
        if fixed is not None:
            if fixed.runway not in runway_choice or fixed.gate not in gate_choice:
                return None, diagnose_infeasibility(scenario, intent)
            model.add(landing == fixed.landing_minute)
            model.add(runway_choice[fixed.runway] == 1)
            model.add(gate_choice[fixed.gate] == 1)
        else:
            hint = hint_by_id.get(flight.id)
            hint_ready = (
                hint is not None
                and earliest <= hint.landing_minute <= latest
                and hint.runway in runway_choice
                and hint.gate in gate_choice
                and not any(
                    item.startswith("FAIL")
                    for item in validate_assignments(
                        scenario,
                        [hint],
                        intent,
                        require_complete=False,
                        turnaround_buffer_by_flight=turnaround_buffer_by_flight,
                    )
                )
                and (
                    profile != PlanProfile.resilient
                    or runway_p90_ready_minute(scenario, hint.runway) is None
                    or hint.landing_minute
                    >= int(runway_p90_ready_minute(scenario, hint.runway) or 0)
                )
            )
            if hint_ready and hint is not None:
                # CP-SAT hints guide incumbent discovery only. Every variable
                # remains free and all hard constraints stay authoritative.
                model.add_hint(landing, hint.landing_minute)
                model.add_hint(
                    hold,
                    max(0, hint.landing_minute - flight.scheduled_arrival),
                )
                for runway_id, chosen in runway_choice.items():
                    model.add_hint(chosen, int(runway_id == hint.runway))
                for gate_id, chosen in gate_choice.items():
                    model.add_hint(chosen, int(gate_id == hint.gate))
                hinted_flights.add(flight.id)
        model.add_exactly_one(gate_choice.values())

        gate_delay = model.new_int_var(0, HORIZON_MINUTES + 30, f"gate_delay_{flight.id}")
        gate_delay_expr = landing - flight.scheduled_arrival - planned_taxi + sum(gate.taxi_minutes * gate_choice[gate.id] for gate in gates)
        model.add_max_equality(gate_delay, [gate_delay_expr, 0])
        if flight.connections:
            buffer = risk_adjusted_connection_buffer_minutes(
                connection_buffer_minutes(scenario.seed, flight.id),
                scenario.weather.severity,
            )
            connection_late = model.new_int_var(0, HORIZON_MINUTES + 30, f"connection_late_{flight.id}")
            model.add_max_equality(connection_late, [gate_delay - buffer, 0])
            connection_missed = model.new_bool_var(f"connection_missed_{flight.id}")
            model.add(gate_delay >= buffer + 1).only_enforce_if(connection_missed)
            model.add(gate_delay <= buffer).only_enforce_if(connection_missed.Not())
            terms.append(connection_missed * flight.connections * weight_int["connections"] * 4)
            terms.append(connection_late * flight.connections * weight_int["connections"])
        if flight.medical_priority:
            model.add(landing <= min(latest, scenario.current_minute + 8))

        terms.append(gate_delay * weight_int["delay"] * 10)
        terms.append(hold * max(1, round(30 / max(flight.fuel_margin_min, 8))) * weight_int["fuel_risk"])
        terms.append(gate_delay * max(1, round(flight.emissions_factor * .58 * 10)) * weight_int["emissions"])
        for gate in gates:
            chosen = gate_choice[gate.id]
            if gate.id != flight.planned_gate:
                terms.append(chosen * weight_int["conflicts"] * 180)
            if gate.remote:
                terms.append(chosen * weight_int["conflicts"] * 260)
            emissions = round((gate.taxi_minutes + (4 if gate.remote else 0)) * flight.emissions_factor * 10)
            terms.append(chosen * emissions * weight_int["emissions"])

        variables[flight.id] = _Vars(landing=landing, hold=hold, runway=runway_choice, gate=gate_choice)

    for intervals in runway_intervals.values():
        model.add_no_overlap(intervals)
    for intervals in gate_intervals.values():
        model.add_no_overlap(intervals)

    for priority in (flight for flight in active if flight.medical_priority):
        for other in (flight for flight in active if not flight.medical_priority):
            model.add(variables[priority.id].landing <= variables[other.id].landing)

    if intent and intent.average_delay_cap is not None:
        model.add(sum(v.hold for v in variables.values()) <= round(intent.average_delay_cap * len(active)))

    model.minimize(sum(terms))
    solver = cp_model.CpSolver()
    # Use deterministic search time as the binding budget. Wall-clock cutoffs can
    # otherwise return different feasible incumbents on identical replays.
    solver.parameters.max_deterministic_time = min(.12, max(.02, timeout_seconds))
    solver.parameters.max_time_in_seconds = max(1.0, timeout_seconds * 3)
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = scenario.seed + PROFILE_SOLVER_SEED_OFFSET[profile]
    status = solver.solve(model)
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        return None, diagnose_infeasibility(scenario, intent)

    assignments: list[FlightAssignment] = []
    actions: list[str] = []
    gate_by_id = {gate.id: gate for gate in gates}
    for flight in sorted(active, key=lambda item: solver.value(variables[item.id].landing)):
        current = variables[flight.id]
        runway_id = next(key for key, chosen in current.runway.items() if solver.value(chosen))
        gate_id = next(key for key, chosen in current.gate.items() if solver.value(chosen))
        gate = gate_by_id[gate_id]
        landing = solver.value(current.landing)
        hold = solver.value(current.hold)
        assignments.append(FlightAssignment(
            flight_id=flight.id,
            runway=runway_id,
            landing_minute=landing,
            gate=gate_id,
            hold_minutes=hold,
            taxi_minutes=gate.taxi_minutes,
            remote_stand=gate.remote,
        ))
        flag = "（医疗优先）" if flight.medical_priority else ""
        actions.append(f"{flight.id}{flag} 于 T+{landing} 使用 {runway_id}，分配至 {gate_id}")

    proof = validate_assignments(
        scenario,
        assignments,
        intent,
        turnaround_buffer_by_flight=turnaround_buffer_by_flight,
    )
    if profile == PlanProfile.resilient:
        robust_violations = [
            item for item in assignments
            if (
                runway_p90_ready_minute(scenario, item.runway) is not None
                and item.landing_minute
                < int(runway_p90_ready_minute(scenario, item.runway) or 0)
            )
        ]
        if robust_violations:
            proof.append("FAIL 稳健候选存在早于 P90 跑道就绪时刻的动作")
        else:
            proof.append("PASS 稳健候选将 P90 跑道就绪时刻写入 CP-SAT")
    if frozen_by_id:
        proof.append(
            f"PASS 因果局部修复固定了 {len(frozen_by_id)} 架未受影响航班"
        )
    if latest_landing_by_flight:
        proof.append(
            f"PASS CP-SAT 对 {len(latest_landing_by_flight)} 架航班强制执行 conformal 运行余量截止"
        )
    if turnaround_buffer_by_flight:
        proof.append(
            f"PASS CP-SAT 对 {len(turnaround_buffer_by_flight)} 架航班强制执行"
            "里程碑感知的稳健机位占用缓冲"
        )
    if hint_by_id:
        proof.append(
            f"PASS 应急预案仅作为 CP-SAT warm-start：接受 {len(hinted_flights)}/"
            f"{len(hint_by_id)} 架提示，全部变量仍由当前硬约束重新求解"
        )

    return PlanCandidate(
        profile=profile,
        title=_title(profile),
        assignments=assignments,
        actions=actions,
        report=empty_report(),
        score=float(solver.objective_value),
        feasible=not any(line.startswith("FAIL") for line in proof),
        constraint_proof=proof,
        explanation=_explanation(profile),
    ), None


def validate_assignments(
    scenario: ScenarioSpec,
    assignments: Iterable[FlightAssignment],
    intent: OperatorIntent | None = None,
    *,
    require_complete: bool = True,
    committed_reservations: Iterable[ResourceReservation] | None = None,
    turnaround_buffer_by_flight: Mapping[str, int] | None = None,
) -> list[str]:
    assignments = list(assignments)
    flight_by_id = {flight.id: flight for flight in scenario.flights}
    runway_by_id = {runway.id: runway for runway in scenario.runways}
    gate_by_id = {gate.id: gate for gate in scenario.gates}
    committed = list(
        scenario.committed_reservations
        if committed_reservations is None
        else committed_reservations
    )
    committed_flight_ids = {item.flight_id for item in committed}
    required_ids = {
        flight.id
        for flight in scenario.flights
        if flight.state in ACTIVE_STATES
        and flight.id not in committed_flight_ids
    }
    counts = Counter(item.flight_id for item in assignments)
    failures: list[str] = []

    def add(message: str) -> None:
        if message not in failures:
            failures.append(message)

    duplicate_ids = sorted(
        flight_id for flight_id, count in counts.items() if count > 1
    )
    if duplicate_ids:
        add("FAIL 航班被重复分配：" + "、".join(duplicate_ids))
    unknown_ids = sorted(set(counts) - set(flight_by_id))
    if unknown_ids:
        add("FAIL 计划引用未知航班：" + "、".join(unknown_ids))
    unexpected_ids = sorted(set(counts) - required_ids - set(unknown_ids))
    if unexpected_ids:
        add("FAIL 非待调度航班进入计划：" + "、".join(unexpected_ids))
    if require_complete:
        missing_ids = sorted(required_ids - set(counts))
        if missing_ids:
            add("FAIL 计划未覆盖全部待调度航班：" + "、".join(missing_ids))

    valid_assignments: list[FlightAssignment] = []
    for item in assignments:
        flight = flight_by_id.get(item.flight_id)
        runway = runway_by_id.get(item.runway)
        gate = gate_by_id.get(item.gate)
        if flight is None:
            continue
        if runway is None:
            add(f"FAIL {flight.id} 使用未知跑道 {item.runway}")
        if gate is None:
            add(f"FAIL {flight.id} 使用未知机位 {item.gate}")
        if runway is None or gate is None:
            continue
        valid_assignments.append(item)
        if item.landing_minute < max(
            scenario.current_minute, flight.scheduled_arrival
        ):
            add(f"FAIL {flight.id} 落地时刻早于当前时刻或计划到达时刻")
        open_minute = (
            scenario.current_minute
            if runway.status == ResourceStatus.open
            else runway.reopen_minute
        )
        if open_minute is None or item.landing_minute < open_minute:
            add(f"FAIL {flight.id} 使用了关闭跑道 {runway.id}")
        if item.landing_minute > scenario.current_minute + flight.fuel_margin_min:
            add(f"FAIL {flight.id} 超过燃油截止时间")
        if gate.status == ResourceStatus.reserved:
            add(f"FAIL {flight.id} 使用了预留机位 {gate.id}")
        if (
            gate.status == ResourceStatus.blocked
            and not (
                scenario.current_minute
                < gate.available_minute
                < HORIZON_MINUTES
            )
        ):
            add(f"FAIL {flight.id} 使用了规划窗口内不可恢复的故障机位 {gate.id}")
        canonical_hold = item.landing_minute - flight.scheduled_arrival
        canonical_taxi = gate.taxi_minutes
        canonical_gate_start = item.landing_minute + canonical_taxi
        if canonical_gate_start < gate.available_minute:
            add(f"FAIL {flight.id} 在 {gate.id} 可用前进位")
        if item.hold_minutes != canonical_hold:
            add(
                f"FAIL {flight.id} 等待时间字段不可信："
                f"{item.hold_minutes} != {canonical_hold}"
            )
        if item.taxi_minutes != canonical_taxi:
            add(
                f"FAIL {flight.id} 滑行时间字段不可信："
                f"{item.taxi_minutes} != {canonical_taxi}"
            )
        if item.remote_stand != gate.remote:
            add(f"FAIL {flight.id} 远机位字段与 {gate.id} 资源定义不一致")
        if (
            flight.medical_priority
            and item.landing_minute > scenario.current_minute + 8
        ):
            add(f"FAIL 医疗优先航班 {flight.id} 未在 8 分钟内落地")
        if intent and not intent.needs_confirmation:
            for constraint in intent.scheduled_constraints:
                if (
                    constraint.resource_id not in {item.runway, item.gate}
                    or constraint.minute is None
                ):
                    continue
                buffer = max(
                    0,
                    int((turnaround_buffer_by_flight or {}).get(
                        item.flight_id, 0
                    )),
                )
                operation_end = (
                    item.landing_minute + runway.separation_min
                    if constraint.resource_id == item.runway
                    else canonical_gate_start + TURNAROUND_MINUTES + buffer
                )
                operation_start = (
                    item.landing_minute
                    if constraint.resource_id == item.runway
                    else canonical_gate_start
                )
                if (
                    constraint.kind == "close_resource"
                    and operation_end > constraint.minute
                ):
                    add(
                        f"FAIL {flight.id} 在计划关闭后使用 "
                        f"{constraint.resource_id}"
                    )
                if (
                    constraint.kind == "reopen_resource"
                    and operation_start < constraint.minute
                ):
                    add(
                        f"FAIL {flight.id} 在计划开放前使用 "
                        f"{constraint.resource_id}"
                    )

    for message in validate_assignment_calendar(
        scenario,
        valid_assignments,
        committed_reservations=committed,
        turnaround_buffer_by_flight=turnaround_buffer_by_flight,
    ):
        add(message)

    if require_complete:
        medical_landings = [
            item.landing_minute
            for item in valid_assignments
            if flight_by_id[item.flight_id].medical_priority
        ]
        regular_landings = [
            item.landing_minute
            for item in valid_assignments
            if not flight_by_id[item.flight_id].medical_priority
        ]
        if (
            medical_landings
            and regular_landings
            and max(medical_landings) > min(regular_landings)
        ):
            add("FAIL 医疗优先航班排在普通航班之后")
    if (
        require_complete
        and intent
        and not intent.needs_confirmation
        and intent.average_delay_cap is not None
        and valid_assignments
    ):
        average_delay = sum(
            item.landing_minute
            - flight_by_id[item.flight_id].scheduled_arrival
            for item in valid_assignments
        ) / len(valid_assignments)
        if average_delay > intent.average_delay_cap:
            add(f"FAIL 平均延误 {average_delay:.1f} 超过操作员上限")
    return failures or [
        (
            "PASS 完整覆盖全部待调度航班，且无重复或未知航班"
            if require_complete
            else "PASS 局部候选身份、字段与资源约束有效"
        ),
        "PASS 跑道就绪、燃油截止与权威最小间隔满足",
        "PASS 机位可用性、唯一性与权威周转时间满足",
        "PASS 等待、滑行和远机位派生字段均由场景事实复算",
        "PASS 医疗优先及已确认的计划层资源约束满足",
    ]


def _title(profile: PlanProfile) -> str:
    return {
        PlanProfile.balanced: "均衡恢复",
        PlanProfile.passenger: "保护转机",
        PlanProfile.capacity: "容量优先",
        PlanProfile.resilient: "不确定性护航",
        PlanProfile.green: "低碳滑行",
        PlanProfile.operator: "按指挥目标",
    }[profile]


def _explanation(profile: PlanProfile) -> str:
    return {
        PlanProfile.balanced: "在延误、转机、燃油、安全冲突和滑行排放之间取得均衡。",
        PlanProfile.passenger: "以保护转机旅客为求解目标；实际收益需由同场景仿真核验，不保证每批随机样本都优于其他方案。",
        PlanProfile.capacity: "优先压低总体等待并尽快清空进港队列。",
        PlanProfile.resilient: "使用场景校准的 90% 时间上界预留燃油运行余量，再由 CP-SAT 生成可执行排序。",
        PlanProfile.green: "优先选择较短滑行路径，并避免不必要的远机位调度。",
        PlanProfile.operator: "按照已确认的自然语言目标权重生成。",
    }[profile]
