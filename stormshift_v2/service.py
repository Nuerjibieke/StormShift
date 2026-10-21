"""Application service: sessions, planning, approval, replay, and auto-recovery."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from .acdm import build_acdm_ledger, build_acdm_ledgers
from .agent_arbitration import arbitrate_agent_proposals
from .agents import run_recovery_workflow
from .approval_gate import (
    build_candidate_approval_gates,
    prioritize_required_evidence,
    require_human_approvable,
)
from .bargaining import nash_bargaining
from .contrastive import build_flight_contrastive_explanation
from .causal_attribution import attribute_recovery_burden
from .contract_repair import analyze_contract_repair
from .execution import (
    ExecutionSafetyViolation,
    advance_fuel_clock,
    commit_landing,
    create_execution_cursor,
    next_execution_minute,
    remaining_scope_complete,
)
from .falsification import discover_minimal_counterexamples
from .information_value import recommend_observation
from .config import Settings, settings
from .mitigation import (
    apply_intervention_outcome,
    optimize_intervention_portfolio,
)
from .outcome_audit import build_outcome_audit, intent_digest
from .operational_baseline import compare_operational_baselines
from .online_execution import advance_state, guided_secondary_event
from .connection_journey import initialize_connection
from .preference_learning import (
    encode_preference_observation,
    learn_operator_preferences,
)
from .data_sources import apply_weather_snapshot, fetch_live_snapshot, live_data_badge
from .llm import explain_recommendation, parse_intent
from .intent_compiler import merge_confirmed, confirmed_intent
from .intent_clarification import clarification_questions, compose_clarification
from .schemas import OperatorIntent
from .optimizer import solve_candidate, validate_assignments
from .passenger_rescue import optimize_passenger_rescue
from .policy import compile_contingency_policy, evaluate_policy_event
from .proof_carrying import issue_plan_certificate, verify_plan_certificate
from .recourse import analyze_recourse_impact, frozen_assignments, summarize_repair
from .resource_calendar import effective_turnaround_buffers
from .runtime_assurance import verify_runtime_contract
from .shadow_twin import evaluate_shadow_switch
from .twin_trust import assess_twin_trust
from .trusted_state import (
    apply_trusted_operational_state,
    build_trusted_operational_state,
    plan_evidence_basis,
)
from .robustness import (
    evaluate_adaptive_horizon,
    evaluate_candidate_regret,
)
from .scenarios import build_scenario
from .schemas import (
    AdvanceReceipt,
    AgentProposal,
    AgentTrace,
    ApprovalResponse,
    DataMode,
    EventRequest,
    ExecutionAdvanceRequest,
    ExecutionAdvanceResponse,
    ObservationRequest,
    EventSpec,
    EventType,
    ExecutionEvent,
    FlightAssignment,
    FlightSpec,
    FlightState,
    IntentRequest,
    IntentClarificationRequest,
    OperatorIntent,
    Phase,
    PlanCandidate,
    PreferenceRequest,
    PlanProfile,
    SandboxUpdate,
    PlanRequest,
    ResourceStatus,
    SessionCreate,
    SessionState,
)
from .simulation import counterfactual_summary, pareto_summary, simulate_candidate
from .store import SessionStore
from .stream import StreamManager, stream_manager


class SessionNotFound(KeyError):
    pass


class UnsafePlan(ValueError):
    pass


class UnsafeExecution(ValueError):
    pass


class StaleExecution(ValueError):
    pass


def _prepared_hint_assignments(
    policy_decision: dict[str, Any],
) -> list[FlightAssignment]:
    """Parse internal branch hints defensively; malformed hints are ignored."""
    parsed: list[FlightAssignment] = []
    prepared = policy_decision.get("prepared_response") or {}
    for item in prepared.get("assignments", []):
        try:
            parsed.append(FlightAssignment.model_validate(item))
        except (TypeError, ValueError):
            continue
    return parsed


def _build_action_diff(
    before: PlanCandidate,
    after: PlanCandidate,
) -> dict[str, Any]:
    """Describe only future action changes across a plan switch."""
    before_by_id = {item.flight_id: item for item in before.assignments}
    after_by_id = {item.flight_id: item for item in after.assignments}
    rows: list[dict[str, Any]] = []
    counts = {"changed": 0, "unchanged": 0, "added": 0, "removed": 0}
    for flight_id in sorted(set(before_by_id) | set(after_by_id)):
        old = before_by_id.get(flight_id)
        new = after_by_id.get(flight_id)
        old_payload = old.model_dump(mode="json") if old else None
        new_payload = new.model_dump(mode="json") if new else None
        if old is None:
            status = "added"
            fields = ["flight"]
        elif new is None:
            status = "removed"
            fields = ["flight"]
        else:
            fields = [
                field
                for field in ("runway", "landing_minute", "gate")
                if getattr(old, field) != getattr(new, field)
            ]
            status = "changed" if fields else "unchanged"
        counts[status] += 1
        rows.append({
            "flight_id": flight_id,
            "status": status,
            "changed_fields": fields,
            "before": old_payload,
            "after": new_payload,
        })
    return {
        "source_plan_id": before.id,
        "replacement_plan_id": after.id,
        **counts,
        "rows": rows,
        "scope": "uncommitted future assignments only",
    }

def _uncertainty_trace(plan: PlanCandidate, ledger: dict[str, Any]) -> AgentTrace:
    calibration = ledger["uncertainty_calibration"]
    return AgentTrace(
        agent="Uncertainty Calibration Analyst",
        role="场景校准时间包络",
        status="completed",
        summary=(
            f"{int(calibration['target_coverage'] * 100)}% conformal 包络已由 "
            f"{calibration['calibration_samples']} 个世界校准、"
            f"{calibration.get('evaluation_samples', 0)} 个独立世界留出验证；"
            f"实测覆盖率 {float(calibration.get('empirical_coverage', 0)) * 100:.1f}%，"
            f"平均上界余量 +{calibration['average_radius_min']} 分钟"
        ),
        tool="held-out-split-conformal + simpy-residual-banks",
        confidence=.94,
    )

def _information_trace(information: dict[str, Any]) -> AgentTrace:
    recommended = next(
        (
            item for item in information.get("queries", [])
            if item.get("id") == information.get("recommended_query_id")
        ),
        None,
    )
    return AgentTrace(
        agent="Active Sensing Advisor",
        role="决策相关信息获取",
        status="completed",
        summary=str(information.get("summary", "当前不需要追加观测")),
        tool="value-of-information + shared-stress-worlds" if recommended else "evidence-sufficiency-check",
        confidence=.90,
    )

def _bargaining_trace(result: dict[str, Any]) -> AgentTrace:
    return AgentTrace(
        agent="Stakeholder Bargaining Mediator",
        role="多方效用协商",
        status="completed",
        summary=str(result.get("summary", "当前没有可协商候选")),
        tool="normalized-utility-ledger + Nash-social-welfare",
        confidence=.93,
    )


def _shadow_twin_trace(result: dict[str, Any]) -> AgentTrace:
    return AgentTrace(
        agent="Shadow Twin Challenger",
        role="统计置信切换闸门",
        status="completed",
        summary=str(result.get("summary", "影子方案证据不足")),
        tool="common-random-numbers + paired-bootstrap-95ci",
        confidence=.95,
    )


def _twin_trust_trace(result: dict[str, Any]) -> AgentTrace:
    blocked = bool(result.get("blocks_unattended_execution", False))
    return AgentTrace(
        agent="Twin Trust Sentinel",
        role="数字孪生序贯失配监测",
        status="fallback" if blocked else "completed",
        summary=str(result.get("summary", "数字孪生可信度证据不可用")),
        tool="conformal-residuals + anytime-valid-e-process",
        confidence=.96 if result.get("familywise_streams") else .70,
    )


def _causal_attribution_trace(result: dict[str, Any]) -> AgentTrace:
    return AgentTrace(
        agent="Counterfactual Attribution Analyst",
        role="恢复负担 Shapley 归因",
        status="completed" if result.get("status") == "completed" else "fallback",
        summary=str(result.get("summary", "当前没有可归因的建模扰动")),
        tool="coalition CP-SAT + shared-seed SimPy + exact Shapley",
        confidence=.93 if result.get("additivity", {}).get("passed") else .72,
    )


def _passenger_rescue_trace(result: dict[str, Any]) -> AgentTrace:
    shortfall = result.get("status") == "capacity_shortfall"
    return AgentTrace(
        agent="Passenger Continuity Agent",
        role="转机旅客容量流改签",
        status="completed",
        summary=str(result.get("summary", "当前无需启动旅客改签")),
        tool="networkx min-cost-flow + conformal connection window",
        confidence=.91 if shortfall else .96,
    )


def _contract_repair_trace(result: dict[str, Any]) -> AgentTrace:
    blocked = result.get("status") == "safety_block"
    return AgentTrace(
        agent="Contract Reconciliation Agent",
        role="运营目标最小修复与反提案",
        status="fallback" if blocked else "completed",
        summary=str(result.get("recommendation", "当前契约无需修复")),
        tool="elastic constraints + lexicographic frontier + human counteroffer",
        confidence=.99 if result.get("hard_safety_relaxations") == 0 else .50,
    )


def _preference_trace(result: dict[str, Any]) -> AgentTrace:
    return AgentTrace(
        agent="Preference Elicitation Agent",
        role="操作员软目标主动学习",
        status="completed",
        summary=str(result.get("summary", "当前没有可比较的安全候选")),
        tool="Bayesian Bradley-Terry particles + mutual-information query",
        confidence=.90 if result.get("comparison_count", 0) else .72,
    )


def _mitigation_trace(result: dict[str, Any]) -> AgentTrace:
    return AgentTrace(
        agent="Intervention Portfolio Agent",
        role="二阶段地面资源干预",
        status="completed",
        summary=str(result.get("summary", "当前无需追加旅客保障资源")),
        tool="lexicographic CP-SAT + paired-seed SimPy",
        confidence=.96 if result.get("proof", {}).get(
            "operational_metrics_unchanged"
        ) else .72,
    )


def _trusted_state_traces(result: dict[str, Any]) -> list[AgentTrace]:
    quality = result["quality"]
    argumentation = result["argumentation"]
    return [
        AgentTrace(
            agent="Evidence Coordinator",
            role="冲突、过期与缺失事实仲裁",
            status="completed" if result["status"] == "ready" else "failed",
            summary=(
                f"发现 {quality['detected_conflicts']} 个冲突、"
                f"{quality['stale_excluded']} 条过期声明；"
                f"已排除非可信输入，未决关键事实 {quality['unresolved_critical']} 条"
            ),
            tool="typed-evidence-ledger + query/confirm/abstain",
            confidence=.99 if result["status"] == "ready" else .60,
        ),
        AgentTrace(
            agent="Formal Argumentation Agent",
            role="多源声明攻击图与拒答裁决",
            status="completed" if argumentation["status"] == "resolved" else "failed",
            summary=str(argumentation["summary"]),
            tool="Dung grounded semantics + NetworkX defeat graph",
            confidence=.98 if argumentation["conflict_free"] else .50,
        ),
        AgentTrace(
            agent="Verifiable Constraint Compiler",
            role="可信事实到求解约束",
            status="completed" if result["status"] == "ready" else "failed",
            summary=(
                f"{quality['solver_facts']} 条确认事实编译为 "
                f"{len(result['compiled_constraints'])} 条约束；"
                f"CP-SAT 输入指纹 {result['digest'][:10]}"
            ),
            tool="deterministic typed compiler + SHA-256 input contract",
            confidence=1.0,
        ),
    ]


def _trusted_state_infeasibility(result: dict[str, Any]) -> dict[str, Any]:
    unresolved = list(result.get("argumentation", {}).get("unresolved_keys", []))
    reasons = [
        f"关键运行事实 {key} 在 grounded argumentation 中仍为 undecided"
        for key in unresolved
    ] or ["关键运行事实未形成无冲突的可接受集合"]
    return {
        "method": "formal-argumentation evidence safety gate",
        "solver_status": "NOT_RUN_EVIDENCE_BLOCKED",
        "summary": "证据冲突尚未解决；系统已拒绝调用 CP-SAT，等待来源确认。",
        "reasons": reasons,
        "relaxations": [
            "查询更高权威或更新的数据源",
            "由操作员确认唯一事实值后重新编译约束",
        ],
        "conflict_core": [],
        "core_irreducible": False,
        "hard_safety_relaxations_allowed": 0,
        "solver_calls": 0,
    }


def _issue_plan_certificates(
    plans: list[PlanCandidate],
    trusted_state: dict[str, Any],
    issued_sequence: int,
) -> dict[str, dict[str, Any]]:
    return {
        plan.id: issue_plan_certificate(
            plan,
            trusted_state,
            issued_sequence=issued_sequence,
        )
        for plan in plans
    }


def _proof_certificate_trace(certificate: dict[str, Any]) -> AgentTrace:
    return AgentTrace(
        agent="Proof-Carrying Plan Verifier",
        role="证据到动作完整性证明",
        status="completed" if certificate["failed_obligations"] == 0 else "failed",
        summary=(
            f"{certificate['certificate_id']} · "
            f"{certificate['passed_obligations']}/{certificate['obligation_count']} "
            f"项证明义务满足 · {certificate['status']}"
        ),
        tool="canonical-json + sha256 + independent-obligation-checks",
        confidence=1.0,
    )


def _operational_baseline_trace(result: dict[str, Any]) -> AgentTrace:
    rows = {item["id"]: item for item in result["rows"]}
    solo = rows["solo_cp_sat"]["fault_trials"]
    evidence = rows["stormshift"]["fault_trials"]
    return AgentTrace(
        agent="Operational Baseline Auditor",
        role="现实基线与信息质量故障注入",
        status="completed",
        summary=(
            f"正常场景共享 CP-SAT 延误差 "
            f"{result['nominal_solver_gap_min']:.2f} 分钟；"
            f"迟到信息下裸 CP-SAT {solo['unsafe_execution_worlds']}/"
            f"{solo['worlds']} 次产生不可执行动作，证据协调为 "
            f"{evidence['unsafe_execution_worlds']} 次"
        ),
        tool="safety-aware-FCFS + shared-CP-SAT + fixed-seed fault worlds",
        confidence=.98,
    )


async def _falsify_candidates(
    scenario: Any,
    plans: list[PlanCandidate],
    intent: OperatorIntent | None,
) -> dict[str, dict[str, Any]]:
    results = await asyncio.gather(*[
        asyncio.to_thread(
            discover_minimal_counterexamples,
            scenario,
            candidate,
            intent,
        )
        for candidate in plans
    ])
    return {
        candidate.id: result
        for candidate, result in zip(plans, results, strict=True)
    }


def _falsification_trace(result: dict[str, Any]) -> AgentTrace:
    return AgentTrace(
        agent="Falsification Agent",
        role="计划反例搜索与安全修复",
        status="completed",
        summary=str(result.get("summary", "有界反例搜索完成")),
        tool="minimal counterexample search + hard-constraint oracle + CP-SAT repair",
        confidence=.98,
    )


class SessionService:
    def __init__(
        self,
        config: Settings = settings,
        store: SessionStore | None = None,
        streams: StreamManager = stream_manager,
    ) -> None:
        self.config = config
        self.store = store or SessionStore(config.database_path)
        self.streams = streams
        self._locks: dict[str, asyncio.Lock] = {}
        self._event_buffers: dict[str, list[ExecutionEvent]] = {}
        self._preview_slots = asyncio.Semaphore(2)

    def _lock(self, session_id: str) -> asyncio.Lock:
        self._locks.setdefault(session_id, asyncio.Lock())
        return self._locks[session_id]

    def require(self, session_id: str) -> SessionState:
        state = self.store.get_session(session_id)
        if state is None:
            raise SessionNotFound(session_id)
        # Recompute on every read so a post-write alteration cannot leave a
        # stale "verified" badge cached inside the serialized session state.
        state.decision_lab["event_integrity"] = self.store.verify_event_chain(session_id)
        return state

    async def preview_branches(self, session_id: str, request) -> dict[str, Any]:
        from time import time
        from .branch_preview import (
            build_branch_preview, preview_id, snapshot_digest, verify_preview_request,
        )
        # Copy under the existing session lock, then release it before simulation.
        # Preview never calls save_session, append_event, approve or advance.
        async with self._lock(session_id):
            source = self.require(session_id).model_copy(deep=True)
            verify_preview_request(source, request)
        key = f"branch-preview:{session_id}:{preview_id(source, request.plan_ids, feedback=request.feedback, config=self.config, runway_delay=request.runway_delay)}"
        report = self.store.get_cache(key, time())
        if report is None:
            async with self._preview_slots:
                report = self.store.get_cache(key, time())
                if report is None:
                    if request.runway_delay is not None:
                        report = await asyncio.to_thread(build_branch_preview, source, request.plan_ids,
                                                       runway_delay=request.runway_delay)
                    elif request.feedback:
                        report = await asyncio.to_thread(build_branch_preview, source, request.plan_ids,
                                                       feedback=True, config=self.config)
                    else:
                        report = await asyncio.to_thread(build_branch_preview, source, request.plan_ids)
                    self.store.set_cache(key, time() + 3600, report)
        current = self.require(session_id)
        return {**report, "status": (
            "fresh" if snapshot_digest(current) == report["source_digest"] else "stale"
        )}

    def get_branch_preview(self, session_id: str, preview_key: str) -> dict[str, Any]:
        from time import time
        from .branch_preview import snapshot_digest
        current = self.require(session_id)
        report = self.store.get_cache(f"branch-preview:{session_id}:{preview_key}", time())
        if report is None:
            raise KeyError("预演不存在或已过期 / Preview not found or expired")
        return {**report, "status": (
            "fresh" if snapshot_digest(current) == report["source_digest"] else "stale"
        )}

    def _compile_approval_gates(
        self,
        state: SessionState,
        trusted_state: dict[str, Any],
        arbitration_by_plan: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        audits = dict(
            arbitration_by_plan
            or state.decision_lab.get("agent_arbitration_by_plan", {})
        )
        if not audits:
            proposals: list[AgentProposal] = []
            for raw in state.decision_lab.get("agent_proposals", []):
                try:
                    proposals.append(AgentProposal.model_validate(raw))
                except (TypeError, ValueError):
                    continue
            if proposals:
                audits = {
                    plan.id: arbitrate_agent_proposals(
                        state.scenario,
                        proposals,
                        plan,
                        list(state.data_snapshot.get(
                            "resolved_observation_ids", []
                        )),
                    )
                    for plan in state.plans
                }
            else:
                audits = {
                    plan.id: {
                        "status": "human_required",
                        "checks": [],
                        "summary": (
                            "历史候选没有逐 Agent 证据记录；"
                            "仅允许在当前请求重新验证硬约束后人工批准。"
                        ),
                    }
                    for plan in state.plans
                }
        gates = build_candidate_approval_gates(
            state.plans,
            audits,
            trusted_state,
            list(state.data_snapshot.get("resolved_observation_ids", [])),
        )
        state.decision_lab["agent_arbitration_by_plan"] = audits
        state.decision_lab["approval_gates"] = gates
        if state.plans:
            first_id = state.plans[0].id
            state.decision_lab["agent_arbitration"] = audits.get(first_id, {})
            state.decision_lab["approval_gate"] = gates.get(first_id, {})
        return gates

    def _invalidate_observations_for_event(
        self,
        state: SessionState,
        event: EventSpec,
    ) -> list[str]:
        invalidation_map = {
            EventType.storm: {"weather_reopen_nowcast"},
            EventType.runway_closed: {"weather_reopen_nowcast"},
            EventType.runway_reopened: {"weather_reopen_nowcast"},
            EventType.gate_outage: {"critical_gate_readiness"},
            EventType.ground_vehicle_failure: {"critical_gate_readiness"},
            EventType.medical_diversion: {"critical_gate_readiness"},
        }
        affected = invalidation_map.get(event.type, set())
        resolved = set(
            state.data_snapshot.get("resolved_observation_ids", [])
        )
        invalidated = sorted(resolved.intersection(affected))
        if not invalidated:
            return []
        resolved.difference_update(invalidated)
        state.data_snapshot["resolved_observation_ids"] = sorted(resolved)
        observations = list(
            state.data_snapshot.get("active_observations", [])
        )
        for observation in observations:
            if observation.get("query_id") not in invalidated:
                continue
            observation["valid"] = False
            observation["invalidated_by_event_id"] = event.id
            observation["invalidated_minute"] = event.minute
            observation["invalidation_reason"] = (
                "新运营事件改变了该观测所绑定的资源事实"
            )
        state.data_snapshot["active_observations"] = observations
        self._record(
            state,
            "active_observation_invalidated",
            {
                "event_id": event.id,
                "event_type": event.type.value,
                "invalidated_query_ids": invalidated,
                "status": "must_observe_and_replan_again_if_required",
            },
            event.minute,
        )
        return invalidated

    def _record(
        self,
        state: SessionState,
        event_type: str,
        payload: dict[str, Any],
        sim_minute: float | None = None,
    ) -> ExecutionEvent:
        effective_minute = (
            state.scenario.current_minute
            if sim_minute is None
            else sim_minute
        )
        from .replay_world import with_world_evidence
        payload = with_world_evidence(state, payload, effective_minute)
        buffer = self._event_buffers.get(state.id)
        if buffer is not None:
            event = ExecutionEvent(
                sequence=state.sequence + 1,
                session_id=state.id,
                sim_minute=effective_minute,
                type=event_type,
                payload=payload,
            )
            state.sequence = event.sequence
            buffer.append(event)
            return event
        event = self.store.append_event(
            state.id,
            effective_minute,
            event_type,
            payload,
        )
        state.sequence = event.sequence
        state.decision_lab["event_integrity"] = (
            self.store.verify_event_chain(state.id)
        )
        return event

    async def create(self, request: SessionCreate) -> SessionState:
        scenario = build_scenario(request)
        state = SessionState(
            mode=request.mode,
            data_mode=request.data_mode,
            scenario=scenario,
            phase=Phase.incident if scenario.events else Phase.normal,
            data_badge={
                DataMode.synthetic: "合成数据 · 固定种子",
                DataMode.live: "正在获取实时辅助数据",
                DataMode.imported: "导入回放数据",
            }[request.data_mode],
        )
        self.store.save_session(state)
        if request.data_mode == DataMode.live:
            snapshot = await fetch_live_snapshot(self.store, self.config)
            state.data_snapshot = snapshot
            state.data_badge = live_data_badge(snapshot)
            if snapshot.get("metar"):
                state.scenario = apply_weather_snapshot(state.scenario, snapshot)
            self._record(state, EventType.data_snapshot.value, {
                "badge": state.data_badge,
                "aircraft_count": len(snapshot.get("aircraft", [])),
                "warnings": snapshot.get("warnings", []),
            })
        else:
            self._record(state, "session_created", {
                "mode": request.mode.value,
                "scenario_id": state.scenario.id,
                "seed": request.seed,
            })
        self.store.save_session(state)
        return state

    async def clone(self, session_id: str) -> SessionState:
        source = self.require(session_id)
        cloned = SessionState(
            mode=source.mode,
            data_mode=source.data_mode,
            scenario=source.scenario.model_copy(deep=True),
            phase=source.phase,
            intent=source.intent.model_copy(deep=True) if source.intent else None,
            plans=[plan.model_copy(deep=True) for plan in source.plans],
            selected_plan_id=source.selected_plan_id,
            traces=[trace.model_copy(deep=True) for trace in source.traces],
            llm_tier=source.llm_tier,
            data_badge=source.data_badge,
            data_snapshot=source.data_snapshot.copy(),
            decision_lab=source.model_copy(deep=True).decision_lab,
        )
        self.store.save_session(cloned)
        self._record(cloned, "session_cloned", {"source_session_id": source.id})
        for plan in cloned.plans:
            self.store.save_plan(cloned.id, plan, approved=plan.id == cloned.selected_plan_id)
        self.store.save_session(cloned)
        return cloned

    def _build_outcome_audit(self, state: SessionState, plan: PlanCandidate, intent,
                             **kwargs) -> dict[str, Any]:
        events = self.store.replay(state.id)
        chain = self.store.verify_event_chain(state.id)
        return build_outcome_audit(
            state.scenario, plan, intent, execution_events=events,
            event_chain_verified=chain.get("valid", False), **kwargs)

    def _store_intent_draft(self, state: SessionState, intent: OperatorIntent, tier: str,
                            text: str) -> None:
        previous = state.intent
        if previous and not previous.needs_confirmation:
            state.decision_lab["intent_predecessor"] = {
                "intent_digest": intent_digest(previous),
                "version": previous.version,
                "snapshot": previous.model_dump(mode="json"),
            }
        else:
            snapshot = state.decision_lab.get("intent_predecessor", {}).get("snapshot")
            previous = OperatorIntent.model_validate(snapshot) if snapshot else None
        intent = merge_confirmed(intent, previous)
        intent.clarification_questions = clarification_questions(intent)
        intent.version = max(int(state.decision_lab.get("intent_version", 0)),
                             state.intent.version if state.intent else 0) + 1
        state.decision_lab["intent_version"] = intent.version
        state.intent = intent
        state.llm_tier = tier
        self._record(state, EventType.operator_command.value, {
            "text": text,
            "intent": intent.model_dump(mode="json"),
            "model_tier": tier,
            "confirmed": False,
        })
        self.store.save_session(state)

    async def submit_intent(self, session_id: str, request: IntentRequest) -> SessionState:
        async with self._lock(session_id):
            state = self.require(session_id)
            intent, tier = await parse_intent(
                request.text, state.scenario, source=request.source, config=self.config,
            )
            intent.original_text = request.text
            self._store_intent_draft(state, intent, tier, request.text)
        await self.streams.publish_state(state)
        return state

    async def clarify_intent(self, session_id: str, request: IntentClarificationRequest) -> SessionState:
        async with self._lock(session_id):
            state = self.require(session_id)
            text, turn = compose_clarification(state.intent, request)
            draft = state.intent
            # The UI discloses this original clock. Untouched "in ten minutes"
            # must not silently drift because answering a question took time.
            scenario = state.scenario.model_copy(update={"current_minute": draft.base_minute})
            intent, tier = await parse_intent(text, scenario, source="text", config=self.config)
            intent.original_text = draft.original_text or draft.raw_text
            intent.clarification_history = [*draft.clarification_history, turn]
            self._store_intent_draft(state, intent, tier, request.text)
        await self.streams.publish_state(state)
        return state

    async def confirm_intent(self, session_id: str, approved: bool) -> SessionState:
        async with self._lock(session_id):
            state = self.require(session_id)
            if state.intent is not None:
                if not state.intent.needs_confirmation:
                    return state
                if approved:
                    if state.intent.issues:
                        raise ValueError("请先澄清指令：" + "；".join(item.message for item in state.intent.issues))
                    prior = state.decision_lab.get("intent_predecessor", {}).get("snapshot")
                    old_actions = prior.get("scheduled_constraints", []) if prior else []
                    for item in state.intent.scheduled_constraints:
                        if (item.model_dump(mode="json") not in old_actions and item.minute is not None
                                and item.minute < state.scenario.current_minute):
                            raise ValueError("指令时刻已过去，请基于当前仿真时刻重新提交。")
                    known_resources = {item.id for item in [*state.scenario.runways, *state.scenario.gates]}
                    known_flights = {item.id for item in state.scenario.flights}
                    for requirement in state.intent.scheduled_constraints:
                        known = known_flights if requirement.kind == "priority" else known_resources
                        if requirement.kind != "delay_cap" and requirement.resource_id not in known:
                            raise ValueError("指令对象不在当前场景：" + str(requirement.resource_id))
                    state.intent.needs_confirmation = False
                    state.intent.confirmed_minute = state.scenario.current_minute
                    predecessor = state.decision_lab.pop("intent_predecessor", None)
                    if predecessor:
                        self._record(state, "intent_superseded",
                                     {key: value for key, value in predecessor.items() if key != "snapshot"})
                    self._record(state, "intent_confirmed", {
                        "intent_digest": intent_digest(confirmed_intent(state)),
                        "intent_version": state.intent.version,
                        "summary": state.intent.summary,
                        "constraints": [
                            item.model_dump(mode="json")
                            for item in state.intent.scheduled_constraints
                        ],
                    })
                else:
                    self._record(state, "intent_rejected", {"summary": state.intent.summary})
                    predecessor = state.decision_lab.pop("intent_predecessor", {})
                    snapshot = predecessor.get("snapshot")
                    state.intent = OperatorIntent.model_validate(snapshot) if snapshot else None
            self.store.save_session(state)
        await self.streams.publish_state(state)
        return state

    async def submit_preference(
        self,
        session_id: str,
        request: PreferenceRequest,
    ) -> SessionState:
        """Update a soft-objective posterior without touching safe schedules."""
        async with self._lock(session_id):
            state = self.require(session_id)
            if state.phase != Phase.awaiting_approval:
                raise ValueError("偏好学习只能在候选方案等待审批时进行")
            if request.left_plan_id == request.right_plan_id:
                raise ValueError("成对比较必须包含两个不同候选")
            plan_ids = {plan.id for plan in state.plans if plan.feasible}
            pair = {request.left_plan_id, request.right_plan_id}
            if not pair.issubset(plan_ids):
                raise ValueError("只能比较当前已通过硬约束的候选")
            if request.preferred_plan_id not in pair:
                raise ValueError("偏好结果必须是所比较的两个候选之一")

            previous = state.decision_lab.get("preference_learning", {})
            history = list(previous.get("comparisons", []))
            pair_key = frozenset(pair)
            if any(
                frozenset((
                    str(item.get("left_plan_id")),
                    str(item.get("right_plan_id")),
                )) == pair_key
                for item in history
            ):
                raise ValueError("这一对候选已经比较过")
            history.append({
                "left_plan_id": request.left_plan_id,
                "right_plan_id": request.right_plan_id,
                "preferred_plan_id": request.preferred_plan_id,
            })
            observation = encode_preference_observation(
                state.plans,
                left_plan_id=request.left_plan_id,
                right_plan_id=request.right_plan_id,
                preferred_plan_id=request.preferred_plan_id,
            )
            observation_id = self.store.save_preference_observation(
                session_id=state.id,
                scenario_id=state.scenario.id,
                winner_vector=observation["winner_vector"],
                loser_vector=observation["loser_vector"],
                winner_title=observation["winner_title"],
                loser_title=observation["loser_title"],
                metadata={
                    "objective_order": observation["objective_order"],
                    "winner_plan_id": observation["winner_plan_id"],
                    "loser_plan_id": observation["loser_plan_id"],
                },
            )
            result = learn_operator_preferences(
                state.scenario,
                state.plans,
                confirmed_intent(state),
                history,
                historical_observations=self.store.list_preference_observations(
                    exclude_session_id=state.id,
                ),
            )
            result["latest_observation_id"] = observation_id
            state.decision_lab["preference_learning"] = result
            state.traces = [
                trace
                for trace in state.traces
                if trace.agent != "Preference Elicitation Agent"
            ]
            state.traces.append(_preference_trace(result))
            self._record(state, "preference_feedback", {
                "left_plan_id": request.left_plan_id,
                "right_plan_id": request.right_plan_id,
                "preferred_plan_id": request.preferred_plan_id,
                "observation_id": observation_id,
                "comparison_count": result["comparison_count"],
                "historical_comparison_count": result[
                    "historical_comparison_count"
                ],
                "memory_scope": result["memory_scope"],
                "recommended_id": result["recommended_id"],
                "posterior_weights": {
                    row["id"]: row["mean"]
                    for row in result["posterior"]["weights"]
                },
                "hard_safety_rules_changed": 0,
            })
            self.store.save_session(state)
        await self.streams.publish_state(state)
        return state

    async def acquire_observation(
        self,
        session_id: str,
        request: ObservationRequest,
    ) -> SessionState:
        """Acquire one decision-relevant observation before re-solving."""
        async with self._lock(session_id):
            state = self.require(session_id)
            if state.phase != Phase.awaiting_approval:
                raise ValueError("主动观测只能在候选方案等待审批时执行")

            information = state.decision_lab.get("information_value", {})
            query = next(
                (
                    item
                    for item in information.get("queries", [])
                    if item.get("id") == request.query_id
                ),
                None,
            )
            if query is None:
                raise ValueError("当前会话没有该主动观测建议")

            reviewed_plan = next(
                (plan for plan in state.plans if plan.id == request.source_plan_id), None
            )
            if request.source_plan_id is not None and reviewed_plan is None:
                raise ValueError("查询前查看的候选已失效，请刷新后重试")
            review_context = None
            if reviewed_plan is not None:
                gate = state.decision_lab.get("approval_gates", {}).get(reviewed_plan.id, {})
                review_context = {
                    "plan_id": reviewed_plan.id,
                    "profile": reviewed_plan.profile.value,
                    "title": reviewed_plan.title,
                    "gate_status": gate.get("status", "unknown"),
                    "gate_summary": gate.get("summary", ""),
                    "required_evidence": [
                        dict(item) for item in gate.get("required_queries", [])
                        if item.get("query_id") == request.query_id
                    ],
                }

            resolved = list(state.data_snapshot.get("resolved_observation_ids", []))
            if request.query_id in resolved:
                return state

            evidence: dict[str, Any] = {
                "review_context": review_context,
                "query_id": request.query_id,
                "target": query.get("target"),
                "source": query.get("source"),
                "acquired_minute": state.scenario.current_minute,
                "synthetic": True,
                "session_data_mode": state.data_mode.value,
                "provenance": "synthetic exercise observation; no live runway/AODB interface",
                "valid": True,
                "evidence_sequence": state.sequence + 1,
                "trusted_state_digest": str(
                    state.decision_lab.get(
                        "trusted_state", {}
                    ).get("digest", "")
                ),
            }
            if request.query_id == "weather_reopen_nowcast":
                runway = next(
                    (
                        item
                        for item in state.scenario.runways
                        if item.id == query.get("target")
                    ),
                    None,
                )
                old_std = state.scenario.weather.closure_std_min
                new_std = max(.8, round(old_std * .35, 1))
                reveal_delta = state.scenario.seed % 5 - 2
                old_reopen = runway.reopen_minute if runway else None
                observed_reopen = (
                    max(
                        state.scenario.current_minute + 1,
                        int(old_reopen) + reveal_delta,
                    )
                    if old_reopen is not None
                    else None
                )
                if runway is not None and observed_reopen is not None:
                    runway.reopen_minute = observed_reopen
                    runway.reason = "主动观测：跑道检查更新时间"
                    state.scenario.weather.closure_mean_min = float(observed_reopen)
                state.scenario.weather.closure_std_min = new_std
                state.scenario.weather.source = "synthetic active nowcast"
                evidence["before"] = {
                    "reopen_minute": old_reopen,
                    "closure_std_min": old_std,
                }
                evidence["observed"] = {
                    "reopen_minute": observed_reopen,
                    "closure_std_min": new_std,
                }
            else:
                gate = next(
                    (
                        item
                        for item in state.scenario.gates
                        if item.id == query.get("target")
                    ),
                    None,
                )
                if gate is None:
                    raise ValueError("关键机位不在当前场景中")
                old_available = gate.available_minute
                reveal_delta = (state.scenario.seed + sum(map(ord, gate.id))) % 5 - 2
                observed_available = max(
                    state.scenario.current_minute,
                    old_available + reveal_delta,
                )
                gate.available_minute = observed_available
                evidence["before"] = {"available_minute": old_available}
                evidence["observed"] = {
                    "available_minute": observed_available,
                    "status": gate.status.value,
                }

            resolved.append(request.query_id)
            state.data_snapshot["resolved_observation_ids"] = sorted(set(resolved))
            observations = list(state.data_snapshot.get("active_observations", []))
            observations.append(evidence)
            state.data_snapshot["active_observations"] = observations
            information["status"] = "observation_acquired"
            information["last_observation"] = evidence
            information["resolved_query_ids"] = state.data_snapshot[
                "resolved_observation_ids"
            ]
            state.decision_lab["information_value"] = information
            state.plans = []
            state.selected_plan_id = None
            state.phase = Phase.incident
            self._record(state, "active_observation_acquired", evidence)
            self.store.save_session(state)

        await self.streams.publish_state(state)
        return state

    def _apply_event(self, state: SessionState, event: EventSpec) -> None:
        scenario = state.scenario
        advance_to = max(scenario.current_minute, event.minute)
        elapsed = advance_to - scenario.current_minute
        if elapsed > 0:
            fuel_updates: list[dict[str, int | str]] = []
            expired_flight_ids: list[str] = []
            for flight in scenario.flights:
                if flight.state not in {
                    FlightState.inbound,
                    FlightState.approach,
                    FlightState.holding,
                }:
                    continue
                before = flight.fuel_margin_min
                after = max(0, before - elapsed)
                flight.fuel_margin_min = after
                if before < elapsed:
                    flight.state = FlightState.diverted
                    expired_flight_ids.append(flight.id)
                fuel_updates.append({
                    "flight_id": flight.id,
                    "before_min": before,
                    "after_min": after,
                    "absolute_deadline_min": scenario.current_minute + before,
                    "status": "diverted" if before < elapsed else "preserved",
                })
            event.payload["fuel_clock"] = {
                "from_minute": scenario.current_minute,
                "to_minute": advance_to,
                "elapsed_min": elapsed,
                "updated_flights": len(fuel_updates),
                "minimum_remaining_min": min(
                    (int(item["after_min"]) for item in fuel_updates),
                    default=None,
                ),
                "critical_flight_ids": [
                    str(item["flight_id"])
                    for item in fuel_updates
                    if int(item["after_min"]) <= 8
                ],
                "expired_flight_ids": expired_flight_ids,
                "deadline_invariant": "deadlines preserved; overdue flights diverted",
                "updates": fuel_updates,
            }
        if event.type == EventType.runway_closed:
            for runway in scenario.runways:
                if runway.id == event.resource_id:
                    runway.status = ResourceStatus.closed
                    runway.reopen_minute = event.minute + max(1, event.duration_min)
                    runway.reason = str(event.payload.get("reason", "动态关闭"))
        elif event.type == EventType.runway_reopened:
            for runway in scenario.runways:
                if runway.id == event.resource_id:
                    runway.status = ResourceStatus.open
                    runway.reopen_minute = None
                    runway.reason = None
        elif event.type == EventType.gate_outage:
            for gate in scenario.gates:
                if gate.id == event.resource_id:
                    gate.status = ResourceStatus.blocked
                    gate.available_minute = event.minute + max(1, event.duration_min)
        elif event.type == EventType.ground_vehicle_failure:
            affected_gates = {str(value) for value in event.payload.get("affected_gates", [])}
            unavailable_until = event.minute + max(1, event.duration_min)
            for gate in scenario.gates:
                if gate.id in affected_gates:
                    gate.available_minute = max(gate.available_minute, unavailable_until)
        elif event.type == EventType.medical_diversion:
            flight_id = event.resource_id or str(event.payload.get("flight_id", "MED901"))
            if not any(flight.id == flight_id for flight in scenario.flights):
                scenario.flights.append(FlightSpec(
                    id=flight_id,
                    aircraft_type=str(event.payload.get("aircraft_type", "A320")),
                    state=FlightState.approach,
                    scheduled_arrival=event.minute,
                    planned_gate=str(event.payload.get("gate", "A3")),
                    connections=0,
                    fuel_margin_min=int(event.payload.get("fuel_margin_min", 20)),
                    medical_priority=True,
                    origin="DIVERT",
                ))
        elif event.type == EventType.storm:
            scenario.weather.severity = event.severity
            scenario.weather.label = "动态强雷暴" if event.severity >= 4 else "动态降雨"
            scenario.weather.closure_mean_min = max(
                scenario.weather.closure_mean_min, event.duration_min
            )
        scenario.events.append(event)
        scenario.current_minute = max(scenario.current_minute, event.minute)

    async def _automatic_policy_replan(
        self,
        state: SessionState,
        selected: PlanCandidate,
        event: EventSpec,
        policy_decision: dict[str, Any],
        trusted_state: dict[str, Any],
    ) -> ExecutionEvent:
        """Run one event-triggered rolling-horizon solve inside an approved envelope."""
        if trusted_state["status"] != "ready":
            evidence_failure = _trusted_state_infeasibility(trusted_state)
            state.phase = Phase.infeasible
            state.selected_plan_id = None
            state.traces = _trusted_state_traces(trusted_state)
            state.decision_lab["trusted_state"] = trusted_state
            state.decision_lab["infeasible"] = evidence_failure
            return self._record(
                state,
                "automatic_policy_replan_failed",
                {
                    "trigger": event.id,
                    "policy_decision": policy_decision,
                    "solver_status": evidence_failure["solver_status"],
                    "solver_calls": 0,
                    "unresolved_fact_keys": trusted_state["argumentation"]["unresolved_keys"],
                    "switch_executed": False,
                    "safety_gate": "evidence_abstention",
                },
                event.minute,
            )
        prepared_hints = _prepared_hint_assignments(policy_decision)
        state.decision_lab.setdefault("policy_runtime", {})[
            "warm_start_flights"
        ] = len(prepared_hints)
        impact = analyze_recourse_impact(state.scenario, selected, event)
        if policy_decision["action"] == "causal_local_repair":
            frozen = frozen_assignments(selected, impact)
        else:
            pending = [
                assignment
                for assignment in selected.assignments
                if assignment.landing_minute > event.minute
            ]
            impact.update({
                "strategy": (
                    "priority_hard_replan"
                    if policy_decision["action"] == "priority_replan"
                    else "global_rolling_replan"
                ),
                "affected_flight_ids": [item.flight_id for item in pending],
                "preserved_flight_ids": [],
                "affected_count": len(pending),
                "frozen_count": 0,
                "pending_count": len(pending),
                "preservation_rate": 0,
                "boundary_proof": (
                    "容量或硬优先级假设已经改变，全部未落地航班重新开放给 CP-SAT"
                ),
            })
            frozen = []

        committed_ids = {
            operation.flight_id
            for operation in (
                state.execution.committed_operations
                if state.execution is not None
                else []
            )
        }
        for assignment in selected.assignments:
            flight = next(
                (
                    item
                    for item in state.scenario.flights
                    if item.id == assignment.flight_id
                ),
                None,
            )
            if flight is None or flight.id in committed_ids:
                continue
            flight.state = FlightState.holding
        state.phase = Phase.replanning
        solver_scenario = apply_trusted_operational_state(
            state.scenario, trusted_state
        )
        trusted_traces = _trusted_state_traces(trusted_state)
        replanned = await asyncio.to_thread(
            run_recovery_workflow,
            solver_scenario,
            [
                PlanProfile.balanced,
                PlanProfile.passenger,
                PlanProfile.resilient,
                PlanProfile.green,
            ],
            confirmed_intent(state),
            50,
            frozen,
            list(state.data_snapshot.get("resolved_observation_ids", [])),
            hint_assignments=prepared_hints,
        )
        new_plans = replanned.get("candidates", [])
        basis = (plan_evidence_basis(trusted_state) | {
                "operator_intent_digest": intent_digest(confirmed_intent(state)),
            })
        for candidate in new_plans:
            candidate.evidence_basis = dict(basis)
        state.traces = [
            *trusted_traces,
            *replanned.get("traces", []),
        ]
        if not new_plans:
            state.phase = Phase.infeasible
            failure = replanned.get("failure")
            state.decision_lab["infeasible"] = (
                failure.model_dump(mode="json")
                if failure
                else {
                    "reasons": ["策略触发后的滚动规划没有安全可行解"],
                    "relaxations": ["转入人工处置或执行安全备降"],
                }
            )
            return self._record(
                state,
                "automatic_policy_replan_failed",
                {
                    "trigger": event.id,
                    "policy_decision": policy_decision,
                    "safety_gate": "stopped",
                },
                event.minute,
            )

        shadow_twin = await asyncio.to_thread(
            evaluate_shadow_switch,
            state.scenario,
            selected,
            new_plans[0],
            confirmed_intent(state),
            32,
        )
        state.decision_lab["shadow_twin"] = shadow_twin
        state.decision_lab.setdefault("policy_runtime", {})[
            "shadow_decision"
        ] = shadow_twin["decision"]
        if shadow_twin["decision"] == "safe_stop":
            state.phase = Phase.infeasible
            state.traces.append(_shadow_twin_trace(shadow_twin))
            return self._record(
                state,
                "automatic_policy_replan_failed",
                {
                    "trigger": event.id,
                    "policy_decision": policy_decision,
                    "shadow_twin": shadow_twin,
                    "switch_executed": False,
                    "safety_gate": "stopped",
                },
                event.minute,
            )
        if shadow_twin["decision"] == "keep":
            state.phase = Phase.monitoring
            state.selected_plan_id = selected.id
            state.decision_lab["recourse_preview"] = summarize_repair(
                selected, new_plans[0], impact
            )
            state.traces.append(_shadow_twin_trace(shadow_twin))
            return self._record(
                state,
                "automatic_policy_replan",
                {
                    "trigger": event.id,
                    "selected_plan_id": selected.id,
                    "summary": shadow_twin["reason"],
                    "strategy": "shadow_hold",
                    "policy_decision": policy_decision,
                    "shadow_twin": shadow_twin,
                    "switch_executed": False,
                    "safety_gate": "passed",
                },
                event.minute,
            )

        plan_certificates = _issue_plan_certificates(
            new_plans,
            trusted_state,
            state.sequence,
        )
        proof_verification = verify_plan_certificate(
            plan_certificates[new_plans[0].id],
            new_plans[0],
            trusted_state,
        )
        proof_blocks = not proof_verification["valid"]
        twin_trust = assess_twin_trust(new_plans[0])
        twin_blocks = bool(twin_trust["blocks_unattended_execution"])
        shield_blocks = proof_blocks or twin_blocks or bool(
            new_plans[0].risk_shield.get("blocks_unattended_execution", False)
        )

        state.plans = new_plans
        replan_arbitration_by_plan = replanned.get(
            "arbitration_by_plan", {}
        )
        approval_gates = self._compile_approval_gates(
            state,
            trusted_state,
            replan_arbitration_by_plan,
        )
        first_gate = approval_gates.get(new_plans[0].id, {})
        shield_blocks = shield_blocks or first_gate.get("status") != "ready"
        state.selected_plan_id = None if shield_blocks else new_plans[0].id
        recourse_by_plan = {
            plan.id: summarize_repair(selected, plan, impact)
            for plan in new_plans
        }
        action_diff_by_plan = {
            plan.id: {
                **_build_action_diff(selected, plan),
                "committed_count_at_replan": (
                    len(state.execution.committed_operations)
                    if state.execution is not None else None
                ),
                "replan_minute": state.scenario.current_minute,
            }
            for plan in new_plans
        }
        impact = recourse_by_plan[new_plans[0].id]
        action_diff = action_diff_by_plan[new_plans[0].id]
        (
            robustness,
            regret,
            falsification_by_plan,
            causal_attribution,
            sensitivity,
        ) = await asyncio.gather(
            asyncio.to_thread(
                evaluate_adaptive_horizon,
                state.scenario,
                new_plans[0],
                confirmed_intent(state),
                12,
            ),
            asyncio.to_thread(
                evaluate_candidate_regret,
                state.scenario,
                new_plans,
                confirmed_intent(state),
                6,
            ),
            _falsify_candidates(
                state.scenario,
                new_plans,
                confirmed_intent(state),
            ),
            asyncio.to_thread(
                attribute_recovery_burden,
                state.scenario,
                new_plans[0],
                confirmed_intent(state),
                8,
            ),
            asyncio.to_thread(
                self._sensitivity,
                state,
                new_plans[0],
            ),
        )
        information = recommend_observation(
            state.scenario,
            new_plans[0],
            robustness,
            regret,
            list(state.data_snapshot.get("resolved_observation_ids", [])),
        )
        information = prioritize_required_evidence(
            information,
            approval_gates.get(new_plans[0].id),
        )
        bargaining = nash_bargaining(new_plans)
        preference_learning = learn_operator_preferences(
            state.scenario,
            new_plans,
            confirmed_intent(state),
            historical_observations=self.store.list_preference_observations(),
        )
        mitigation_by_plan = {
            plan.id: optimize_intervention_portfolio(
                state.scenario,
                plan,
                confirmed_intent(state),
            )
            for plan in new_plans
        }
        effective_plans = [
            apply_intervention_outcome(plan, mitigation_by_plan[plan.id])
            for plan in new_plans
        ]
        effective_by_id = {plan.id: plan for plan in effective_plans}
        passenger_rescue_by_plan = {
            plan.id: optimize_passenger_rescue(
                state.scenario,
                effective_by_id[plan.id],
            )
            for plan in new_plans
        }
        contract_repair_by_plan = {
            plan.id: analyze_contract_repair(
                state.scenario,
                effective_by_id[plan.id],
                effective_plans,
                confirmed_intent(state),
            )
            for plan in new_plans
        }
        next_policy = compile_contingency_policy(
            state.scenario,
            new_plans[0],
            robustness,
            regret,
        )
        next_policy["approved"] = not shield_blocks
        next_policy["authorization"] = (
            "human-required-proof-verification"
            if proof_blocks
            else "human-required-twin-drift"
            if twin_blocks
            else "human-required-conformal-escalation"
            if shield_blocks
            else "automatic-safe-recourse-envelope"
        )
        state.decision_lab.update({
            "agent_arbitration": replanned.get("arbitration", {}),
            "agent_arbitration_by_plan": replan_arbitration_by_plan,
            "approval_gate": approval_gates.get(
                new_plans[0].id, {}
            ),
            "approval_gates": approval_gates,
            "agent_proposals": [
                item.model_dump(mode="json")
                for item in replanned.get("proposals", [])
            ],
            "counterfactuals": counterfactual_summary(
                state.scenario,
                new_plans[0],
            ),
            "action_diff": action_diff,
            "action_diff_by_plan": action_diff_by_plan,
            "recourse_by_plan": recourse_by_plan,
            "sensitivity": sensitivity,
            "robustness": robustness,
            "regret": regret,
            "information_value": information,
            "bargaining": bargaining,
            "preference_learning": preference_learning,
            "pareto": pareto_summary(new_plans),
            "plan_certificate": plan_certificates[new_plans[0].id],
            "plan_certificates": plan_certificates,
            "proof_carrying_verification": proof_verification,
            "risk_shield": new_plans[0].risk_shield,
            "risk_shield_by_plan": {
                plan.id: plan.risk_shield for plan in new_plans
            },
            "twin_trust": twin_trust,
            "twin_trust_by_plan": {
                plan.id: assess_twin_trust(plan) for plan in new_plans
            },
            "falsification": falsification_by_plan[new_plans[0].id],
            "falsification_by_plan": falsification_by_plan,
            "causal_attribution": causal_attribution,
            "passenger_rescue": passenger_rescue_by_plan[new_plans[0].id],
            "passenger_rescue_by_plan": passenger_rescue_by_plan,
            "contract_repair": contract_repair_by_plan[new_plans[0].id],
            "contract_repair_by_plan": contract_repair_by_plan,
            "mitigation": mitigation_by_plan[new_plans[0].id],
            "mitigation_by_plan": mitigation_by_plan,
            "policy": next_policy,
            "recourse": impact,
            "outcome_audit": self._build_outcome_audit(
                state,
                effective_by_id[new_plans[0].id],
                confirmed_intent(state),
                stage="monitoring" if not shield_blocks else "human_review",
                baseline=selected,
            ),
            "acdm": build_acdm_ledger(
                state.scenario, new_plans[0], committed=not shield_blocks
            ),
            "acdm_by_plan": build_acdm_ledgers(
                state.scenario,
                new_plans,
                committed_plan_id=None if shield_blocks else new_plans[0].id,
            ),
        })
        state.traces.extend([
            AgentTrace(
                agent="Runtime Assurance Agent",
                role="策略包络运行时裁决",
                status="completed",
                summary=(
                    "携证计划绑定校验失败，已停止无人值守执行"
                    if proof_blocks
                    else "Twin Trust Sentinel 检测到数字孪生失配，已停止无人值守执行"
                    if twin_blocks
                    else "Conformal Risk Shield 要求人工确认，已停止无人值守执行"
                    if shield_blocks
                    else str(policy_decision["reason"])
                ),
                tool="ECA-match + rolling-horizon-controller",
                confidence=.97,
            ),
            AgentTrace(
                agent="Causal Impact Agent",
                role="稳定性保护重规划",
                status="completed",
                summary=str(impact["summary"]),
                tool="networkx-scope + frozen-boundary CP-SAT",
                confidence=.95,
            ),
            AgentTrace(
                agent="Safety Critic",
                role="重规划后独立复核",
                status="completed",
                summary="滚动方案通过跑道、机位、燃油与计划层约束复核；服务结果另行验收",
                tool="constraint-proof-validator",
                confidence=.99,
            ),
        ])
        state.traces.append(_proof_certificate_trace(
            plan_certificates[new_plans[0].id]
        ))
        state.traces.append(_uncertainty_trace(
            new_plans[0], state.decision_lab["acdm"]
        ))
        state.traces.append(_information_trace(information))
        state.traces.append(_bargaining_trace(bargaining))
        state.traces.append(_preference_trace(preference_learning))
        state.traces.append(_shadow_twin_trace(shadow_twin))
        state.traces.append(_twin_trust_trace(twin_trust))
        state.traces.append(_causal_attribution_trace(causal_attribution))
        state.traces.append(_passenger_rescue_trace(passenger_rescue_by_plan[new_plans[0].id]))
        state.traces.append(_contract_repair_trace(contract_repair_by_plan[new_plans[0].id]))
        state.traces.append(_mitigation_trace(mitigation_by_plan[new_plans[0].id]))
        state.traces.append(_falsification_trace(falsification_by_plan[new_plans[0].id]))
        for candidate in new_plans:
            self.store.save_plan(
                state.id,
                candidate,
                approved=candidate.rank == 1 and not shield_blocks,
            )
        state.phase = Phase.awaiting_approval if shield_blocks else Phase.monitoring
        return self._record(
            state,
            (
                "automatic_policy_replan_requires_approval"
                if shield_blocks
                else "automatic_policy_replan"
            ),
            {
                "trigger": event.id,
                "selected_plan_id": state.selected_plan_id,
                "bargaining_recommended_id": bargaining.get("recommended_id"),
                "summary": str(impact["summary"]),
                "strategy": impact["strategy"],
                "recourse": impact,
                "action_diff": action_diff,
                "policy_decision": policy_decision,
                "risk_shield": new_plans[0].risk_shield["status"],
                "twin_trust": twin_trust["status"],
                "proof_carrying_plan": {
                    "certificate_id": proof_verification["certificate_id"],
                    "verified": proof_verification["valid"],
                    "failed_checks": proof_verification["failed_checks"],
                },
                "causal_attribution": {
                    "status": causal_attribution["status"],
                    "top_factor_id": causal_attribution["top_factor_id"],
                    "risk_burden_points": causal_attribution["total_burden"]["risk_points"],
                },
                "passenger_rescue": {
                    "reaccommodated": passenger_rescue_by_plan[new_plans[0].id]["reaccommodated_passengers"],
                    "remaining_stranded": passenger_rescue_by_plan[new_plans[0].id]["remaining_stranded"],
                    "status": passenger_rescue_by_plan[new_plans[0].id]["status"],
                },
                "contract_repair": {
                    "status": contract_repair_by_plan[new_plans[0].id]["status"],
                    "recommended_plan_id": contract_repair_by_plan[new_plans[0].id]["recommended_plan_id"],
                    "unmet_target_ids": contract_repair_by_plan[new_plans[0].id]["unmet_target_ids"],
                    "hard_safety_relaxations": 0,
                },
                "shadow_twin": shadow_twin,
                "switch_executed": not shield_blocks,
                "safety_gate": "human_required" if shield_blocks else "passed",
            },
            event.minute,
        )

    async def inject_event(self, session_id: str, request: EventRequest) -> SessionState:
        async with self._lock(session_id):
            state = self.require(session_id)
            was_operating = state.phase in {
                Phase.executing,
                Phase.monitoring,
                Phase.recovered,
            }
            if (
                request.event.type == EventType.gate_outage
                and request.event.resource_id
            ):
                request.event.payload.setdefault(
                    "affected_gates",
                    [request.event.resource_id],
                )
            elif (
                request.event.type == EventType.ground_vehicle_failure
                and not request.event.payload.get("affected_gates")
            ):
                known_gates = {gate.id for gate in state.scenario.gates}
                defaults = [
                    gate_id
                    for gate_id in ("A3", "A4", "R1")
                    if gate_id in known_gates
                ]
                request.event.payload["affected_gates"] = (
                    defaults
                    or [gate.id for gate in state.scenario.gates[:2]]
                )
                request.event.payload.setdefault("zone", "Taxiway C")

            if request.event.minute < state.scenario.current_minute:
                raise StaleExecution(
                    "事件生效时间不能早于当前模拟时间"
                )

            cursor = state.execution
            if cursor is not None:
                if cursor.status == "completed":
                    raise UnsafeExecution("本轮恢复已经完成")
                if cursor.status == "blocked":
                    raise UnsafeExecution("执行已被安全闸门阻断")
                expected_revision = (
                    cursor.revision
                    if request.expected_revision is None
                    else request.expected_revision
                )
                receipt_key = (
                    "event:"
                    + (request.request_id or request.event.id)
                )
                existing = cursor.request_receipts.get(receipt_key)
                if existing is not None:
                    if (
                        existing.target_minute != request.event.minute
                        or existing.expected_revision != expected_revision
                    ):
                        raise StaleExecution(
                            "request_id 已用于不同的事件注入"
                        )
                    return state
                if expected_revision != cursor.revision:
                    raise StaleExecution(
                        f"执行版本已更新：期望 {expected_revision}，"
                        f"当前 {cursor.revision}"
                    )
                if any(
                    item.id == request.event.id
                    for item in cursor.scheduled_events
                ) or request.event.id in cursor.processed_event_ids:
                    raise StaleExecution(
                        f"事件 ID 已存在：{request.event.id}"
                    )

                base_sequence = state.sequence
                self._event_buffers[state.id] = []
                try:
                    cursor.scheduled_events.append(
                        request.event.model_copy(deep=True)
                    )
                    cursor.scheduled_events.sort(
                        key=lambda item: (item.minute, item.id)
                    )
                    self._record(
                        state,
                        "scheduled_event",
                        {
                            "event_id": request.event.id,
                            "event_type": request.event.type.value,
                            "effective_minute": request.event.minute,
                            "resource_id": request.event.resource_id,
                            "status": "queued",
                        },
                        state.scenario.current_minute,
                    )
                    stopped_reason = None
                    if (
                        request.event.minute
                        == state.scenario.current_minute
                    ):
                        _, next_minute, stopped_reason = await advance_state(
                            self,
                            state,
                            ExecutionAdvanceRequest(
                                target_minute=request.event.minute,
                                expected_revision=cursor.revision,
                                request_id=receipt_key,
                            ),
                        )
                    else:
                        next_minute = next_execution_minute(
                            state.scenario,
                            cursor,
                        )
                    emitted = list(self._event_buffers[state.id])
                    cursor.revision += 1
                    cursor.request_receipts[receipt_key] = AdvanceReceipt(
                        request_id=receipt_key,
                        target_minute=request.event.minute,
                        expected_revision=expected_revision,
                        resulting_revision=cursor.revision,
                        resulting_minute=state.scenario.current_minute,
                        event_sequences=[
                            event.sequence for event in emitted
                        ],
                        next_event_minute=next_minute,
                        stopped_reason=stopped_reason,
                    )
                    self.store.commit_execution_batch(
                        state,
                        emitted,
                        expected_previous_sequence=base_sequence,
                    )
                finally:
                    self._event_buffers.pop(state.id, None)
                state.decision_lab["event_integrity"] = (
                    self.store.verify_event_chain(state.id)
                )
                for emitted_event in emitted:
                    await self.streams.publish(
                        session_id,
                        "execution.event",
                        emitted_event.model_dump(mode="json"),
                    )
                await self.streams.publish_state(state)
                return state

            if request.event.minute > state.scenario.current_minute:
                scheduled = list(
                    state.data_snapshot.get("scheduled_events", [])
                )
                if not any(
                    str(item.get("id")) == request.event.id
                    for item in scheduled
                    if isinstance(item, dict)
                ):
                    scheduled.append(
                        request.event.model_dump(mode="json")
                    )
                    state.data_snapshot["scheduled_events"] = scheduled
                    queued_event = self._record(
                        state,
                        "scheduled_event",
                        {
                            "event_id": request.event.id,
                            "event_type": request.event.type.value,
                            "effective_minute": request.event.minute,
                            "resource_id": request.event.resource_id,
                            "status": "queued_before_approval",
                        },
                        state.scenario.current_minute,
                    )
                    self.store.save_session(state)
                    await self.streams.publish(
                        session_id,
                        "execution.event",
                        queued_event.model_dump(mode="json"),
                    )
                    await self.streams.publish_state(state)
                return state

            selected = next(
                (
                    plan
                    for plan in state.plans
                    if plan.id == state.selected_plan_id
                ),
                None,
            )
            policy_decision = evaluate_policy_event(
                state.decision_lab.get("policy"),
                request.event,
            )
            request.event.payload["policy_decision"] = policy_decision
            state.decision_lab["policy_runtime"] = {
                "event_id": request.event.id,
                **policy_decision,
            }
            previous_trusted_state = state.decision_lab.get(
                "trusted_state"
            ) or build_trusted_operational_state(
                state.scenario, state.data_mode
            )
            self._invalidate_observations_for_event(state, request.event)
            if was_operating and selected is not None:
                flight_by_id = {
                    flight.id: flight for flight in state.scenario.flights
                }
                for assignment in selected.assignments:
                    flight = flight_by_id.get(assignment.flight_id)
                    if flight is None:
                        continue
                    if assignment.landing_minute <= request.event.minute:
                        flight.state = FlightState.landed
                    elif flight.state in {
                        FlightState.inbound,
                        FlightState.approach,
                        FlightState.holding,
                    }:
                        flight.state = FlightState.holding
            self._apply_event(state, request.event)
            if was_operating:
                state.phase = (
                    Phase.monitoring
                    if policy_decision["action"] == "continue_and_monitor"
                    else Phase.replanning
                )
            else:
                state.phase = Phase.incident
            emitted = [self._record(
                state,
                request.event.type.value,
                request.event.model_dump(mode="json"),
                request.event.minute,
            )]
            trusted_state = build_trusted_operational_state(
                state.scenario,
                state.data_mode,
                previous=previous_trusted_state,
                event=request.event,
            )
            state.decision_lab["trusted_state"] = trusted_state
            emitted.append(self._record(
                state,
                "trusted_state_reconciled",
                {
                    "trigger": request.event.id,
                    "digest": trusted_state["digest"],
                    "changed_facts": len(trusted_state["changed_facts"]),
                    "invalidated_constraints": len(
                        trusted_state["invalidated_constraints"]
                    ),
                    "summary": trusted_state["summary"],
                },
                request.event.minute,
            ))
            if (
                was_operating
                and selected is not None
                and policy_decision["matched"]
                and policy_decision.get("approved_envelope")
            ):
                emitted.append(await self._automatic_policy_replan(
                    state,
                    selected,
                    request.event,
                    policy_decision,
                    trusted_state,
                ))
            if was_operating:
                runtime_contract = verify_runtime_contract(
                    self.store.replay(state.id),
                    target_phase=state.phase.value,
                    scenario=state.scenario,
                )
                state.decision_lab["runtime_contract"] = runtime_contract
                if state.decision_lab.get("outcome_audit"):
                    state.decision_lab["outcome_audit"]["runtime_contract"] = (
                        runtime_contract
                    )
            self.store.save_session(state)
        for event in emitted:
            await self.streams.publish(
                session_id,
                "execution.event",
                event.model_dump(mode="json"),
            )
        await self.streams.publish_state(state)
        return state

    async def update_sandbox(self, session_id: str, update: SandboxUpdate) -> SessionState:
        async with self._lock(session_id):
            state = self.require(session_id)
            if state.mode.value != "sandbox":
                raise ValueError("只有开放沙盘会话可以直接调整机场状态")
            if update.weather_severity is not None:
                state.scenario.weather.severity = update.weather_severity
                state.scenario.weather.label = f"沙盘天气等级 {update.weather_severity}"
            if update.runway_closure_min is not None:
                runway = state.scenario.runways[-1]
                if update.runway_closure_min == 0:
                    runway.status = ResourceStatus.open
                    runway.reopen_minute = None
                else:
                    runway.status = ResourceStatus.closed
                    runway.reopen_minute = state.scenario.current_minute + update.runway_closure_min
                    runway.reason = "沙盘动态关闭"
            if update.passenger_multiplier is not None:
                for flight in state.scenario.flights:
                    flight.connections = min(600, round(flight.connections * update.passenger_multiplier))
            if update.fuel_margin_delta is not None:
                for flight in state.scenario.flights:
                    flight.fuel_margin_min = max(8, min(180, flight.fuel_margin_min + update.fuel_margin_delta))
            if update.priority_flight_id:
                for flight in state.scenario.flights:
                    flight.medical_priority = flight.id == update.priority_flight_id
            if update.weights is not None:
                state.intent = OperatorIntent(
                    raw_text="开放沙盘目标权重",
                    weights=update.weights.normalized(),
                    summary="使用沙盘面板设置的目标权重",
                    needs_confirmation=False,
                )
            state.phase = Phase.incident
            self._record(state, "sandbox_updated", update.model_dump(mode="json", exclude_none=True))
            self.store.save_session(state)
        await self.streams.publish_state(state)
        return state

    def _sensitivity(self, state: SessionState, recommended: PlanCandidate) -> list[dict[str, Any]]:
        """Re-optimize each runway future; never claim sensitivity from a stale schedule."""
        rows: list[dict[str, Any]] = []
        closed = next(
            (runway for runway in state.scenario.runways if runway.reopen_minute is not None),
            None,
        )
        if closed is None:
            return rows
        for delta, label in ((-5, "提前 5 分钟开放"), (5, "延后 5 分钟开放")):
            scenario = state.scenario.model_copy(deep=True)
            runway = next(item for item in scenario.runways if item.id == closed.id)
            runway.reopen_minute = max(
                scenario.current_minute,
                int(runway.reopen_minute or 0) + delta,
            )
            candidate, failure = solve_candidate(
                scenario,
                recommended.profile,
                confirmed_intent(state),
            )
            if candidate is None:
                rows.append({
                    "label": label,
                    "status": "infeasible",
                    "mean_delay": None,
                    "p90_delay": None,
                    "missed_connections": None,
                    "reason": failure.reasons[0] if failure and failure.reasons else "无可行解",
                })
                continue
            report = simulate_candidate(scenario, candidate, runs=20)
            rows.append({
                "label": label,
                "status": "reoptimized",
                "mean_delay": report.mean_delay_min,
                "p90_delay": report.p90_delay_min,
                "missed_connections": report.missed_connections,
            })
        return rows

    async def plan(self, session_id: str, request: PlanRequest) -> SessionState:
        async with self._lock(session_id):
            state = self.require(session_id)
            if state.intent and state.intent.needs_confirmation:
                raise ValueError("结构化指挥意图尚未确认")
            state.phase = Phase.replanning if request.replan else Phase.analyzing
            state.traces = []
            self.store.save_session(state)
            await self.streams.publish_state(state)
            previous_trusted_state = state.decision_lab.get("trusted_state")
            trusted_state = build_trusted_operational_state(
                state.scenario,
                state.data_mode,
                previous=previous_trusted_state,
            )
            self._record(state, "trusted_state_compiled", {
                "status": trusted_state["status"],
                "digest": trusted_state["digest"],
                "quality": trusted_state["quality"],
                "summary": trusted_state["summary"],
            })
            if trusted_state["status"] != "ready":
                evidence_failure = _trusted_state_infeasibility(trusted_state)
                state.phase = Phase.infeasible
                state.plans = []
                state.selected_plan_id = None
                state.traces = _trusted_state_traces(trusted_state)
                state.decision_lab = {
                    "trusted_state": trusted_state,
                    "infeasible": evidence_failure,
                }
                self._record(state, "planning_infeasible", {
                    "solver_status": evidence_failure["solver_status"],
                    "unresolved_fact_keys": trusted_state["argumentation"]["unresolved_keys"],
                    "hard_safety_relaxations_allowed": 0,
                    "solver_calls": 0,
                })
                self.store.save_session(state)
                await self.streams.publish_state(state)
                return state
            solver_scenario = apply_trusted_operational_state(
                state.scenario, trusted_state
            )
            result = await asyncio.to_thread(
                run_recovery_workflow,
                solver_scenario,
                request.profiles,
                confirmed_intent(state),
                50,
                None,
                list(state.data_snapshot.get("resolved_observation_ids", [])),
            )
            state.traces = [
                *_trusted_state_traces(trusted_state),
                *result.get("traces", []),
            ]
            state.plans = result.get("candidates", [])
            basis = (plan_evidence_basis(trusted_state) | {
                "operator_intent_digest": intent_digest(confirmed_intent(state)),
            })
            for candidate in state.plans:
                candidate.evidence_basis = dict(basis)
            plan_certificates = _issue_plan_certificates(
                state.plans,
                trusted_state,
                state.sequence,
            )
            failure = result.get("failure")
            if not state.plans:
                state.phase = Phase.infeasible
                state.decision_lab = {
                    "trusted_state": trusted_state,
                    "infeasible": failure.model_dump(mode="json") if failure else {},
                }
                self._record(state, "planning_infeasible", {
                    "solver_status": failure.solver_status if failure else "unknown",
                    "core_irreducible": failure.core_irreducible if failure else False,
                    "conflict_core_ids": (
                        [item.id for item in failure.conflict_core]
                        if failure
                        else []
                    ),
                    "hard_safety_relaxations_allowed": 0,
                })
            else:
                arbitration_by_plan = result.get(
                    "arbitration_by_plan", {}
                )
                approval_gates = self._compile_approval_gates(
                    state,
                    trusted_state,
                    arbitration_by_plan,
                )
                candidate_summaries = [
                    {
                        "id": plan.id,
                        "title": plan.title,
                        "score": plan.score,
                        "report": plan.report.model_dump(mode="json"),
                    }
                    for plan in state.plans
                ]
                (
                    explanation_bundle,
                    sensitivity,
                    robustness,
                    regret,
                    falsification_by_plan,
                    causal_attribution,
                    operational_baseline,
                ) = await asyncio.gather(
                    explain_recommendation(
                        state.scenario,
                        candidate_summaries,
                        deep=request.deep,
                        config=self.config,
                    ),
                    asyncio.to_thread(
                        self._sensitivity,
                        state,
                        state.plans[0],
                    ),
                    asyncio.to_thread(
                        evaluate_adaptive_horizon,
                        state.scenario,
                        state.plans[0],
                        confirmed_intent(state),
                        16,
                    ),
                    asyncio.to_thread(
                        evaluate_candidate_regret,
                        state.scenario,
                        state.plans,
                        confirmed_intent(state),
                        8,
                    ),
                    _falsify_candidates(
                        state.scenario,
                        state.plans,
                        confirmed_intent(state),
                    ),
                    asyncio.to_thread(
                        attribute_recovery_burden,
                        state.scenario,
                        state.plans[0],
                        confirmed_intent(state),
                        8,
                    ),
                    asyncio.to_thread(
                        compare_operational_baselines,
                        solver_scenario,
                        trusted_state,
                        state.plans[0],
                        confirmed_intent(state),
                        runs=12,
                    ),
                )
                explanation, tier = explanation_bundle
                state.llm_tier = tier
                state.plans[0].explanation = explanation.recommendation + "；" + "；".join(explanation.why)
                state.plans[0].why_not = list(dict.fromkeys([*state.plans[0].why_not, *explanation.why_not]))[:3]
                information = recommend_observation(
                    state.scenario,
                    state.plans[0],
                    robustness,
                    regret,
                    list(state.data_snapshot.get("resolved_observation_ids", [])),
                )
                information = prioritize_required_evidence(
                    information,
                    approval_gates.get(state.plans[0].id),
                )
                bargaining = nash_bargaining(state.plans)
                preference_learning = learn_operator_preferences(
                    state.scenario,
                    state.plans,
                    confirmed_intent(state),
                    historical_observations=self.store.list_preference_observations(),
                )
                twin_trust_by_plan = {
                    plan.id: assess_twin_trust(plan) for plan in state.plans
                }
                mitigation_by_plan = {
                    plan.id: optimize_intervention_portfolio(
                        state.scenario,
                        plan,
                        confirmed_intent(state),
                    )
                    for plan in state.plans
                }
                effective_plans = [
                    apply_intervention_outcome(plan, mitigation_by_plan[plan.id])
                    for plan in state.plans
                ]
                effective_by_id = {plan.id: plan for plan in effective_plans}
                passenger_rescue_by_plan = {
                    plan.id: optimize_passenger_rescue(
                        state.scenario,
                        effective_by_id[plan.id],
                    )
                    for plan in state.plans
                }
                contract_repair_by_plan = {
                    plan.id: analyze_contract_repair(
                        state.scenario,
                        effective_by_id[plan.id],
                        effective_plans,
                        confirmed_intent(state),
                    )
                    for plan in state.plans
                }
                policy = compile_contingency_policy(
                    state.scenario,
                    state.plans[0],
                    robustness,
                    regret,
                )
                state.decision_lab = {
                    "agent_arbitration": result.get("arbitration", {}),
                    "agent_arbitration_by_plan": arbitration_by_plan,
                    "approval_gate": approval_gates.get(
                        state.plans[0].id, {}
                    ),
                    "approval_gates": approval_gates,
                    "agent_proposals": [
                        item.model_dump(mode="json")
                        for item in result.get("proposals", [])
                    ],
                    "counterfactuals": counterfactual_summary(state.scenario, state.plans[0]),
                    "operational_baseline": operational_baseline,
                    "sensitivity": sensitivity,
                    "robustness": robustness,
                    "regret": regret,
                    "information_value": information,
                    "bargaining": bargaining,
                    "preference_learning": preference_learning,
                    "policy": policy,
                    "pareto": pareto_summary(state.plans),
                    "risk_shield": state.plans[0].risk_shield,
                    "risk_shield_by_plan": {
                        plan.id: plan.risk_shield for plan in state.plans
                    },
                    "twin_trust": twin_trust_by_plan[state.plans[0].id],
                    "twin_trust_by_plan": twin_trust_by_plan,
                    "falsification": falsification_by_plan[state.plans[0].id],
                    "falsification_by_plan": falsification_by_plan,
                    "causal_attribution": causal_attribution,
                    "passenger_rescue": passenger_rescue_by_plan[state.plans[0].id],
                    "passenger_rescue_by_plan": passenger_rescue_by_plan,
                    "contract_repair": contract_repair_by_plan[state.plans[0].id],
                    "contract_repair_by_plan": contract_repair_by_plan,
                    "mitigation": mitigation_by_plan[state.plans[0].id],
                    "mitigation_by_plan": mitigation_by_plan,
                    "trusted_state": trusted_state,
                    "plan_certificate": plan_certificates[state.plans[0].id],
                    "plan_certificates": plan_certificates,
                    "acdm": build_acdm_ledger(state.scenario, state.plans[0]),
                    "acdm_by_plan": build_acdm_ledgers(state.scenario, state.plans),
                    "explanation": explanation.model_dump(mode="json"),
                }
                state.traces.append(_operational_baseline_trace(operational_baseline))
                state.traces.append(AgentTrace(
                    agent="Robustness Agent",
                    role="跨情景稳健决策",
                    status="completed",
                    summary=f"{robustness['summary']}；{regret['summary']}",
                    tool="stress-worlds + minimax-regret + cp-sat-recourse",
                    confidence=.92,
                ))
                state.traces.append(_falsification_trace(falsification_by_plan[state.plans[0].id]))
                state.traces.append(AgentTrace(
                    agent="Policy Synthesizer",
                    role="事件驱动恢复策略",
                    status="completed",
                    summary=str(policy["summary"]),
                    tool="scenario-tree + ECA-policy + runtime-assurance",
                    confidence=.94,
                ))
                state.traces.append(AgentTrace(
                    agent="A-CDM Data Steward",
                    role="里程碑与数据血缘",
                    status="completed",
                    summary=(
                        f"{len(state.plans[0].assignments)} 架航班已建立输入、"
                        "求解输出与推导时间契约；TSAT 明确标记为未接入"
                    ),
                    tool="deterministic milestone-ledger + lineage-validator",
                    confidence=.99,
                ))
                state.traces.append(_uncertainty_trace(state.plans[0], state.decision_lab["acdm"]))
                state.traces.append(_information_trace(information))
                state.traces.append(_bargaining_trace(bargaining))
                state.traces.append(_preference_trace(preference_learning))
                state.traces.append(_twin_trust_trace(twin_trust_by_plan[state.plans[0].id]))
                state.traces.append(_causal_attribution_trace(causal_attribution))
                state.traces.append(_passenger_rescue_trace(passenger_rescue_by_plan[state.plans[0].id]))
                state.traces.append(_contract_repair_trace(contract_repair_by_plan[state.plans[0].id]))
                state.traces.append(_mitigation_trace(mitigation_by_plan[state.plans[0].id]))
                state.traces.append(_proof_certificate_trace(
                    plan_certificates[state.plans[0].id]
                ))
                state.phase = Phase.awaiting_approval
                for candidate in state.plans:
                    self.store.save_plan(state.id, candidate)
                self._record(state, EventType.plan_generated.value, {
                    "plan_ids": [item.id for item in state.plans],
                    "recommended_id": state.plans[0].id,
                    "bargaining_recommended_id": bargaining.get("recommended_id"),
                    "policy_id": policy["id"],
                    "deep": request.deep,
                    "replan": request.replan,
                    "proof_carrying_plan": {
                        "certificate_id": plan_certificates[state.plans[0].id]["certificate_id"],
                        "status": plan_certificates[state.plans[0].id]["status"],
                    },
                })
            self.store.save_session(state)
        await self.streams.publish_state(state)
        return state

    async def explain_flight(
        self,
        session_id: str,
        plan_id: str,
        flight_id: str,
    ) -> dict[str, Any]:
        async with self._lock(session_id):
            state = self.require(session_id)
            plan = next((item for item in state.plans if item.id == plan_id), None)
            if plan is None:
                raise KeyError(plan_id)
            if not any(item.flight_id == flight_id for item in plan.assignments):
                raise KeyError(flight_id)
            cache = state.decision_lab.setdefault("flight_contrasts", {})
            cache_key = f"{plan_id}:{flight_id}:seq-{state.sequence}"
            if cache_key in cache:
                return cache[cache_key]
            result = await asyncio.to_thread(
                build_flight_contrastive_explanation,
                state.scenario.model_copy(deep=True),
                plan.model_copy(deep=True),
                flight_id,
                confirmed_intent(state).model_copy(deep=True) if confirmed_intent(state) else None,
            )
            cache[cache_key] = result
            self.store.save_session(state)
            return result

    async def advance_execution(
        self,
        session_id: str,
        request: ExecutionAdvanceRequest,
    ) -> ExecutionAdvanceResponse:
        duplicate = False
        async with self._lock(session_id):
            state = self.require(session_id)
            cursor = state.execution
            if cursor is None:
                raise UnsafeExecution("会话尚未批准执行计划")
            existing = cursor.request_receipts.get(request.request_id)
            if existing is not None:
                if (
                    existing.target_minute != request.target_minute
                    or existing.expected_revision != request.expected_revision
                ):
                    raise StaleExecution(
                        "request_id 已用于不同的推进请求"
                    )
                wanted = set(existing.event_sequences)
                replay = (
                    self.store.replay(
                        state.id,
                        min(wanted) - 1,
                    )
                    if wanted
                    else []
                )
                emitted = [
                    event for event in replay if event.sequence in wanted
                ]
                response = ExecutionAdvanceResponse(
                    session=state,
                    execution_events=emitted,
                    next_event_minute=existing.next_event_minute,
                    stopped_reason=existing.stopped_reason,
                    revision=existing.resulting_revision,
                )
                duplicate = True
            else:
                if request.expected_revision != cursor.revision:
                    raise StaleExecution(
                        f"执行版本已更新：期望 {request.expected_revision}，"
                        f"当前 {cursor.revision}"
                    )
                if request.target_minute < state.scenario.current_minute:
                    raise StaleExecution(
                        "target_minute 不能早于当前模拟时间"
                    )
                if cursor.status == "blocked":
                    raise UnsafeExecution("执行已被安全闸门阻断")
                if cursor.status == "completed":
                    raise UnsafeExecution("本轮恢复已经完成")

                base_sequence = state.sequence
                self._event_buffers[state.id] = []
                try:
                    emitted, next_minute, stopped_reason = await advance_state(
                        self,
                        state,
                        request,
                    )
                    cursor.revision += 1
                    receipt = AdvanceReceipt(
                        request_id=request.request_id,
                        target_minute=request.target_minute,
                        expected_revision=request.expected_revision,
                        resulting_revision=cursor.revision,
                        resulting_minute=state.scenario.current_minute,
                        event_sequences=[
                            event.sequence for event in emitted
                        ],
                        next_event_minute=next_minute,
                        stopped_reason=stopped_reason,
                    )
                    cursor.request_receipts[request.request_id] = receipt
                    if len(cursor.request_receipts) > 64:
                        oldest = next(iter(cursor.request_receipts))
                        cursor.request_receipts.pop(oldest, None)
                    self.store.commit_execution_batch(
                        state,
                        emitted,
                        expected_previous_sequence=base_sequence,
                    )
                finally:
                    self._event_buffers.pop(state.id, None)
                state.decision_lab["event_integrity"] = (
                    self.store.verify_event_chain(state.id)
                )
                response = ExecutionAdvanceResponse(
                    session=state,
                    execution_events=emitted,
                    next_event_minute=next_minute,
                    stopped_reason=stopped_reason,
                    revision=cursor.revision,
                )

        if not duplicate:
            for event in response.execution_events:
                await self.streams.publish(
                    session_id,
                    "execution.event",
                    event.model_dump(mode="json"),
                )
            await self.streams.publish_state(response.session)
        return response

    async def approve(self, session_id: str, plan_id: str) -> ApprovalResponse:
        async with self._lock(session_id):
            state = self.require(session_id)
            if state.intent and state.intent.needs_confirmation:
                raise UnsafePlan("当前指令尚未澄清并确认，不能批准旧方案；请先确认或撤销草稿。")
            selected = next((plan for plan in state.plans if plan.id == plan_id), None)
            if selected is None:
                raise KeyError(plan_id)
            if state.execution is not None and state.phase != Phase.awaiting_approval:
                if (
                    state.execution.status in {"ready", "running"}
                    and state.execution.active_plan_id == plan_id
                ):
                    return ApprovalResponse(
                        session=state,
                        execution_events=[],
                    )
                raise StaleExecution(
                    "当前执行版本不接受新的计划批准；请刷新会话状态"
                )
            trusted_state = state.decision_lab.get("trusted_state")
            if not trusted_state:
                trusted_state = build_trusted_operational_state(
                    state.scenario,
                    state.data_mode,
                )
                state.decision_lab["trusted_state"] = trusted_state
            if not selected.evidence_basis:
                selected.evidence_basis = (plan_evidence_basis(trusted_state) | {
                "operator_intent_digest": intent_digest(confirmed_intent(state)),
            })
            approval_gates = self._compile_approval_gates(
                state,
                trusted_state,
            )
            try:
                approval_gate = require_human_approvable(
                    approval_gates.get(plan_id),
                    plan_id,
                )
            except ValueError as exc:
                self._record(state, "approval_gate_rejected", {
                    "plan_id": plan_id,
                    "reason": str(exc),
                    "gate": approval_gates.get(plan_id, {}),
                    "switch_executed": False,
                })
                self.store.save_session(state)
                raise UnsafePlan(str(exc)) from exc
            certificates = state.decision_lab.setdefault("plan_certificates", {})
            certificate = certificates.get(plan_id)
            certificate_backfilled = certificate is None
            if certificate is None and not selected.evidence_basis:
                selected.evidence_basis = (plan_evidence_basis(trusted_state) | {
                "operator_intent_digest": intent_digest(confirmed_intent(state)),
            })
            if certificate is None:
                certificate = issue_plan_certificate(
                    selected,
                    trusted_state,
                    issued_sequence=state.sequence,
                )
                certificates[plan_id] = certificate
            verification = verify_plan_certificate(
                certificate,
                selected,
                trusted_state,
            )
            verification["issued_on_approval"] = certificate_backfilled
            state.decision_lab["plan_certificate"] = certificate
            state.decision_lab["proof_carrying_verification"] = verification
            if not verification["valid"]:
                self._record(state, "plan_certificate_rejected", {
                    "plan_id": plan_id,
                    "certificate_id": verification["certificate_id"],
                    "failed_checks": verification["failed_checks"],
                    "switch_executed": False,
                })
                self.store.save_session(state)
                raise UnsafePlan("携证计划校验失败，已拒绝执行")
            certificate_event = self._record(state, "plan_certificate_verified", {
                "plan_id": plan_id,
                "certificate_id": verification["certificate_id"],
                "certificate_hash": verification["certificate_hash"],
                "checks_passed": len(verification["checks"]),
            })
            proof = validate_assignments(
                state.scenario,
                selected.assignments,
                confirmed_intent(state),
                turnaround_buffer_by_flight=(
                    effective_turnaround_buffers(selected)
                ),
            )
            if any(item.startswith("FAIL") for item in proof):
                raise UnsafePlan("候选未通过独立安全闸门")
            approval_gate["human_approved"] = True
            approval_gate["approved_minute"] = state.scenario.current_minute
            approval_gate["risk_acknowledged"] = (
                approval_gate["status"] == "human_required"
            )
            state.decision_lab["approval_gate"] = approval_gate
            state.decision_lab["approval_gates"][plan_id] = approval_gate
            # Promote only evidence computed for the actually approved candidate.
            # The original recommendation remains in its immutable replan event.
            state.decision_lab["agent_arbitration"] = (
                state.decision_lab.get("agent_arbitration_by_plan", {}).get(plan_id, {})
            )
            for evidence_key in ("action_diff", "recourse"):
                selected_evidence = state.decision_lab.get(
                    evidence_key + "_by_plan", {}
                ).get(plan_id)
                if selected_evidence is not None:
                    state.decision_lab[evidence_key] = selected_evidence
            policy = compile_contingency_policy(
                state.scenario,
                selected,
                state.decision_lab.get("robustness", {}),
                state.decision_lab.get("regret", {}),
            )
            policy["approved"] = True
            policy["authorization"] = "human-approved-safe-recourse-envelope"
            state.decision_lab["policy"] = policy
            selected_rescue = state.decision_lab.get(
                "passenger_rescue_by_plan", {}
            ).get(plan_id) or optimize_passenger_rescue(state.scenario, selected)
            state.decision_lab["passenger_rescue"] = selected_rescue
            selected_contract_repair = state.decision_lab.get(
                "contract_repair_by_plan", {}
            ).get(plan_id) or analyze_contract_repair(
                state.scenario,
                selected,
                state.plans,
                confirmed_intent(state),
            )
            state.decision_lab["contract_repair"] = selected_contract_repair
            selected_mitigation = state.decision_lab.get(
                "mitigation_by_plan", {}
            ).get(plan_id) or optimize_intervention_portfolio(
                state.scenario,
                selected,
                confirmed_intent(state),
            )
            selected_mitigation["approved"] = True
            selected_mitigation["approval_note"] = "干预资源组合与候选方案一并批准"
            state.decision_lab["mitigation"] = selected_mitigation
            state.decision_lab.setdefault("mitigation_by_plan", {})[plan_id] = (
                selected_mitigation
            )
            effective_selected = apply_intervention_outcome(
                selected,
                selected_mitigation,
            )
            state.decision_lab["outcome_audit"] = self._build_outcome_audit(
                state,
                effective_selected,
                confirmed_intent(state),
                stage="approved",
            )
            state.decision_lab["acdm"] = build_acdm_ledger(
                state.scenario, selected, committed=True
            )
            state.decision_lab.setdefault("acdm_by_plan", {})[plan_id] = (
                state.decision_lab["acdm"]
            )
            preference = state.decision_lab.get("preference_learning")
            preference_recommended_id = (
                preference.get("recommended_id") if preference else None
            )
            preference_override = bool(
                preference_recommended_id
                and preference_recommended_id != plan_id
            )
            if preference is not None:
                preference["approved_plan_id"] = plan_id
                preference["human_override"] = preference_override
                preference["approval_note"] = (
                    "操作员保留最终决定并覆盖偏好后验推荐"
                    if preference_override
                    else "操作员审批与偏好后验推荐一致"
                )
            state.selected_plan_id = plan_id
            state.phase = Phase.executing
            self.store.approve_plan(state.id, plan_id)
            emitted = [certificate_event, self._record(state, EventType.plan_approved.value, {
                "plan_id": plan_id,
                "profile": selected.profile.value,
                "policy_id": policy["id"],
                "preference_recommended_id": preference_recommended_id,
                "human_preference_override": preference_override,
                "proof_certificate_id": verification["certificate_id"],
                "proof_certificate_verified": verification["valid"],
                "approval_gate_status": approval_gate["status"],
                "approval_gate_digest": approval_gate["evidence_binding"][
                    "trusted_state_digest"
                ],
                "risk_acknowledged": approval_gate["risk_acknowledged"],
                "action_diff": state.decision_lab.get("action_diff_by_plan", {}).get(plan_id),
                "recourse": state.decision_lab.get("recourse_by_plan", {}).get(plan_id),
                "mitigation": {
                    "status": selected_mitigation["status"],
                    "action_ids": [
                        item["id"] for item in selected_mitigation["selected_actions"]
                    ],
                    "predicted_missed_after": selected_mitigation["mitigated"][
                        "predicted_missed"
                    ],
                    "tail_exposed_after": selected_mitigation["mitigated"][
                        "tail_risk_exposed"
                    ],
                    "assignments_unchanged": selected_mitigation["proof"][
                        "assignments_unchanged"
                    ],
                },
            })]
            if (
                state.execution is not None
                and state.execution.status != "completed"
            ):
                cursor = state.execution
                committed_ids = {
                    operation.flight_id
                    for operation in cursor.committed_operations
                }
                cursor.revision += 1
                cursor.active_plan_id = selected.id
                cursor.pending_assignments = sorted(
                    (
                        assignment.model_copy(deep=True)
                        for assignment in selected.assignments
                        if assignment.flight_id not in committed_ids
                    ),
                    key=lambda item: (
                        item.landing_minute,
                        item.flight_id,
                    ),
                )
                cursor.scope_flight_ids = list(dict.fromkeys([
                    *cursor.scope_flight_ids,
                    *[
                        item.flight_id
                        for item in cursor.pending_assignments
                    ],
                ]))
                cursor.status = "running"
                cursor.last_stopped_reason = None
            else:
                secondary = guided_secondary_event(state, selected)
                scheduled = [
                    EventSpec.model_validate(item)
                    for item in state.data_snapshot.get(
                        "scheduled_events", []
                    )
                ]
                if secondary is not None and all(
                    item.id != secondary.id for item in scheduled
                ):
                    scheduled.append(secondary)
                state.execution = create_execution_cursor(
                    state.scenario,
                    selected,
                    scheduled_events=scheduled,
                )
                initialize_connection(state)
                state.execution.status = "running"
                if secondary is not None:
                    emitted.append(self._record(
                        state,
                        "scheduled_event",
                        {
                            "event_id": secondary.id,
                            "event_type": secondary.type.value,
                            "effective_minute": secondary.minute,
                            "resource_id": secondary.resource_id,
                            "status": "queued",
                        },
                        state.scenario.current_minute,
                    ))
            state.decision_lab["execution_contract"] = {
                "status": "online",
                "clock_source": "scenario.current_minute",
                "cursor_version": state.execution.version,
                "revision": state.execution.revision,
                "pending_flights": len(
                    state.execution.pending_assignments
                ),
                "committed_operations": len(
                    state.execution.committed_operations
                ),
                "future_actions_emitted_on_approve": 0,
                "advance_endpoint": (
                    f"/api/v2/sessions/{state.id}/execution/advance"
                ),
            }
            self.store.save_session(state)
            for event in emitted:
                await self.streams.publish(
                    session_id,
                    "execution.event",
                    event.model_dump(mode="json"),
                )
            await self.streams.publish_state(state)
            return ApprovalResponse(
                session=state,
                execution_events=emitted,
            )

            execution_ledger: list[tuple[FlightAssignment, str, str]] = [
                (assignment, selected.id, "approved")
                for assignment in selected.assignments
            ]

            # The guided story contains a one-time second failure and automatic safe replan.
            recovery_succeeded = True
            has_secondary = any(
                event.type == EventType.ground_vehicle_failure
                for event in state.scenario.events
            )
            if state.mode.value == "guided" and not has_secondary:
                failure_minute = state.scenario.current_minute + 9
                affected_gates = ["A3", "A4", "R1"]
                next_pending_gate = next((
                    assignment.gate
                    for assignment in selected.assignments
                    if assignment.landing_minute > failure_minute
                ), None)
                if next_pending_gate and next_pending_gate not in affected_gates:
                    affected_gates.append(next_pending_gate)
                secondary = EventSpec(
                    type=EventType.ground_vehicle_failure,
                    minute=failure_minute,
                    resource_id="TUG-3",
                    severity=3,
                    duration_min=14,
                    payload={
                        "zone": "Taxiway C",
                        "affected_gates": affected_gates,
                        "story_beat": "secondary_failure",
                    },
                )
                policy_decision = evaluate_policy_event(
                    state.decision_lab.get("policy"),
                    secondary,
                )
                prepared_hints = _prepared_hint_assignments(policy_decision)
                secondary.payload["policy_decision"] = policy_decision
                state.decision_lab["policy_runtime"] = {
                    "event_id": secondary.id,
                    **policy_decision,
                    "warm_start_flights": len(prepared_hints),
                }
                impact = analyze_recourse_impact(state.scenario, selected, secondary)
                frozen = frozen_assignments(selected, impact)
                for assignment in selected.assignments:
                    flight = next(f for f in state.scenario.flights if f.id == assignment.flight_id)
                    flight.state = (
                        FlightState.landed
                        if assignment.landing_minute <= failure_minute
                        else FlightState.holding
                    )
                previous_trusted_state = state.decision_lab.get(
                    "trusted_state"
                ) or build_trusted_operational_state(
                    state.scenario, state.data_mode
                )
                self._apply_event(state, secondary)
                emitted.append(self._record(
                    state,
                    secondary.type.value,
                    secondary.model_dump(mode="json"),
                    failure_minute,
                ))
                trusted_state = build_trusted_operational_state(
                    state.scenario,
                    state.data_mode,
                    previous=previous_trusted_state,
                    event=secondary,
                )
                state.decision_lab["trusted_state"] = trusted_state
                emitted.append(self._record(
                    state,
                    "trusted_state_reconciled",
                    {
                        "trigger": secondary.id,
                        "digest": trusted_state["digest"],
                        "changed_facts": len(trusted_state["changed_facts"]),
                        "invalidated_constraints": len(
                            trusted_state["invalidated_constraints"]
                        ),
                        "summary": trusted_state["summary"],
                    },
                    failure_minute,
                ))
                state.phase = Phase.replanning
                trusted_traces = _trusted_state_traces(trusted_state)
                if trusted_state["status"] == "ready":
                    solver_scenario = apply_trusted_operational_state(
                        state.scenario, trusted_state
                    )
                    replanned = await asyncio.to_thread(
                        run_recovery_workflow,
                        solver_scenario,
                        [PlanProfile.balanced, PlanProfile.passenger, PlanProfile.resilient, PlanProfile.green],
                        confirmed_intent(state),
                        50,
                        frozen,
                        list(state.data_snapshot.get("resolved_observation_ids", [])),
                        hint_assignments=prepared_hints,
                    )
                else:
                    replanned = {
                        "candidates": [],
                        "failure": None,
                        "traces": [],
                    }
                new_plans = replanned.get("candidates", [])
                basis = (plan_evidence_basis(trusted_state) | {
                "operator_intent_digest": intent_digest(confirmed_intent(state)),
            })
                for candidate in new_plans:
                    candidate.evidence_basis = dict(basis)
                state.traces = [
                    *trusted_traces,
                    *replanned.get("traces", []),
                ]
                if new_plans:
                    post_plan_certificates = _issue_plan_certificates(
                        new_plans,
                        trusted_state,
                        state.sequence,
                    )
                    post_proof_verification = verify_plan_certificate(
                        post_plan_certificates[new_plans[0].id],
                        new_plans[0],
                        trusted_state,
                    )
                    proof_blocks = not post_proof_verification["valid"]
                    shadow_twin = await asyncio.to_thread(
                        evaluate_shadow_switch,
                        state.scenario,
                        selected,
                        new_plans[0],
                        confirmed_intent(state),
                        32,
                    )
                    state.decision_lab["shadow_twin"] = shadow_twin
                    shadow_blocks = shadow_twin["decision"] == "safe_stop"
                    post_twin_trust = assess_twin_trust(new_plans[0])
                    twin_blocks = bool(post_twin_trust["blocks_unattended_execution"])
                    shield_blocks = proof_blocks or shadow_blocks or twin_blocks or bool(
                        new_plans[0].risk_shield.get(
                            "blocks_unattended_execution", False
                        )
                    )
                    state.plans = new_plans
                    impact = summarize_repair(selected, new_plans[0], impact)
                    state.decision_lab["recourse"] = impact
                    state.decision_lab["counterfactuals"] = counterfactual_summary(
                        state.scenario, new_plans[0]
                    )
                    (
                        post_sensitivity,
                        post_robustness,
                        post_regret,
                        post_falsification_by_plan,
                        post_attribution,
                    ) = await asyncio.gather(
                        asyncio.to_thread(
                            self._sensitivity,
                            state,
                            new_plans[0],
                        ),
                        asyncio.to_thread(
                            evaluate_adaptive_horizon,
                            state.scenario,
                            new_plans[0],
                            confirmed_intent(state),
                            16,
                        ),
                        asyncio.to_thread(
                            evaluate_candidate_regret,
                            state.scenario,
                            state.plans,
                            confirmed_intent(state),
                            8,
                        ),
                        _falsify_candidates(
                            state.scenario,
                            new_plans,
                            confirmed_intent(state),
                        ),
                        asyncio.to_thread(
                            attribute_recovery_burden,
                            state.scenario,
                            new_plans[0],
                            confirmed_intent(state),
                            8,
                        ),
                    )
                    state.decision_lab["sensitivity"] = post_sensitivity
                    state.decision_lab["robustness"] = post_robustness
                    post_information = recommend_observation(
                        state.scenario,
                        new_plans[0],
                        post_robustness,
                        post_regret,
                        list(state.data_snapshot.get("resolved_observation_ids", [])),
                    )
                    post_bargaining = nash_bargaining(state.plans)
                    post_preference_learning = learn_operator_preferences(
                        state.scenario,
                        state.plans,
                        confirmed_intent(state),
                        historical_observations=self.store.list_preference_observations(),
                    )
                    post_mitigation_by_plan = {
                        plan.id: optimize_intervention_portfolio(
                            state.scenario,
                            plan,
                            confirmed_intent(state),
                        )
                        for plan in new_plans
                    }
                    post_effective_plans = [
                        apply_intervention_outcome(
                            plan,
                            post_mitigation_by_plan[plan.id],
                        )
                        for plan in new_plans
                    ]
                    post_effective_by_id = {
                        plan.id: plan for plan in post_effective_plans
                    }
                    post_rescue_by_plan = {
                        plan.id: optimize_passenger_rescue(
                            state.scenario,
                            post_effective_by_id[plan.id],
                        )
                        for plan in new_plans
                    }
                    post_contract_repair_by_plan = {
                        plan.id: analyze_contract_repair(
                            state.scenario,
                            post_effective_by_id[plan.id],
                            post_effective_plans,
                            confirmed_intent(state),
                        )
                        for plan in new_plans
                    }
                    post_policy = compile_contingency_policy(
                        state.scenario,
                        new_plans[0],
                        state.decision_lab["robustness"],
                        post_regret,
                    )
                    post_policy["approved"] = not shield_blocks
                    post_policy["authorization"] = (
                        "human-required-proof-verification"
                        if proof_blocks
                        else "human-required-shadow-safe-stop"
                        if shadow_blocks
                        else "human-required-twin-drift"
                        if twin_blocks
                        else "human-required-conformal-escalation"
                        if shield_blocks
                        else "automatic-safe-recourse-envelope"
                    )
                    state.decision_lab["agent_arbitration"] = replanned.get("arbitration", {})
                    state.decision_lab["agent_proposals"] = [
                        item.model_dump(mode="json")
                        for item in replanned.get("proposals", [])
                    ]
                    state.decision_lab["regret"] = post_regret
                    state.decision_lab["information_value"] = post_information
                    state.decision_lab["bargaining"] = post_bargaining
                    state.decision_lab["preference_learning"] = post_preference_learning
                    state.decision_lab["causal_attribution"] = post_attribution
                    state.decision_lab["passenger_rescue"] = post_rescue_by_plan[new_plans[0].id]
                    state.decision_lab["passenger_rescue_by_plan"] = post_rescue_by_plan
                    state.decision_lab["contract_repair"] = post_contract_repair_by_plan[new_plans[0].id]
                    state.decision_lab["contract_repair_by_plan"] = post_contract_repair_by_plan
                    state.decision_lab["mitigation"] = post_mitigation_by_plan[new_plans[0].id]
                    state.decision_lab["mitigation_by_plan"] = post_mitigation_by_plan
                    state.decision_lab["policy"] = post_policy
                    state.decision_lab["pareto"] = pareto_summary(state.plans)
                    state.decision_lab["plan_certificate"] = post_plan_certificates[new_plans[0].id]
                    state.decision_lab["plan_certificates"] = post_plan_certificates
                    state.decision_lab["proof_carrying_verification"] = post_proof_verification
                    state.decision_lab["outcome_audit"] = self._build_outcome_audit(
                        state,
                        post_effective_by_id[new_plans[0].id],
                        confirmed_intent(state),
                        stage="automatic_replan" if not shield_blocks else "human_review",
                        baseline=selected,
                    )
                    state.decision_lab["risk_shield"] = new_plans[0].risk_shield
                    state.decision_lab["risk_shield_by_plan"] = {
                        plan.id: plan.risk_shield for plan in new_plans
                    }
                    state.decision_lab["twin_trust"] = post_twin_trust
                    state.decision_lab["twin_trust_by_plan"] = {
                        plan.id: assess_twin_trust(plan) for plan in new_plans
                    }
                    state.decision_lab["falsification"] = post_falsification_by_plan[new_plans[0].id]
                    state.decision_lab["falsification_by_plan"] = post_falsification_by_plan
                    state.decision_lab["acdm"] = build_acdm_ledger(
                        state.scenario,
                        new_plans[0],
                        committed=not shield_blocks,
                    )
                    state.decision_lab["acdm_by_plan"] = build_acdm_ledgers(
                        state.scenario,
                        new_plans,
                        committed_plan_id=None if shield_blocks else new_plans[0].id,
                    )
                    state.traces.append(AgentTrace(
                        agent="Causal Impact Agent",
                        role="因果局部修复",
                        status="completed",
                        summary=str(impact["summary"]),
                        tool="networkx-causal-scope + cp-sat-boundary",
                        confidence=.95,
                    ))
                    state.traces.append(AgentTrace(
                        agent="Runtime Assurance Agent",
                        role="事件触发安全护栏",
                        status="completed",
                        summary=(
                            "携证计划绑定校验失败，已停止并转人工处置"
                            if proof_blocks
                            else "Shadow Twin 无法证明安全切换，已停止并转人工处置"
                            if shadow_blocks
                            else "Twin Trust Sentinel 检测到数字孪生失配，已停止无人值守执行"
                            if twin_blocks
                            else "Conformal Risk Shield 要求人工确认，已停止无人值守执行"
                            if shield_blocks
                            else str(policy_decision["reason"])
                        ),
                        tool="ECA-policy-match + mandatory-safety-gate",
                        confidence=.97,
                    ))
                    state.traces.append(AgentTrace(
                        agent="Robustness Agent",
                        role="恢复后压力复核",
                        status="completed",
                        summary=f"{state.decision_lab['robustness']['summary']}；{post_regret['summary']}",
                        tool="stress-worlds + minimax-regret + cp-sat-recourse",
                        confidence=.92,
                    ))
                    state.traces.append(_falsification_trace(post_falsification_by_plan[new_plans[0].id]))
                    state.traces.append(_uncertainty_trace(
                        new_plans[0], state.decision_lab["acdm"]
                    ))
                    state.traces.append(_information_trace(post_information))
                    state.traces.append(_bargaining_trace(post_bargaining))
                    state.traces.append(_preference_trace(post_preference_learning))
                    state.traces.append(_shadow_twin_trace(shadow_twin))
                    state.traces.append(_twin_trust_trace(post_twin_trust))
                    state.traces.append(_causal_attribution_trace(post_attribution))
                    state.traces.append(_passenger_rescue_trace(post_rescue_by_plan[new_plans[0].id]))
                    state.traces.append(_contract_repair_trace(post_contract_repair_by_plan[new_plans[0].id]))
                    state.traces.append(_mitigation_trace(post_mitigation_by_plan[new_plans[0].id]))
                    state.traces.append(_proof_certificate_trace(
                        post_plan_certificates[new_plans[0].id]
                    ))
                    state.selected_plan_id = (
                        None if shield_blocks else new_plans[0].id
                    )
                    if shield_blocks:
                        recovery_succeeded = False
                        state.phase = Phase.awaiting_approval
                    executed_prefix = [
                        assignment
                        for assignment in selected.assignments
                        if assignment.landing_minute <= failure_minute
                    ]
                    superseded_future_count = sum(
                        assignment.landing_minute > failure_minute
                        for assignment in selected.assignments
                    )
                    execution_ledger = [
                        (assignment, selected.id, "approved")
                        for assignment in executed_prefix
                    ]
                    if not shield_blocks:
                        execution_ledger.extend(
                            (assignment, new_plans[0].id, "automatic_replan")
                            for assignment in new_plans[0].assignments
                            if assignment.landing_minute > failure_minute
                        )

                    for candidate in new_plans:
                        self.store.save_plan(
                            state.id,
                            candidate,
                            approved=candidate.rank == 1 and not shield_blocks,
                        )
                    emitted.append(self._record(
                        state,
                        (
                            "automatic_replan_requires_approval"
                            if shield_blocks
                            else "automatic_replan"
                        ),
                        {
                            "trigger": secondary.id,
                            "selected_plan_id": state.selected_plan_id,
                            "bargaining_recommended_id": post_bargaining.get("recommended_id"),
                            "summary": str(impact["summary"]),
                            "recourse": impact,
                            "policy_decision": policy_decision,
                            "schedule_switch": {
                                "superseded_plan_id": selected.id,
                                "active_plan_id": state.selected_plan_id,
                                "executed_prefix": len(executed_prefix),
                                "superseded_future_actions": superseded_future_count,
                                "replacement_future_actions": sum(
                                    revision == "automatic_replan"
                                    for _, _, revision in execution_ledger
                                ),
                                "status": "switched" if not shield_blocks else "held_for_approval",
                            },
                            "risk_shield": new_plans[0].risk_shield["status"],
                            "twin_trust": post_twin_trust["status"],
                            "proof_carrying_plan": {
                                "certificate_id": post_proof_verification["certificate_id"],
                                "verified": post_proof_verification["valid"],
                                "failed_checks": post_proof_verification["failed_checks"],
                            },
                            "causal_attribution": {
                                "status": post_attribution["status"],
                                "top_factor_id": post_attribution["top_factor_id"],
                                "risk_burden_points": post_attribution["total_burden"]["risk_points"],
                            },
                            "passenger_rescue": {
                                "reaccommodated": post_rescue_by_plan[new_plans[0].id]["reaccommodated_passengers"],
                                "remaining_stranded": post_rescue_by_plan[new_plans[0].id]["remaining_stranded"],
                                "status": post_rescue_by_plan[new_plans[0].id]["status"],
                            },
                            "contract_repair": {
                                "status": post_contract_repair_by_plan[new_plans[0].id]["status"],
                                "recommended_plan_id": post_contract_repair_by_plan[new_plans[0].id]["recommended_plan_id"],
                                "unmet_target_ids": post_contract_repair_by_plan[new_plans[0].id]["unmet_target_ids"],
                                "hard_safety_relaxations": 0,
                            },
                            "shadow_twin": shadow_twin,
                            "switch_executed": not shield_blocks,
                            "safety_gate": (
                                "human_required" if shield_blocks else "passed"
                            ),
                        },
                        failure_minute + 1,
                    ))
                else:
                    recovery_succeeded = False
                    state.phase = Phase.infeasible
                    execution_ledger = [
                        (assignment, selected.id, "approved")
                        for assignment in selected.assignments
                        if assignment.landing_minute <= failure_minute
                    ]
                    failure = replanned.get("failure")
                    evidence_blocked = trusted_state["status"] != "ready"
                    state.decision_lab["infeasible"] = (
                        _trusted_state_infeasibility(trusted_state)
                        if evidence_blocked
                        else failure.model_dump(mode="json")
                        if failure
                        else {
                            "reasons": ["局部修复没有可行解"],
                            "relaxations": ["转入人工处置或安全备降"],
                        }
                    )
                    emitted.append(self._record(state, "automatic_replan_failed", {
                        "trigger": secondary.id,
                        "summary": (
                            "关键证据未决，求解器未运行并已转入人工确认"
                            if evidence_blocked
                            else "局部修复不可行，已停止自动执行并转入人工处置"
                        ),
                        "solver_status": (
                            "NOT_RUN_EVIDENCE_BLOCKED"
                            if evidence_blocked
                            else getattr(failure, "solver_status", "unknown")
                        ),
                        "solver_calls": (
                            0 if evidence_blocked
                            else getattr(failure, "solver_calls", 0)
                        ),
                        "switch_executed": False,
                        "safety_gate": (
                            "evidence_abstention"
                            if evidence_blocked
                            else "stopped"
                        ),
                    }, failure_minute + 1))
            for runway in state.scenario.runways:
                if runway.status == ResourceStatus.closed and runway.reopen_minute is not None:
                    assigned_landings = [
                        assignment.landing_minute
                        for assignment, _, _ in execution_ledger
                        if assignment.runway == runway.id
                    ]
                    emitted.append(self._record(
                        state,
                        EventType.runway_reopened.value,
                        {
                            "resource_id": runway.id,
                            "status": "open",
                            "reason": runway.reason or "scheduled inspection complete",
                            "reopen_minute": runway.reopen_minute,
                            "earliest_assigned_landing": (
                                min(assigned_landings) if assigned_landings else None
                            ),
                        },
                        runway.reopen_minute,
                    ))
            for assignment, action_plan_id, schedule_revision in sorted(
                execution_ledger,
                key=lambda row: (row[0].landing_minute, row[0].flight_id),
            ):
                emitted.append(self._record(
                    state,
                    EventType.execution_tick.value,
                    {
                        "flight_id": assignment.flight_id,
                        "runway": assignment.runway,
                        "gate": assignment.gate,
                        "status": "executing",
                        "plan_id": action_plan_id,
                        "schedule_revision": schedule_revision,
                        "supersedes_plan_id": (
                            selected.id
                            if schedule_revision == "automatic_replan"
                            else None
                        ),
                    },
                    assignment.landing_minute,
                ))

            if recovery_succeeded:
                final_plan = next(
                    (
                        plan for plan in state.plans
                        if plan.id == state.selected_plan_id
                    ),
                    state.plans[0] if state.plans else None,
                )
                recovery_minute = (
                    max(
                        [event.sim_minute for event in emitted]
                        + [state.scenario.current_minute]
                    )
                    + 6
                )
                runtime_contract = verify_runtime_contract(
                    emitted,
                    target_phase=Phase.recovered.value,
                    recovery_minute=recovery_minute,
                    scenario=state.scenario,
                )
                state.decision_lab["runtime_contract"] = runtime_contract
                if final_plan is not None:
                    state.decision_lab["outcome_audit"] = self._build_outcome_audit(
                        state,
                        apply_intervention_outcome(
                            final_plan,
                            state.decision_lab.get("mitigation_by_plan", {}).get(
                                final_plan.id
                            ) or state.decision_lab.get("mitigation", {}),
                        ),
                        confirmed_intent(state),
                        stage="recovered",
                        audit_minute=recovery_minute,
                        baseline=selected if final_plan.id != selected.id else None,
                    )
                    state.decision_lab["outcome_audit"]["runtime_contract"] = (
                        runtime_contract
                    )
                state.phase = Phase.recovered
                emitted.append(self._record(state, EventType.recovered.value, {
                    "selected_plan_id": state.selected_plan_id,
                    "status": "operations_stable",
                    "runtime_contract": runtime_contract,
                    "outcome_audit": state.decision_lab.get("outcome_audit"),
                }, recovery_minute))
            else:
                runtime_contract = verify_runtime_contract(
                    emitted,
                    target_phase=state.phase.value,
                    scenario=state.scenario,
                )
                state.decision_lab["runtime_contract"] = runtime_contract
                if state.decision_lab.get("outcome_audit"):
                    state.decision_lab["outcome_audit"]["runtime_contract"] = (
                        runtime_contract
                    )
            self.store.save_session(state)
        for event in emitted:
            await self.streams.publish(session_id, "execution.event", event.model_dump(mode="json"))
        await self.streams.publish_state(state)
        return ApprovalResponse(session=state, execution_events=emitted)

    async def reset_preference_memory(self) -> dict[str, Any]:
        """Revoke soft preference memory while retaining its audit trail."""
        revoked = self.store.revoke_all_preference_observations()
        return {
            "status": "revoked",
            "revoked": revoked,
            "hard_safety_rules_changed": 0,
            "message": (
                "跨会话软偏好记忆已撤销；审计记录保留但不再参与后验。"
                "当前已生成的方案证据不被改写，下次规划起生效。"
            ),
        }

    def replay(self, session_id: str, after_sequence: int = 0) -> list[ExecutionEvent]:
        self.require(session_id)
        return self.store.replay(session_id, after_sequence)


service = SessionService()
