"""LangGraph supervisor workflow around deterministic tools and safety gates."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

import numpy as np
from langgraph.graph import END, START, StateGraph

from .agent_arbitration import arbitrate_agent_proposals
from .passenger import connection_risk_margin_minutes, is_tight_connection_group
from .runway_assurance import runway_p90_ready_minute
from .optimizer import validate_assignments
from .schemas import (
    AgentProposal,
    AgentTrace,
    FlightAssignment,
    InfeasibilityReport,
    OperatorIntent,
    PlanCandidate,
    PlanProfile,
    ResourceStatus,
    ScenarioSpec,
)
from .simulation import build_passenger_graph, generate_ranked_candidates


class RecoveryState(TypedDict, total=False):
    scenario: ScenarioSpec
    intent: OperatorIntent | None
    profiles: list[PlanProfile]
    runs: int
    frozen_assignments: list[FlightAssignment]
    hint_assignments: list[FlightAssignment]
    resolved_query_ids: list[str]
    traces: Annotated[list[AgentTrace], operator.add]
    proposals: Annotated[list[AgentProposal], operator.add]
    candidates: list[PlanCandidate]
    failure: InfeasibilityReport | None
    arbitration: dict[str, Any]
    arbitration_by_plan: dict[str, dict[str, Any]]
    recommendation_id: str | None


def _weather(state: RecoveryState) -> dict:
    scenario = state["scenario"]
    weather = scenario.weather
    rng = np.random.default_rng(scenario.seed)
    samples = np.maximum(
        scenario.current_minute,
        rng.normal(weather.closure_mean_min, max(1, weather.closure_std_min), 200),
    )
    sample_p90 = float(np.percentile(samples, 90)) if weather.closure_mean_min else 0
    constrained_runway = next(
        (item for item in scenario.runways if item.reopen_minute is not None),
        None,
    )
    contract_p90 = (
        runway_p90_ready_minute(scenario, constrained_runway.id)
        if constrained_runway is not None
        else None
    )
    p90 = float(contract_p90 if contract_p90 is not None else sample_p90)
    trace = AgentTrace(
        agent="Weather Agent",
        role="天气概率",
        status="completed",
        summary=f"{weather.label}等级 {weather.severity}；跑道恢复 P90 约 T+{p90:.0f} 分钟",
        tool="probabilistic-weather-model",
        confidence=.88,
    )
    proposal = AgentProposal(
        agent=trace.agent,
        summary=trace.summary,
        observations=[
            f"关闭时长均值 T+{weather.closure_mean_min:.1f}",
            f"P90 跑道恢复 T+{p90:.0f}",
        ],
        recommendations=["将 P90 就绪时刻交给 Runway Agent 做边界检查"],
        confidence=.88,
        tool_evidence={
            "weather_severity": weather.severity,
            "closure_mean_min": weather.closure_mean_min,
            "closure_std_min": weather.closure_std_min,
            "reopen_p90_min": round(p90, 2),
            "sample_p90_min": round(sample_p90, 2),
            "contract": "shared point/P90 runway readiness",
            "sample_count": 200,
        },
    )
    return {"traces": [trace], "proposals": [proposal]}


def _runway(state: RecoveryState) -> dict:
    scenario = state["scenario"]
    available = [
        runway.id for runway in scenario.runways
        if runway.status == ResourceStatus.open or runway.reopen_minute is not None
    ]
    capacity = sum(
        runway.capacity_per_hour for runway in scenario.runways
        if runway.status == ResourceStatus.open
    )
    trace = AgentTrace(
        agent="Runway Agent",
        role="跑道容量与序列",
        status="completed",
        summary=f"可规划跑道 {' / '.join(available) or '无'}；当前容量 {capacity} 架次/小时",
        tool="runway-capacity-model",
    )
    proposal = AgentProposal(
        agent=trace.agent,
        summary=trace.summary,
        observations=[f"当前开放容量 {capacity} 架次/小时"],
        recommendations=["把跑道开放点预测作为 CP-SAT 最早使用边界"],
        confidence=.96,
        tool_evidence={
            "available_runways": available,
            "open_capacity_per_hour": capacity,
            "point_reopen_by_runway": {
                item.id: item.reopen_minute for item in scenario.runways
            },
        },
    )
    return {"traces": [trace], "proposals": [proposal]}


def _gate(state: RecoveryState) -> dict:
    scenario = state["scenario"]
    blocked = [
        gate.id for gate in scenario.gates
        if gate.status in {ResourceStatus.blocked, ResourceStatus.occupied}
    ]
    remote = [gate.id for gate in scenario.gates if gate.remote]
    trace = AgentTrace(
        agent="Gate & Ground Agent",
        role="机位与地面资源",
        status="completed",
        summary=f"{len(blocked)} 个受限机位；远机位 {' / '.join(remote) or '不可用'} 可作为恢复缓冲",
        tool="gate-resource-graph",
        confidence=.94,
    )
    proposal = AgentProposal(
        agent=trace.agent,
        summary=trace.summary,
        observations=[f"受限机位 {len(blocked)} 个", f"远机位 {len(remote)} 个"],
        recommendations=["逐航班验证落地后进位时刻与机位释放窗口"],
        confidence=.94,
        tool_evidence={
            "blocked_gates": blocked,
            "remote_gates": remote,
            "availability_by_gate": {
                item.id: item.available_minute for item in scenario.gates
            },
        },
    )
    return {"traces": [trace], "proposals": [proposal]}


def _passenger(state: RecoveryState) -> dict:
    scenario = state["scenario"]
    graph = build_passenger_graph(scenario)
    passengers = sum(
        int(data.get("passengers", 0)) for _, _, data in graph.edges(data=True)
    )
    risk_margin = connection_risk_margin_minutes(scenario.weather.severity)
    exposed = sum(
        1 for _, _, data in graph.edges(data=True)
        if is_tight_connection_group(data.get("buffer_min", 99), scenario.weather.severity)
    )
    trace = AgentTrace(
        agent="Airline & Passenger Agent",
        role="航司与旅客网络",
        status="completed",
        summary=f"{passengers} 名转机旅客进入影响图；{exposed} 个紧衔接组，P90 风险裕度 {risk_margin} 分钟",
        tool="networkx-transfer-graph + risk-adjusted-buffer",
        confidence=.91,
    )
    proposal = AgentProposal(
        agent=trace.agent,
        summary=trace.summary,
        observations=[f"转机旅客 {passengers} 名", f"紧衔接组 {exposed} 个"],
        recommendations=["关键转机波次需要机位服务链证据"],
        confidence=.91,
        tool_evidence={
            "transfer_passengers": passengers,
            "tight_connection_groups": exposed,
            "risk_margin_min": risk_margin,
            "graph_edges": graph.number_of_edges(),
        },
    )
    return {"traces": [trace], "proposals": [proposal]}


def _optimize(state: RecoveryState) -> dict:
    candidates, failure = generate_ranked_candidates(
        state["scenario"],
        state.get("profiles", [PlanProfile.balanced]),
        state.get("intent"),
        runs=state.get("runs", 50),
        frozen_assignments=state.get("frozen_assignments"),
        hint_assignments=state.get("hint_assignments"),
    )
    if not candidates:
        summary = "硬约束组合不可行，已返回可验证冲突核心"
        status = "failed"
    else:
        summary = f"CP-SAT 生成、Monte Carlo 评估 {len(candidates)} 个候选；Risk Shield 推荐 {candidates[0].title}"
        frozen = state.get("frozen_assignments", [])
        if frozen:
            summary += f"；局部修复边界固定 {len(frozen)} 架航班"
        status = "completed"
    traces = [AgentTrace(
        agent="CP-SAT Decision Engine",
        role="约束求解与随机仿真",
        status=status,
        summary=summary,
        tool="ortools-cpsat + simpy + conformal-risk-tier + epsilon-pareto",
        confidence=.97 if candidates else .7,
    )]
    if failure and failure.conflict_core:
        traces.append(AgentTrace(
            agent="Feasibility Core Analyst",
            role="不可行证书与安全处置",
            status="completed",
            summary=failure.summary,
            tool="cp-sat-assumption-core + deletion-filtered-IIS",
            confidence=1.0 if failure.core_irreducible else .8,
        ))
    return {
        "candidates": candidates,
        "failure": failure,
        "traces": traces,
    }


def _safety(state: RecoveryState) -> dict:
    scenario = state["scenario"]
    safe: list[PlanCandidate] = []
    rejected = 0
    for candidate in state.get("candidates", []):
        solver_evidence = list(candidate.constraint_proof)
        proof = validate_assignments(scenario, candidate.assignments, state.get("intent"))
        for item in solver_evidence:
            if item.startswith("PASS ") and item not in proof:
                # Preserve solver provenance after independent revalidation.
                proof.append(item)
        frozen = state.get("frozen_assignments", [])
        if frozen:
            proof.append(
                f"PASS 因果局部修复固定了 {len(frozen)} 架未受影响航班"
            )
        candidate.constraint_proof = proof
        candidate.feasible = not any(line.startswith("FAIL") for line in proof)
        if candidate.feasible:
            safe.append(candidate)
        else:
            rejected += 1
    return {
        "candidates": safe,
        "traces": [AgentTrace(
            agent="Safety Critic",
            role="独立安全闸门",
            status="completed" if safe else "failed",
            summary=(
                f"{len(safe)} 个候选通过全部硬约束；拒绝 {rejected} 个"
                if safe else "没有候选通过跑道、机位、燃油和医疗优先检查"
            ),
            tool="constraint-proof-validator",
            confidence=1,
        )],
    }


def _risk_shield(state: RecoveryState) -> dict:
    candidates = state.get("candidates", [])
    if not candidates:
        status, summary, confidence = "failed", "没有可评估候选", .6
    else:
        shield = candidates[0].risk_shield
        status = "completed"
        summary = (
            f"{str(shield.get('status', 'watch')).upper()} · "
            f"{shield.get('summary', '等待不确定性证据')}"
        )
        adaptive = shield.get("adaptive_envelope", {})
        if str(adaptive.get("status", "")).startswith("applied"):
            summary += f"；闭环响应：{adaptive.get('summary', '')}"
        confidence = .94
    return {"traces": [AgentTrace(
        agent="Conformal Risk Shield",
        role="不确定性运行闸门",
        status=status,
        summary=summary,
        tool="split-conformal + e-process + milestone-aware CP-SAT envelope",
        confidence=confidence,
    )]}


def _arbitrate(state: RecoveryState) -> dict:
    candidates = state.get("candidates", [])
    arbitrations = {
        candidate.id: arbitrate_agent_proposals(
            state["scenario"],
            state.get("proposals", []),
            candidate,
            state.get("resolved_query_ids", []),
        )
        for candidate in candidates
    }
    arbitration = (
        arbitrations[candidates[0].id]
        if candidates
        else arbitrate_agent_proposals(
            state["scenario"],
            state.get("proposals", []),
            None,
            state.get("resolved_query_ids", []),
        )
    )
    blocked = sum(
        item["status"] == "blocked"
        for item in arbitrations.values()
    )
    evidence_required = sum(
        item["status"] == "needs_evidence"
        for item in arbitrations.values()
    )
    return {
        "arbitration": arbitration,
        "arbitration_by_plan": arbitrations,
        "traces": [AgentTrace(
            agent="Cross-Agent Contract Arbiter",
            role="逐候选跨 Agent 假设与证据仲裁",
            status="completed" if blocked < len(candidates) else "failed",
            summary=(
                f"{len(candidates)} 个候选分别完成契约审计；"
                f"{blocked} 个硬阻断，{evidence_required} 个需要先补证据。"
                if candidates
                else arbitration["summary"]
            ),
            tool="typed-AgentProposal + per-candidate interface-contracts + evidence-routing",
            confidence=.98,
        )],
    }


def _coordinate(state: RecoveryState) -> dict:
    candidates = state.get("candidates", [])
    recommendation_id = candidates[0].id if candidates else None
    summary = (
        f"推荐 {candidates[0].title}，等待操作员审批"
        if candidates else "无法推荐执行方案，等待操作员处理不可行条件"
    )
    return {
        "recommendation_id": recommendation_id,
        "traces": [AgentTrace(
            agent="Recovery Coordinator",
            role="监督与人机协同",
            status="completed" if candidates else "failed",
            summary=summary,
            tool="human-approval-gate",
            confidence=.93 if candidates else .6,
        )],
    }


def build_workflow():
    graph = StateGraph(RecoveryState)
    graph.add_node("weather", _weather)
    graph.add_node("runway", _runway)
    graph.add_node("gate", _gate)
    graph.add_node("passenger", _passenger)
    graph.add_node("optimizer", _optimize)
    graph.add_node("safety_critic", _safety)
    graph.add_node("risk_shield", _risk_shield)
    graph.add_node("arbiter", _arbitrate)
    graph.add_node("coordinator", _coordinate)
    for node in ("weather", "runway", "gate", "passenger"):
        graph.add_edge(START, node)
    graph.add_edge(["weather", "runway", "gate", "passenger"], "optimizer")
    graph.add_edge("optimizer", "safety_critic")
    graph.add_edge("safety_critic", "risk_shield")
    graph.add_edge("risk_shield", "arbiter")
    graph.add_edge("arbiter", "coordinator")
    graph.add_edge("coordinator", END)
    return graph.compile()


recovery_workflow = build_workflow()


def run_recovery_workflow(
    scenario: ScenarioSpec,
    profiles: list[PlanProfile],
    intent: OperatorIntent | None = None,
    runs: int = 50,
    frozen_assignments: list[FlightAssignment] | None = None,
    resolved_query_ids: list[str] | None = None,
    hint_assignments: list[FlightAssignment] | None = None,
) -> RecoveryState:
    return recovery_workflow.invoke({
        "scenario": scenario,
        "profiles": profiles,
        "intent": intent,
        "runs": runs,
        "frozen_assignments": frozen_assignments or [],
        "hint_assignments": hint_assignments or [],
        "resolved_query_ids": resolved_query_ids or [],
        "traces": [],
        "proposals": [],
    })
