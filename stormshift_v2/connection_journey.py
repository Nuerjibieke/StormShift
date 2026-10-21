"""A single connecting departure driven by the authoritative execution clock.

This is a conservative, resource-checked post-arrival demonstration, not a claim
that inbound CP-SAT optimized departures or a full airport departure manager.
"""
from __future__ import annotations

from typing import Any
from .resource_calendar import intervals_overlap
from .schemas import ConnectingDeparture, FlightState, Phase, ResourceStatus, SessionState


def initialize_connection(state: SessionState) -> None:
    cursor = state.execution
    if cursor is None or cursor.connection is not None or state.mode.value != "guided":
        return
    flights = {f.id: f for f in state.scenario.flights}
    source = next((a.flight_id for a in cursor.pending_assignments
                   if flights[a.flight_id].connections > 0), None)
    if source:
        cursor.connection = ConnectingDeparture(
            source_flight_id=source,
            passenger_count=min(24, flights[source].connections),
        )


def connection_complete(state: SessionState) -> bool:
    c = state.execution.connection if state.execution else None
    return c is None or c.stage in {"completed", "cancelled"}


def holds_departure_gate(state: SessionState, flight_id: str) -> bool:
    c = state.execution.connection if state.execution else None
    return bool(c and c.carrier_flight_id == flight_id and c.stage in {"turnaround", "boarding", "pushback"})


def departure_conflicts(state: SessionState) -> list[str]:
    c = state.execution.connection if state.execution else None
    if c is None or not c.gate or not c.runway or c.boarding_start_minute is None:
        return ["出港资源尚未确定"]
    errors = []
    gate = next((g for g in state.scenario.gates if g.id == c.gate), None)
    runway = next((r for r in state.scenario.runways if r.id == c.runway), None)
    if not runway or runway.status != ResourceStatus.open:
        errors.append("出港跑道未确认开放")
    if not gate or gate.status == ResourceStatus.blocked:
        errors.append("出港机位不可用")
    if gate and gate.occupied_by not in {None, c.carrier_flight_id, c.flight_id}:
        errors.append("出港机位被另一架飞机占用")
    if state.execution.pending_assignments:
        errors.append("进港分配尚未全部提交，出港不能占用同一资源")
    if any(o.stage == "taxiing" for o in state.execution.committed_operations):
        errors.append("仍有进港飞机滑行，出港暂不进入共享地面通道")
    for row in state.scenario.committed_reservations:
        if row.kind == "runway" and row.resource_id == c.runway and intervals_overlap(
            row.start_minute, row.end_minute, c.takeoff_minute, c.airborne_minute + (runway.separation_min if runway else 3)
        ):
            errors.append("出港跑道时间窗与已提交航班冲突")
        if row.kind == "gate" and row.resource_id == c.gate and row.flight_id != c.carrier_flight_id and intervals_overlap(
            row.start_minute, row.end_minute, c.boarding_start_minute, c.taxi_start_minute
        ):
            errors.append("出港机位时间窗与已提交航班冲突")
    return sorted(set(errors))


def process_connection(service: Any, state: SessionState) -> None:
    cursor = state.execution
    c = cursor.connection if cursor else None
    if c is None or c.stage in {"completed", "cancelled", "blocked"}:
        return
    now = state.scenario.current_minute
    ops = {o.flight_id: o for o in cursor.committed_operations}

    def record(kind: str, summary: str) -> None:
        service._record(state, kind, {
            "flight_id": c.flight_id, "source_flight_id": c.source_flight_id,
            "gate": c.gate, "runway": c.runway, "summary": summary,
            "passenger_count": c.passenger_count, "boarded_count": c.boarded_count,
            "connection": c.model_dump(mode="json"),
            "scope": "single synthetic connection; separate from inbound forecast",
        })

    source_flight = next((f for f in state.scenario.flights if f.id == c.source_flight_id), None)
    if source_flight and source_flight.state == FlightState.diverted:
        c.stage = "cancelled"; c.next_minute = None
        record("connection_cancelled", "进港航班备降，本组转机演示取消，不报告成功")
        return
    source = ops.get(c.source_flight_id)
    if c.passenger_stage == "onboard" and source and source.stage in {"at_gate", "released"}:
        c.source_gate = source.gate; c.source_arrival_minute = now
        c.transfer_start_minute = now + 3; c.transfer_ready_minute = now + 9
        c.passenger_stage = "disembarking"
        record("connection_disembarking", f"{c.source_flight_id} 到位，跟随 {c.passenger_count} 名示意转机旅客下机")
    if c.passenger_stage == "disembarking" and now >= c.transfer_start_minute:
        c.passenger_stage = "transferring"
        record("connection_transferring", "旅客沿航站楼内部通道前往转机等候区")
    if c.passenger_stage == "transferring" and now >= c.transfer_ready_minute:
        c.passenger_stage = "waiting"
        record("connection_waiting", "旅客已到转机等候区，等待接续飞机完成周转")

    # Select the last usable actual airframe only after all future landings were committed.
    # The physical plane stays on screen: arrival callsign becomes NH701 at boarding.
    if c.carrier_flight_id is None and not cursor.pending_assignments and c.transfer_ready_minute is not None:
        candidates = [o for o in ops.values() if any(g.id == o.gate and not g.remote for g in state.scenario.gates)]
        carrier = max(candidates, key=lambda o: (o.gate_release_minute, o.flight_id), default=None)
        runways = [r for r in state.scenario.runways if r.status == ResourceStatus.open]
        if carrier and carrier.stage == "at_gate" and runways:
            c.carrier_flight_id = carrier.flight_id; c.gate = carrier.gate
            c.runway = sorted(runways, key=lambda r:r.id)[0].id
            ground_clear = max(o.gate_start_minute for o in ops.values()) + 2
            c.boarding_start_minute = max(carrier.gate_release_minute, c.transfer_ready_minute + 2, ground_clear)
            c.pushback_minute = c.boarding_start_minute + 6
            c.taxi_start_minute = c.pushback_minute + 4
            c.holding_short_minute = c.taxi_start_minute + 6
            c.takeoff_minute = c.holding_short_minute + 3
            c.airborne_minute = c.takeoff_minute + 3
            c.completed_minute = c.airborne_minute + 5
            c.stage = "turnaround"
            # Keep this same aircraft's stand until pushback clears it.
            gate = next(g for g in state.scenario.gates if g.id == c.gate)
            gate.available_minute = c.taxi_start_minute
            record("connection_aircraft_ready", f"{carrier.flight_id} 同一架飞机将周转执飞 {c.flight_id}；不是新飞机突然出现")

    transitions = [
        ("turnaround", "boarding", c.boarding_start_minute, "connection_boarding", "旅客从等候区走向登机口，登上接续航班"),
        ("boarding", "pushback", c.pushback_minute, "departure_pushback", "登机结束、舱门关闭，同一架飞机缓慢推出"),
        ("pushback", "taxiing", c.taxi_start_minute, "departure_taxi", "推出完成，飞机沿地面通道滑向跑道等待线"),
        ("taxiing", "holding_short", c.holding_short_minute, "departure_holding", "飞机停在跑道等待线外，复核起飞资源"),
        ("holding_short", "takeoff", c.takeoff_minute, "departure_takeoff", "跑道开放且无占用冲突，飞机获准进入并起飞"),
        ("takeoff", "airborne", c.airborne_minute, "departure_airborne", "飞机离地，沿离场方向爬升"),
        ("airborne", "completed", c.completed_minute, "connection_completed", "接续航班已飞离画面，这一批旅客完成演示行程"),
    ]
    for before, after, minute, kind, summary in transitions:
        if c.stage != before or minute is None or now < minute:
            continue
        if before in {"turnaround", "boarding", "holding_short"}:
            failures = departure_conflicts(state)
            if before == "turnaround" and c.passenger_stage != "waiting":
                failures.append("旅客尚未到达转机等候区")
            if before == "holding_short" and c.boarded_count != c.passenger_count:
                failures.append("登机尚未完成")
            if failures:
                c.stage = "blocked"; c.blocked_reason = "；".join(failures); c.next_minute = None
                cursor.status = "blocked"; state.phase = Phase.infeasible
                record("connection_blocked", c.blocked_reason)
                return
            c.safety_checks = ["旅客已到转机等候区", "机位无重叠", "跑道确认开放", "已提交起飞窗口无进港冲突"]
        c.stage = after
        if after == "boarding":
            c.passenger_stage = "boarding"
        if after == "pushback":
            c.passenger_stage = "boarded"; c.boarded_count = c.passenger_count
            c.safety_checks.append(f"登机完成：{c.boarded_count}/{c.passenger_count}，舱门已关闭")
        if after == "taxiing":
            gate = next(g for g in state.scenario.gates if g.id == c.gate)
            if gate.occupied_by == c.carrier_flight_id:
                gate.status = ResourceStatus.available; gate.occupied_by = None; gate.available_minute = now
        record(kind, summary)

    future = []
    if c.passenger_stage == "disembarking": future.append(c.transfer_start_minute)
    if c.passenger_stage == "transferring": future.append(c.transfer_ready_minute)
    stage_times = {"turnaround": c.boarding_start_minute, "boarding": c.pushback_minute,
                   "pushback": c.taxi_start_minute, "taxiing": c.holding_short_minute,
                   "holding_short": c.takeoff_minute, "takeoff": c.airborne_minute, "airborne": c.completed_minute}
    future.append(stage_times.get(c.stage))
    c.next_minute = min((t for t in future if t is not None and t > now), default=None)
