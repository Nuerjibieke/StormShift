"""Repeatable guided, library, and sandbox scenarios."""

from __future__ import annotations

import random

from .schemas import (
    AirportLayoutSpec,
    DataMode,
    EventSpec,
    EventType,
    FlightSpec,
    FlightState,
    GateLayout,
    GateSpec,
    LayoutPoint,
    Mode,
    ResourceStatus,
    RunwayLayout,
    RunwaySpec,
    ScenarioSpec,
    SessionCreate,
    TerminalLayout,
    WeatherSpec,
)


def _runways(storm: bool = False) -> list[RunwaySpec]:
    return [
        RunwaySpec(id="07L", status=ResourceStatus.open, separation_min=3, capacity_per_hour=19),
        RunwaySpec(
            id="07R",
            status=ResourceStatus.closed if storm else ResourceStatus.open,
            separation_min=3,
            capacity_per_hour=19,
            reopen_minute=24 if storm else None,
            reason="雷击检查" if storm else None,
        ),
    ]


def _vhhh_layout() -> AirportLayoutSpec:
    """Public-layout-derived schematic; not surveyed geometry or live operations."""
    return AirportLayoutSpec(
        source_type="public_aip_simplified",
        source_label="Hong Kong CAD eAIP · VHHH public aerodrome layout",
        source_url="https://www.ais.gov.hk/eaip_20260709/2026-09-03-000000/html/VH-cover-en-US.html",
        effective_date="2026-09-03",
        geometry_fidelity="public-layout-simplified",
        terminal=TerminalLayout(id="T1", label="VHHH · T1 EXERCISE ZONE", x=.055, y=.36, width=.255, height=.32),
        runways=[
            RunwayLayout(id="07L", x=.40, y=.13, width=.56, height=.055),
            RunwayLayout(id="07C", x=.40, y=.45, width=.56, height=.055),
            RunwayLayout(id="07R", x=.40, y=.77, width=.56, height=.055),
        ],
        gates=[
            GateLayout(id="A1", x=.335, y=.40), GateLayout(id="A2", x=.365, y=.40),
            GateLayout(id="A3", x=.395, y=.40), GateLayout(id="A4", x=.335, y=.66),
            GateLayout(id="A5", x=.365, y=.66), GateLayout(id="A6", x=.395, y=.66),
            GateLayout(id="R1", x=.44, y=.70),
        ],
        taxiways=[
            [LayoutPoint(x=.37, y=.16), LayoutPoint(x=.37, y=.80)],
            [LayoutPoint(x=.31, y=.40), LayoutPoint(x=.40, y=.40)],
            [LayoutPoint(x=.31, y=.66), LayoutPoint(x=.40, y=.66)],
        ],
        disclaimer="跑道相对关系依据公开 AIP 简化绘制；机位编号、航班、天气、容量和调度结果均为合成演练数据。",
    )


def _vhhh_runways() -> list[RunwaySpec]:
    return [
        RunwaySpec(id="07L", status=ResourceStatus.open, separation_min=3, capacity_per_hour=19),
        RunwaySpec(id="07C", status=ResourceStatus.closed, separation_min=3, capacity_per_hour=19, reopen_minute=18, reason="雷雨区检查"),
        RunwaySpec(id="07R", status=ResourceStatus.closed, separation_min=3, capacity_per_hour=19, reopen_minute=24, reason="雷击检查"),
    ]


def _gates(blocked: str | None = None) -> list[GateSpec]:
    gates = [
        GateSpec(id=f"A{i}", status=ResourceStatus.available, taxi_minutes=4 + i % 3)
        for i in range(1, 7)
    ]
    gates.append(GateSpec(id="R1", status=ResourceStatus.available, remote=True, taxi_minutes=10))
    if blocked:
        for gate in gates:
            if gate.id == blocked:
                gate.status = ResourceStatus.blocked
                gate.available_minute = 30
    return gates


def storm_scenario(mode: Mode = Mode.guided, seed: int = 2026) -> ScenarioSpec:
    gates = _gates()
    gates[0].status, gates[0].occupied_by, gates[0].available_minute = ResourceStatus.occupied, "QN205", 16
    gates[1].status, gates[1].occupied_by, gates[1].available_minute = ResourceStatus.blocked, "AX106", 28
    gates[4].status = ResourceStatus.reserved
    flights = [
        FlightSpec(id="SK218", aircraft_type="A321", state=FlightState.inbound, scheduled_arrival=4, planned_gate="A2", connections=62, fuel_margin_min=34, emissions_factor=1.2, origin="TPE"),
        FlightSpec(id="AX106", aircraft_type="B738", state=FlightState.gate_delayed, scheduled_arrival=0, planned_gate="A2", connections=12, fuel_margin_min=70, origin="NRT"),
        FlightSpec(id="JR330", aircraft_type="A320", state=FlightState.inbound, scheduled_arrival=8, planned_gate="A4", connections=18, fuel_margin_min=47, origin="ICN"),
        FlightSpec(id="LV412", aircraft_type="E190", state=FlightState.approach, scheduled_arrival=6, planned_gate="A5", connections=31, fuel_margin_min=39, emissions_factor=0.8, origin="MNL"),
        FlightSpec(id="QN205", aircraft_type="A319", state=FlightState.boarding, scheduled_arrival=0, planned_gate="A1", connections=7, fuel_margin_min=80, origin="BKK"),
        FlightSpec(id="CX551", aircraft_type="A321", state=FlightState.inbound, scheduled_arrival=5, planned_gate="A3", connections=74, fuel_margin_min=42, emissions_factor=1.2, origin="KIX"),
        FlightSpec(id="TK608", aircraft_type="B738", state=FlightState.inbound, scheduled_arrival=7, planned_gate="A4", connections=58, fuel_margin_min=44, emissions_factor=1.15, origin="SIN"),
        FlightSpec(id="MH672", aircraft_type="A320", state=FlightState.approach, scheduled_arrival=9, planned_gate="A6", connections=66, fuel_margin_min=33, origin="KUL"),
        FlightSpec(id="SQ439", aircraft_type="A350", state=FlightState.inbound, scheduled_arrival=10, planned_gate="A1", connections=51, fuel_margin_min=52, emissions_factor=1.55, origin="SIN"),
        FlightSpec(id="OZ284", aircraft_type="A321", state=FlightState.inbound, scheduled_arrival=12, planned_gate="A5", connections=49, fuel_margin_min=46, emissions_factor=1.18, origin="ICN"),
    ]
    return ScenarioSpec(
        id="storm",
        name="强雷暴与单跑道恢复",
        airport="North Harbor International",
        mode=mode,
        seed=seed,
        runways=_runways(storm=True),
        gates=gates,
        flights=flights,
        weather=WeatherSpec(severity=4, label="强雷暴", visibility_km=3.2, closure_mean_min=24, closure_std_min=7),
        events=[
            EventSpec(type=EventType.storm, minute=0, severity=4, duration_min=24),
            EventSpec(type=EventType.runway_closed, minute=0, resource_id="07R", severity=4, duration_min=24),
        ],
    )


def vhhh_storm_scenario(seed: int = 2032) -> ScenarioSpec:
    scenario = storm_scenario(mode=Mode.scenarios, seed=seed)
    scenario.id = "vhhh_storm"
    scenario.name = "VHHH 公开布局 · 合成雷暴恢复"
    scenario.airport = "Hong Kong International · Synthetic Exercise"
    scenario.airport_icao = "VHHH"
    scenario.runways = _vhhh_runways()
    scenario.layout = _vhhh_layout()
    scenario.events = [
        EventSpec(type=EventType.storm, minute=0, severity=4, duration_min=24),
        EventSpec(type=EventType.runway_closed, minute=0, resource_id="07C", severity=4, duration_min=18),
        EventSpec(type=EventType.runway_closed, minute=0, resource_id="07R", severity=4, duration_min=24),
    ]
    scenario.disclaimer = "公开 VHHH 相对布局 + 合成运营数据；仅用于运营演练，不代表真实机场状态或决策。"
    return scenario


def gate_outage_scenario(seed: int = 2027) -> ScenarioSpec:
    gates = _gates("A3")
    gates[0].status, gates[0].occupied_by, gates[0].available_minute = ResourceStatus.occupied, "NX110", 12
    flights = [
        FlightSpec(id="NX110", state=FlightState.gate_delayed, planned_gate="A1", scheduled_arrival=0, connections=9, fuel_margin_min=70),
        FlightSpec(id="CS221", state=FlightState.inbound, planned_gate="A3", scheduled_arrival=4, connections=54, fuel_margin_min=37, origin="HND"),
        FlightSpec(id="EA144", state=FlightState.inbound, planned_gate="A4", scheduled_arrival=7, connections=21, fuel_margin_min=42, origin="SIN"),
        FlightSpec(id="PK508", state=FlightState.approach, planned_gate="A5", scheduled_arrival=3, connections=17, fuel_margin_min=31, origin="KUL"),
        FlightSpec(id="MR616", state=FlightState.inbound, planned_gate="A6", scheduled_arrival=11, connections=38, fuel_margin_min=45, origin="CGK"),
        FlightSpec(id="VT390", state=FlightState.inbound, planned_gate="A2", scheduled_arrival=14, connections=12, fuel_margin_min=51, origin="HAN"),
    ]
    return ScenarioSpec(
        id="gate_outage",
        name="廊桥故障与晚高峰连锁冲突",
        airport="North Harbor International",
        mode=Mode.scenarios,
        seed=seed,
        runways=_runways(),
        gates=gates,
        flights=flights,
        weather=WeatherSpec(severity=2, label="阵雨", visibility_km=8, closure_mean_min=0, closure_std_min=2),
        events=[EventSpec(type=EventType.gate_outage, minute=0, resource_id="A3", severity=3, duration_min=30)],
    )


def medical_scenario(seed: int = 2028) -> ScenarioSpec:
    gates = _gates()
    gates[2].status, gates[2].occupied_by, gates[2].available_minute = ResourceStatus.occupied, "TR220", 15
    flights = [
        FlightSpec(id="MED901", aircraft_type="A320", state=FlightState.approach, scheduled_arrival=1, planned_gate="A3", connections=0, fuel_margin_min=22, medical_priority=True, origin="DIVERT"),
        FlightSpec(id="TR220", state=FlightState.gate_delayed, scheduled_arrival=0, planned_gate="A3", connections=33, fuel_margin_min=70, origin="KIX"),
        FlightSpec(id="SK712", state=FlightState.approach, scheduled_arrival=2, planned_gate="A2", connections=46, fuel_margin_min=30, origin="PVG"),
        FlightSpec(id="AZ430", state=FlightState.inbound, scheduled_arrival=6, planned_gate="A4", connections=25, fuel_margin_min=38, origin="TPE"),
        FlightSpec(id="BH118", state=FlightState.inbound, scheduled_arrival=9, planned_gate="A5", connections=19, fuel_margin_min=44, origin="BKK"),
        FlightSpec(id="CA612", state=FlightState.inbound, scheduled_arrival=12, planned_gate="A6", connections=41, fuel_margin_min=48, origin="PEK"),
    ]
    return ScenarioSpec(
        id="medical",
        name="医疗备降与转机波次恢复",
        airport="North Harbor International",
        mode=Mode.scenarios,
        seed=seed,
        runways=_runways(),
        gates=gates,
        flights=flights,
        weather=WeatherSpec(severity=1, label="多云", visibility_km=12),
        events=[EventSpec(type=EventType.medical_diversion, minute=0, resource_id="MED901", severity=5)],
    )


def sandbox_scenario(seed: int = 2029, flight_count: int = 12) -> ScenarioSpec:
    rng = random.Random(seed)
    gates = _gates()
    flights = []
    prefixes = ["AX", "SK", "JR", "LV", "CS", "EA", "MR", "VT"]
    for index in range(flight_count):
        arrival = 2 + index * 3 + rng.randint(0, 2)
        flights.append(
            FlightSpec(
                id=f"{prefixes[index % len(prefixes)]}{200 + index}",
                aircraft_type=rng.choice(["A319", "A320", "A321", "B738", "E190"]),
                state=FlightState.approach if index < 2 else FlightState.inbound,
                scheduled_arrival=arrival,
                planned_gate=f"A{index % 6 + 1}",
                connections=rng.randint(5, 80),
                fuel_margin_min=arrival + rng.randint(24, 45),
                medical_priority=index == 0 and rng.random() < 0.15,
                emissions_factor=round(rng.uniform(0.7, 1.5), 2),
                origin=rng.choice(["TPE", "NRT", "ICN", "BKK", "SIN", "MNL"]),
            )
        )
    return ScenarioSpec(
        id="sandbox",
        name="开放机场恢复沙盘",
        airport="North Harbor International",
        mode=Mode.sandbox,
        seed=seed,
        runways=_runways(),
        gates=gates,
        flights=flights,
        weather=WeatherSpec(severity=2, label="不稳定天气", visibility_km=8, closure_mean_min=8, closure_std_min=4),
    )


SCENARIO_BUILDERS = {
    "storm": storm_scenario,
    "vhhh_storm": vhhh_storm_scenario,
    "gate_outage": gate_outage_scenario,
    "medical": medical_scenario,
}


def build_scenario(request: SessionCreate) -> ScenarioSpec:
    if request.data_mode == DataMode.imported and request.imported_scenario is not None:
        scenario = request.imported_scenario.model_copy(deep=True)
        scenario.seed = request.seed
        scenario.mode = request.mode
        return scenario
    if request.mode == Mode.sandbox:
        return sandbox_scenario(request.seed, request.sandbox_flights)
    builder = SCENARIO_BUILDERS.get(request.scenario_id, storm_scenario)
    scenario = builder(seed=request.seed)
    scenario.mode = request.mode
    return scenario


def scenario_catalog() -> list[dict[str, str]]:
    return [
        {"id": "storm", "name": "强雷暴与单跑道恢复", "summary": "跑道关闭、登机口冲突和转机旅客风险。"},
        {"id": "vhhh_storm", "name": "VHHH 公开布局 · 合成雷暴恢复", "summary": "真实三跑道相对关系，合成航班与运营状态。"},
        {"id": "gate_outage", "name": "廊桥故障与连锁冲突", "summary": "晚高峰关键机位不可用，重新分配地面资源。"},
        {"id": "medical", "name": "医疗备降与转机波次", "summary": "医疗航班优先插队，同时保护后续转机网络。"},
    ]
