"""Machine-checkable interface contracts between specialist agents.

The monitor audits boundary assumptions exchanged by agents. It never schedules
aircraft and never treats free-form language as an executable action.
"""

from __future__ import annotations

from typing import Any

from .schemas import AgentProposal, PlanCandidate, ScenarioSpec


WEATHER_QUERY = "weather_reopen_nowcast"
GATE_QUERY = "critical_gate_readiness"


def _proposal(
    proposals: list[AgentProposal],
    agent: str,
) -> AgentProposal | None:
    return next((item for item in proposals if item.agent == agent), None)


def _check(
    *,
    check_id: str,
    producer: str,
    consumer: str,
    contract: str,
    status: str,
    evidence: str,
    consequence: str,
    query_id: str | None = None,
    hard_block: bool = False,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "producer": producer,
        "consumer": consumer,
        "contract": contract,
        "status": status,
        "evidence": evidence,
        "consequence": consequence,
        "query_id": query_id,
        "hard_block": hard_block,
    }


def arbitrate_agent_proposals(
    scenario: ScenarioSpec,
    proposals: list[AgentProposal],
    plan: PlanCandidate | None,
    resolved_query_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Audit cross-agent assumptions and route uncertainty to evidence or HITL."""
    resolved = set(resolved_query_ids or [])
    weather = _proposal(proposals, "Weather Agent")
    runway = _proposal(proposals, "Runway Agent")
    gate = _proposal(proposals, "Gate & Ground Agent")
    passenger = _proposal(proposals, "Airline & Passenger Agent")
    checks: list[dict[str, Any]] = []

    closed = next(
        (
            item for item in scenario.runways
            if item.reopen_minute is not None
        ),
        None,
    )
    weather_p90 = (
        float(weather.tool_evidence.get("reopen_p90_min", 0))
        if weather else 0.0
    )
    first_closed_use = (
        min(
            (
                item.landing_minute for item in plan.assignments
                if closed is not None and item.runway == closed.id
            ),
            default=None,
        )
        if plan else None
    )
    if closed is None or first_closed_use is None:
        checks.append(_check(
            check_id="weather_to_runway",
            producer="Weather Agent",
            consumer="Runway Agent",
            contract="天气可用时刻必须覆盖首个计划跑道动作",
            status="aligned",
            evidence=(
                "当前计划没有使用带预计恢复时刻的跑道"
                if closed is None else f"{closed.id} 未承载本轮航班"
            ),
            consequence="无需追加跑道观测",
        ))
    elif first_closed_use + 1e-9 < weather_p90:
        already_resolved = WEATHER_QUERY in resolved
        checks.append(_check(
            check_id="weather_to_runway",
            producer="Weather Agent",
            consumer="Runway Agent",
            contract="P90 跑道就绪时刻不得晚于无人值守首用时刻",
            status="resolved_watch" if already_resolved else "needs_evidence",
            evidence=(
                f"{closed.id} 点预测 T+{closed.reopen_minute}；"
                f"天气 P90 T+{weather_p90:.0f}；首个计划动作 T+{first_closed_use}"
            ),
            consequence=(
                "已获取最新观测；保留风险护栏监控"
                if already_resolved
                else "先获取跑道观测，再由 CP-SAT 重算"
            ),
            query_id=None if already_resolved else WEATHER_QUERY,
        ))
    else:
        checks.append(_check(
            check_id="weather_to_runway",
            producer="Weather Agent",
            consumer="Runway Agent",
            contract="P90 跑道就绪时刻必须覆盖首个计划跑道动作",
            status="aligned",
            evidence=(
                f"{closed.id} 天气 P90 T+{weather_p90:.0f}；"
                f"首个计划动作 T+{first_closed_use}"
            ),
            consequence="天气与跑道计划假设一致",
        ))

    gates = {item.id: item for item in scenario.gates}
    gate_violations: list[str] = []
    if plan:
        for assignment in plan.assignments:
            current = gates.get(assignment.gate)
            if current is None:
                gate_violations.append(f"{assignment.flight_id}:未知机位")
                continue
            gate_in = assignment.landing_minute + assignment.taxi_minutes
            if gate_in < current.available_minute:
                gate_violations.append(
                    f"{assignment.flight_id}:{assignment.gate} T+{gate_in}"
                    f"<可用 T+{current.available_minute}"
                )
    checks.append(_check(
        check_id="runway_to_gate",
        producer="Runway Agent",
        consumer="Gate & Ground Agent",
        contract="每次落地后的进位时刻必须晚于机位可用时刻",
        status="blocked" if gate_violations else "aligned",
        evidence=(
            "；".join(gate_violations[:3])
            if gate_violations
            else f"{len(plan.assignments) if plan else 0} 个跑道→机位交接全部通过"
        ),
        consequence=(
            "停止执行并重新求解"
            if gate_violations else "跑道与机位时间契约一致"
        ),
        hard_block=bool(gate_violations),
    ))

    flight_by_id = {item.id: item for item in scenario.flights}
    critical = (
        max(
            plan.assignments,
            key=lambda item: flight_by_id.get(item.flight_id).connections
            if flight_by_id.get(item.flight_id) else 0,
            default=None,
        )
        if plan else None
    )
    shield_rows = {
        str(item.get("flight_id")): item
        for item in (plan.risk_shield.get("rows", []) if plan else [])
    }
    critical_row = shield_rows.get(critical.flight_id, {}) if critical else {}
    exposed = bool(critical_row.get("transfer_exposed", False))
    critical_passengers = (
        flight_by_id[critical.flight_id].connections
        if critical and critical.flight_id in flight_by_id else 0
    )
    gate_resolved = GATE_QUERY in resolved
    if exposed:
        checks.append(_check(
            check_id="gate_to_passenger",
            producer="Gate & Ground Agent",
            consumer="Airline & Passenger Agent",
            contract="关键转机波次的机位服务链必须有可验证余量",
            status="resolved_watch" if gate_resolved else "needs_evidence",
            evidence=(
                f"{critical.flight_id} 在 {critical.gate} 承载 "
                f"{critical_passengers} 名转机旅客且进入尾部观察"
            ),
            consequence=(
                "地服观测已获取；继续监控转机尾部风险"
                if gate_resolved
                else "确认机位、拖车与廊桥状态后重算"
            ),
            query_id=None if gate_resolved else GATE_QUERY,
        ))
    else:
        checks.append(_check(
            check_id="gate_to_passenger",
            producer="Gate & Ground Agent",
            consumer="Airline & Passenger Agent",
            contract="关键转机波次的机位服务链必须有可验证余量",
            status="aligned",
            evidence=(
                f"{critical.flight_id} 的转机包络未越界"
                if critical else "当前没有需评估的关键转机航班"
            ),
            consequence="机位服务链与旅客保护目标一致",
        ))

    shield = plan.risk_shield if plan else {}
    unattended_blocked = bool(
        not plan
        or not plan.feasible
        or shield.get("blocks_unattended_execution", False)
    )
    checks.append(_check(
        check_id="safety_to_coordinator",
        producer="Safety Critic",
        consumer="Recovery Coordinator",
        contract="协调器不得越过硬约束或不确定性执行闸门",
        status="human_required" if unattended_blocked else "aligned",
        evidence=(
            str(shield.get("summary", "没有可执行候选"))
            if unattended_blocked
            else "硬约束通过，Risk Shield 允许在受控包络内审批"
        ),
        consequence=(
            "只能人工审批或安全停机"
            if unattended_blocked else "可进入人工批准节点"
        ),
        hard_block=bool(plan is not None and not plan.feasible),
    ))

    unresolved = [
        item for item in checks if item["status"] == "needs_evidence"
    ]
    human_required = [
        item for item in checks if item["status"] == "human_required"
    ]
    hard_blocks = [item for item in checks if item["hard_block"]]
    preferred_queries = [WEATHER_QUERY, GATE_QUERY]
    available_queries = {
        str(item["query_id"])
        for item in unresolved if item["query_id"]
    }
    recommended_query_id = next(
        (item for item in preferred_queries if item in available_queries),
        None,
    )
    if hard_blocks:
        status = "blocked"
        next_action = "stop_and_repair"
    elif unresolved:
        status = "needs_evidence"
        next_action = "observe_then_optimize"
    elif human_required:
        status = "human_required"
        next_action = "human_approval"
    else:
        status = "aligned"
        next_action = "proceed_to_approval"

    aligned = sum(item["status"] in {"aligned", "resolved_watch"} for item in checks)
    if status == "blocked":
        summary = f"{aligned}/{len(checks)} 个 Agent 接口契约一致；检测到硬冲突，禁止执行。"
    elif status == "needs_evidence":
        first = unresolved[0]
        summary = (
            f"{aligned}/{len(checks)} 个 Agent 接口契约已对齐；"
            f"{first['producer']} → {first['consumer']} 仍需外部证据。"
        )
    elif status == "human_required":
        summary = (
            f"{aligned}/{len(checks)} 个接口契约已对齐；"
            "不确定性闸门要求保留人工最终授权。"
        )
    else:
        summary = f"{aligned}/{len(checks)} 个 Agent 接口契约全部对齐，可进入人工批准节点。"

    return {
        "method": "typed cross-agent interface contracts + evidence-first arbitration",
        "status": status,
        "next_action": next_action,
        "proposal_count": len(proposals),
        "checks_total": len(checks),
        "aligned_count": aligned,
        "open_issue_count": len(unresolved) + len(human_required) + len(hard_blocks),
        "recommended_query_id": recommended_query_id,
        "checks": checks,
        "summary": summary,
        "policy": (
            "硬安全优先于多数意见；不确定性分歧优先请求证据；"
            "所有新航班动作必须由 CP-SAT 重算并再次通过 Safety Critic。"
        ),
        "hidden_reasoning": False,
        "source": "structured AgentProposal.tool_evidence + solver candidate + Risk Shield",
        "caveat": (
            "这是演练中的接口契约审计，不证明真实组织已达成共识；"
            "接入现场系统后应由权威数据源签名各项 Claim。"
        ),
    }

