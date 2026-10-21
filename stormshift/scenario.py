"""Deterministic demo scenario used by the API and tests."""

from copy import deepcopy


SCENARIO = {
    "id": "storm-1827",
    "title": "18:27 雷暴恢复",
    "airport": "North Harbor International",
    "clock": "18:27",
    "weather": {"level": 4, "label": "强雷暴", "visibility_km": 3.2},
    "runways": [
        {"id": "07L", "status": "open", "capacity_per_hour": 19},
        {"id": "07R", "status": "closed", "reason": "雷击检查"},
    ],
    "gates": [
        {"id": "A1", "status": "occupied", "flight": "QN205"},
        {"id": "A2", "status": "blocked", "flight": "AX106"},
        {"id": "A3", "status": "available", "flight": None},
        {"id": "A4", "status": "available", "flight": None},
        {"id": "A5", "status": "reserved", "flight": "LV412"},
        {"id": "A6", "status": "available", "flight": None},
        {"id": "R1", "status": "available", "flight": None},
    ],
    "flights": [
        {"id": "SK218", "type": "A321", "state": "inbound", "planned_gate": "A2", "connections": 62, "fuel_margin_min": 34, "priority": "high"},
        {"id": "AX106", "type": "B738", "state": "gate_delayed", "planned_gate": "A2", "connections": 12, "departure_delay_min": 14, "priority": "normal"},
        {"id": "JR330", "type": "A320", "state": "inbound", "planned_gate": "A4", "connections": 18, "fuel_margin_min": 47, "priority": "normal"},
        {"id": "LV412", "type": "E190", "state": "approach", "planned_gate": "A5", "connections": 31, "fuel_margin_min": 39, "priority": "normal"},
        {"id": "QN205", "type": "A319", "state": "boarding", "planned_gate": "A1", "connections": 7, "priority": "normal"},
    ],
    "conflicts": [
        {"id": "gate-a2", "kind": "gate", "severity": "critical", "flights": ["SK218", "AX106"]},
        {"id": "runway-sequence", "kind": "runway", "severity": "high", "flights": ["SK218", "LV412"]},
        {"id": "taxi-crossing", "kind": "taxi", "severity": "medium", "flights": ["JR330", "LV412"]},
    ],
    "baseline": {"average_delay_min": 31, "missed_connections": 37, "remaining_conflicts": 3},
}


def get_scenario():
    """Return a copy so callers cannot mutate the canonical scenario."""
    return deepcopy(SCENARIO)
