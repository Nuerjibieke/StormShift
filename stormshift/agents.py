"""Role-separated deterministic agents for recovery planning."""

from dataclasses import asdict, dataclass
from typing import Dict, List


@dataclass(frozen=True)
class Proposal:
    agent: str
    role: str
    recommendation: str
    rationale: str
    affected_flights: List[str]
    expected_delay_min: int
    conflicts_after: int
    confidence: float


class RunwayAgent:
    name = "跑道 Agent"

    def evaluate(self, scenario: Dict, preference: str) -> Proposal:
        hold = 4 if preference == "connections" else 6
        return Proposal("runway", self.name, f"SK218 优先降落，LV412 等待 {hold} 分钟", "07R 关闭后，将单跑道序列留给转机风险最高的航班。", ["SK218", "LV412"], hold, 1, 0.92)


class GateAgent:
    name = "登机口 Agent"

    def evaluate(self, scenario: Dict, preference: str) -> Proposal:
        remote = "JR330" if preference != "capacity" else "LV412"
        return Proposal("gate", self.name, f"SK218 改停 A4，{remote} 使用远机位 R1", "避开被 AX106 占用的 A2，并保留一条无交叉滑行路径。", ["SK218", remote], 5, 0, 0.89)


class AirlineAgent:
    name = "航司 Agent"

    def evaluate(self, scenario: Dict, preference: str) -> Proposal:
        protected = next(f["connections"] for f in scenario["flights"] if f["id"] == "SK218")
        return Proposal("airline", self.name, f"优先保护 SK218 的 {protected} 名紧张转机旅客", "该航班的转机损失显著高于短时等待带来的运行成本。", ["SK218"], 3, 1, 0.94)


def validate_plan(scenario: Dict, plan: Dict) -> List[str]:
    errors = []
    closed = {r["id"] for r in scenario["runways"] if r["status"] == "closed"}
    if plan["landing_runway"] in closed:
        errors.append("计划使用了已关闭跑道")
    assignments = list(plan["gate_assignments"].values())
    if len(assignments) != len(set(assignments)):
        errors.append("多个航班被分配到同一机位")
    if "A2" in assignments:
        errors.append("A2 仍被 AX106 占用")
    if plan["hold_minutes"].get("LV412", -1) < 0:
        errors.append("等待时间不可为负数")
    return errors


def negotiate(scenario: Dict, preference: str = "balanced") -> Dict:
    if preference not in {"balanced", "connections", "capacity"}:
        preference = "balanced"
    agents = [RunwayAgent(), GateAgent(), AirlineAgent()]
    proposals = [asdict(agent.evaluate(scenario, preference)) for agent in agents]
    hold = 4 if preference == "connections" else 6
    remote_flight = "LV412" if preference == "capacity" else "JR330"
    gate_assignments = {"SK218": "A4", remote_flight: "R1"}
    gate_assignments["LV412" if remote_flight == "JR330" else "JR330"] = "A5" if remote_flight == "JR330" else "A3"
    plan = {
        "id": f"recovery-{preference}",
        "preference": preference,
        "title": "单跑道协同恢复方案",
        "landing_runway": "07L",
        "landing_sequence": ["SK218", "JR330", "LV412"],
        "gate_assignments": gate_assignments,
        "hold_minutes": {"LV412": hold},
        "actions": [
            "SK218 优先使用 07L 降落",
            "SK218 改停 A4，避开 A2 冲突",
            f"{remote_flight} 调整至远机位 R1",
            f"LV412 等待 {hold} 分钟后进入序列",
        ],
        "explanation": "协调器选择了同时消除登机口与滑行冲突、并保护转机旅客的可执行方案。",
    }
    errors = validate_plan(scenario, plan)
    outcome = {
        "average_delay_min": 16 if preference == "connections" else 18,
        "missed_connections": 6 if preference == "connections" else 8,
        "remaining_conflicts": 0,
        "connections_protected": 54 if preference == "connections" else 52,
    }
    return {
        "proposals": proposals,
        "coordinator": {"role": "恢复协调 Agent", "status": "validated" if not errors else "rejected", "summary": plan["explanation"]},
        "plan": plan,
        "validation": {"valid": not errors, "errors": errors},
        "baseline": scenario["baseline"],
        "outcome": outcome,
    }
