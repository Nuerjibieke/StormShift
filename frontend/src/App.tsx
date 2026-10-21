import { SolverPipeline } from "./SolverPipeline";
import { ConnectionJourney } from "./ConnectionJourney";
import { useEffect, useMemo, useRef, useState } from "react";
import { IntentPreview } from "./IntentPreview";
import { GuidedStory } from "./GuidedStory";
import { deriveGuidedStory, storyReviewKey, type StoryAction } from "./guidedStory";
import { guidedStoryCopy } from "./guidedStoryCopy";
import { BranchPreviewButton } from "./BranchPreview";
import type { PreviewSelectionReceiptData } from "./branchPreview";
import { PreviewSelectionReceipt } from "./PreviewSelectionReceipt";
import { EvidenceReceipt, observationReplacement } from "./EvidenceReceipt";
import { ReplayPanel } from "./ReplayPanel";
import { historicalSelection, type ReplayFocus } from "./replayNavigation";
import { presentedWorld, hasHistoricalWorlds } from "./historicalWorld";
import { candidateDecisionEvidence } from "./candidateDecisionEvidence";
import { useMachine } from "@xstate/react";
import { CartesianGrid, Cell, LabelList, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { Background, Controls, MarkerType, Position, ReactFlow, type Edge, type Node } from "@xyflow/react";
import {
  Activity, AlertTriangle, BrainCircuit, Check, ChevronDown, CloudLightning,
  CloudRain, Copy, Database, FlaskConical, Gauge, GripHorizontal, Handshake, History,
  Layers3, Leaf, Mic, Pause, Plane, Play, Plus, Radio, RotateCcw, ShieldCheck, Stethoscope, Upload,
  Users, Wrench, X,
} from "lucide-react";
import { AirportWorld, type MapLayer } from "./AirportWorld";
import { aircraftStageAt, milestonesFromEvent } from "./aircraftProjection";
import { FalsificationPanel } from "./FalsificationPanel";
import { TrustedStatePanel } from "./TrustedStatePanel";
import {
  acquireObservation, advanceExecution, ApiError, approvePlan, clarifyIntent, cloneSession, confirmIntent, connectStream, createSession, getFlightExplanation, getSession,
  generatePlans, getReplay, injectEvent, listSessions, submitIntent, submitPreference, transcribe, updateSandbox,
} from "./api";
import { resetPreferenceMemory } from "./api";
import { copy } from "./copy";
import { auditCheckLabel, auditNumber, auditRate, baselineLabel, forecastMetric, auditPassengerMetric } from "./auditPresentation";
import {
  dataBadgeLabel, planTitleLabel, runtimeText, scenarioLabel, weatherLabel, executionEventLabel,
} from "./localization";
import { recoveryMachine } from "./machine";
import {
  approvalActionLabel, canAdvanceExecution, executionMadeProgress, executionNeedsApproval, executionRequestId,
  mergeExecutionEvents, nextExecutionMinute, shouldRequestAdvance, type AdvanceExecutionResponse,
} from "./executionDriver";
import {
  buildPlaybackSchedule, compareExecutionEvents, isMissionBriefingEvent,
  missionChapterForEvent, playbackDuration, playbackClockSamples, formatSimulationMinute, runwayStateAt, emptyPlaybackBatchLabel,
} from "./playback";
import type {
  ActionDiff, AgentTrace, CandidateApprovalGate, DataMode, ExecutionEvent, FlightContrastiveExplanation,
  ConnectingDeparture, Intent, MitigationAnalysis, Mode, OutcomeAudit,
  OperationalBaselineAnalysis, ParetoAnalysis, Phase, Plan, SessionState,
} from "./types";
import "@xyflow/react/dist/style.css";
import "./styles.css";

type Language = keyof typeof copy;
import "./contrastive.css";
import "./mapFirst.css";
import "./intentClarification.css";
import { AgentAvatars, AgentCommunication } from "./AgentCommunication";
import { PassengerPanel } from "./PassengerPanel";
import { AudienceBrief, DecisionSummary } from "./AudienceBrief";
import { audienceNextAction } from "./audienceView";
import "./daylight.tokens.css";
import "./daylight.css";
type CopyKey = keyof typeof copy.zh;

const intentCommandCopy = {
  zh: {
    parseAll: "重新解析完整指令",
    fullPlaceholder: "输入完整替代指令；逐项补充请使用下方问题框",
    replacementHint: "此输入框会替换整份草稿。要保留原目标并补充细节，请回答下方对应问题。",
    pending: "指令仍有待澄清问题，尚未执行",
    ready: "请核对当前目标并人工确认，尚未执行",
    missingVersion: "当前草稿缺少版本，请重新解析完整指令。",
  },
  en: {
    parseAll: "Parse full replacement command",
    fullPlaceholder: "Enter a full replacement command; answer individual questions below",
    replacementHint: "This input replaces the entire draft. To retain the original goals, answer the matching question below.",
    pending: "The command still needs clarification; nothing has been executed",
    ready: "Review the current goals and confirm them; nothing has been executed",
    missingVersion: "This draft has no version. Parse a complete command again.",
  },
};


type PlaybackTask = {
  id: number;
  callback: () => void;
  remainingMs: number;
  startedAt: number;
};

type DirectorCue = {
  event: ExecutionEvent;
  chapter?: number;
  briefing?: boolean;
  step?: number;
};

const phaseLabels: Record<Language, Record<string, string>> = {
  zh: {
    normal: "运行正常", incident: "事故响应", analyzing: "Agent 分析中",
    candidates: "候选已生成", awaiting_approval: "等待审批", executing: "方案执行中",
    monitoring: "持续监控", replanning: "自动重规划", recovered: "运行已恢复",
    infeasible: "需要人工处置",
  },
  en: {
    normal: "Normal", incident: "Incident", analyzing: "Agents Analyzing",
    candidates: "Candidates Ready", awaiting_approval: "Awaiting Approval",
    executing: "Executing", monitoring: "Monitoring", replanning: "Auto Replanning",
    recovered: "Recovered", infeasible: "Human Action Needed",
  },
};

const scenarioNames = {
  storm: "强雷暴与单跑道运行",
  vhhh_storm: "VHHH 公开布局 · 合成雷暴恢复",
  gate_outage: "廊桥故障与晚高峰连锁",
  medical: "医疗备降与转机波次",
};

function ModePicker({
  language,
  setLanguage,
  onStart,
  onResume,
  busy,
}: {
  language: Language;
  setLanguage: (language: Language) => void;
  onStart: (options: {
    mode: Mode;
    scenario_id: string;
    data_mode: DataMode;
    imported_scenario?: unknown;
    sandbox_flights?: number;
  }) => void;
  onResume: (session: SessionState) => Promise<void>;
  busy: boolean;
}) {
  const t = (key: CopyKey) => copy[language][key];
  const [mode, setMode] = useState<Mode>("guided");
  const [advanced, setAdvanced] = useState(false);
  const [scenario, setScenario] = useState("storm");
  const [dataMode, setDataMode] = useState<DataMode>("synthetic");
  const [imported, setImported] = useState<unknown>();
  const [recentSessions, setRecentSessions] = useState<SessionState[]>([]);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [sandboxFlights, setSandboxFlights] = useState(12);
  const [fileName, setFileName] = useState("");
  const modes: Array<{ id: Mode; icon: typeof Plane; title: CopyKey; desc: CopyKey }> = [
    { id: "guided", icon: Play, title: "guided", desc: "guidedDesc" },
    { id: "scenarios", icon: FlaskConical, title: "scenarios", desc: "scenariosDesc" },
    { id: "sandbox", icon: Layers3, title: "sandbox", desc: "sandboxDesc" },
  ];

  useEffect(() => {
    listSessions(1).then(setRecentSessions).catch(() => setRecentSessions([]));
  }, []);

  const resumeSession = async (source: SessionState, clone = false) => {
    setHistoryBusy(true);
    try {
      await onResume(clone ? await cloneSession(source.id) : source);
    } finally {
      setHistoryBusy(false);
    }
  };

  const loadImport = async (file?: File) => {
    if (!file) return;
    const value = JSON.parse(await file.text());
    setImported(value);
    setFileName(file.name);
    setDataMode("imported");
  };

  return (
    <div className="lobby">
      <header className="lobby-header">
        <div className="wordmark"><Plane size={22} /><span>{t("appName")}</span></div>
        <div className="lobby-actions">
          {recentSessions[0] && (
            <div className="recent-session">
              <History size={15} />
              <span>
                <small>{t("recentExercise")}</small>
                <strong>{scenarioLabel(recentSessions[0].scenario.id, recentSessions[0].scenario.name, language)}</strong>
              </span>
              <em>{phaseLabels[language][recentSessions[0].phase]}</em>
              <button disabled={historyBusy} onClick={() => resumeSession(recentSessions[0])}>
                {t("resume")}
              </button>
              <button
                className="clone-session"
                aria-label={t("cloneSnapshot")}
                title={t("cloneSnapshot")}
                disabled={historyBusy}
                onClick={() => resumeSession(recentSessions[0], true)}
              ><Copy size={13} /></button>
            </div>
          )}
          <button className="language-switch" onClick={() => setLanguage(language === "zh" ? "en" : "zh")}>
            {language === "zh" ? "EN" : "中文"}
          </button>
        </div>
      </header>
      <div className="lobby-copy">
        <div className="eyebrow"><Radio size={14} /> HUMAN–AGENT OPERATIONS LAB</div>
        <h1>{t("appSubtitle")}</h1>
        <p>{t("mainStoryPitch")}</p>
      </div>
      <section className={`mode-grid ${advanced ? "" : "single-story"}`} aria-label={language === "zh" ? "演练模式" : "Exercise modes"}>
        {modes.filter(item => advanced || item.id === "guided").map(({ id, icon: Icon, title, desc }) => (
          <button
            key={id}
            className={`mode-card ${mode === id ? "selected" : ""}`}
            onClick={() => setMode(id)}
          >
            <span className="mode-icon"><Icon size={20} /></span>
            <span className="mode-title">
              {t(title)}
              {id === "guided" && <small>{t("defaultMode")}</small>}
            </span>
            <span className="mode-desc">{t(desc)}</span>
            <span className="mode-arrow">↗</span>
          </button>
        ))}
      </section>
      {!advanced && <div className="story-entry-steps">{guidedStoryCopy[language].stages.map((step, index) =>
        <span key={step.title}><b>{String(index + 1).padStart(2, "0")}</b>{step.title}</span>)}</div>}
      <button className="story-advanced-toggle" onClick={() => {
        if (advanced) { setMode("guided"); setDataMode("synthetic"); }
        setAdvanced(value => !value);
      }}>{advanced ? t("mainStoryDefaults") : t("advancedTools")}</button>
      {mode === "scenarios" && (
        <div className="scenario-picker">
          {Object.entries(scenarioNames).map(([id, name]) => (
            <button key={id} className={scenario === id ? "active" : ""} onClick={() => setScenario(id)}>
              {scenarioLabel(id, name, language)}
            </button>
          ))}
        </div>
      )}
      {mode === "sandbox" && (
        <div className="sandbox-density">
          <span>{t("sandboxDensity")} <b>{sandboxFlights}</b></span>
          <input
            aria-label={t("sandboxDensity")}
            type="range"
            min="4"
            max="20"
            step="1"
            value={sandboxFlights}
            onChange={(event) => setSandboxFlights(Number(event.target.value))}
          />
          <small>{t("sandboxDensityHint")}</small>
        </div>
      )}
      <footer className="lobby-footer">
        <div className="data-modes" hidden={!advanced}>
          <Database size={15} />
          {(["synthetic", "live"] as DataMode[]).map((item) => (
            <button key={item} className={dataMode === item ? "active" : ""} onClick={() => setDataMode(item)}>
              {t(item)}
            </button>
          ))}
          <label className={dataMode === "imported" ? "active" : ""}>
            <Upload size={14} /> {fileName || t("imported")}
            <input type="file" accept=".json,application/json" onChange={(event) => loadImport(event.target.files?.[0])} />
          </label>
        </div>
        <button
          className="primary enter-button"
          disabled={busy || (dataMode === "imported" && !imported)}
          onClick={() => onStart({
            mode,
            scenario_id: mode === "guided" ? "storm" : mode === "sandbox" ? "sandbox" : scenario,
            data_mode: dataMode,
            imported_scenario: imported,
            sandbox_flights: mode === "sandbox" ? sandboxFlights : undefined,
          })}
        >
          {busy ? t("loading") : t("enter")} <span>→</span>
        </button>
      </footer>
    </div>
  );
}

function VoiceControl({
  onText,
  onError,
  language,
}: {
  onText: (text: string) => void;
  onError: (message: string) => void;
  language: Language;
}) {
  const t = (key: CopyKey) => copy[language][key];
  const recorder = useRef<MediaRecorder | undefined>(undefined);
  const chunks = useRef<Blob[]>([]);
  const timeout = useRef<number | undefined>(undefined);
  const [listening, setListening] = useState(false);

  const start = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const active = new MediaRecorder(stream, { mimeType: "audio/webm" });
      recorder.current = active;
      chunks.current = [];
      active.ondataavailable = (event) => event.data.size && chunks.current.push(event.data);
      active.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        setListening(false);
        try {
          const result = await transcribe(new Blob(chunks.current, { type: "audio/webm" }));
          if (result.text) onText(result.text);
        } catch (error) {
          onError(error instanceof Error ? error.message : "语音不可用，请使用文字输入");
        }
      };
      active.start();
      setListening(true);
      timeout.current = window.setTimeout(stop, 20_000);
    } catch {
      onError("麦克风不可用，文字输入仍可正常使用");
    }
  };
  const stop = () => {
    window.clearTimeout(timeout.current);
    if (recorder.current?.state === "recording") recorder.current.stop();
  };
  return (
    <button
      className={`voice-button ${listening ? "listening" : ""}`}
      onPointerDown={start}
      onPointerUp={stop}
      onPointerLeave={stop}
      aria-label={t("voiceHold")}
    >
      <Mic size={16} /> {listening ? t("listening") : t("voiceHold")}
    </button>
  );
}

function AgentDock({ session, language, onOpen, onPassengers }: {
  session: SessionState; language: Language; onOpen: () => void; onPassengers: () => void;
}) {
  const count = session.traces.length;
  return <aside className="agent-dock collapsed" aria-label={language === "zh" ? "人物与协同视角" : "People and collaboration"}>
    <button className="dock-title passenger-launch" onClick={onPassengers}><Users size={15}/><span>{language === "zh" ? "旅客视角" : "Passengers"}</span></button>
    <button className="dock-title" onClick={onOpen} disabled={!session.traces.length}>
      <BrainCircuit size={16}/><span>{language === "zh" ? "Agent 做了什么" : "Agent work"}</span>
      <em>{count ? "→" : (language === "zh" ? "待分析" : "Waiting")}</em>
    </button>
  </aside>;
}

function PlanCard({
  plan,
  selected,
  onSelect,
  language,
  approvalGate,
}: {
  plan: Plan;
  selected: boolean;
  onSelect: () => void;
  language: Language;
  mitigation?: MitigationAnalysis;
  approvalGate?: CandidateApprovalGate;
}) {
  const t = (key: CopyKey) => copy[language][key];
  const icon = plan.profile === "passenger"
    ? Users
    : plan.profile === "green"
      ? Leaf
      : plan.profile === "resilient"
        ? ShieldCheck
        : Gauge;
  const Icon = icon;
  const shield = plan.risk_shield;
  const shieldStatus = shield?.status ?? "watch";
  const runwayConditional = (shield?.runway_clearance?.conditional_actions ?? 0) > 0;
  const shieldCopy = runwayConditional
    ? (language === "zh" ? "跑道需最新开放证据" : "Runway evidence required")
    : {
      clear: language === "zh" ? "90% 上界留有余量" : "90% upper bound buffered",
      watch: language === "zh" ? "受控执行并监控" : "Guarded execution",
      escalate: language === "zh" ? "禁止无人值守执行" : "Human decision required",
    }[shieldStatus];
  // Keep the main card on the same scheduling forecast as rehearsal.
  // Optional intervention proposals are not approved or executed flight outcomes.
  const missedLabel = t("missed");
  const missedValue = auditNumber(forecastMetric(plan.report, "missed_connections"), 2);
  const gateCopy = approvalGate ? {
    ready: language === "zh" ? "证据齐全 · 可批准" : "Evidence ready",
    human_required: language === "zh" ? "仍有风险，请你确认" : "Human risk decision",
    needs_evidence: language === "zh" ? "缺少关键证据 · 暂不可批准" : "Evidence required",
    blocked: language === "zh" ? "安全阻断" : "Safety blocked",
  }[approvalGate.status] : "";
  return (
    <button className={`plan-card ${selected ? "selected" : ""}`} onClick={onSelect}>
      <div className="plan-card-head">
        <span className="plan-icon"><Icon size={17} /></span>
        <span><strong>{planTitleLabel(plan.title, plan.profile, language)}</strong><small>{plan.profile.toUpperCase()}</small></span>
        {plan.rank === 1 && <em><Check size={11} /> {language === "zh" ? "风险校准推荐" : "RISK-CALIBRATED"}</em>}
      </div>
      {approvalGate && (
        <div className={`plan-approval-gate ${approvalGate.status}`}>
          <ShieldCheck size={12} /><strong>{gateCopy}</strong><small>{approvalGate.summary}</small>
        </div>
      )}
      <div className={`plan-shield ${shieldStatus}`}>
        <ShieldCheck size={12} />
        <strong>CONFORMAL SHIELD · {shieldStatus.toUpperCase()}</strong>
        <small>{shieldCopy}</small>
      </div>
      {plan.evidence_basis && (
        <div className="plan-evidence-basis">
          <Database size={11} />
          <strong>{language === "zh" ? "证据约束" : "EVIDENCE"}</strong>
          <small>{plan.evidence_basis.confirmed_facts} facts · {plan.evidence_basis.compiled_constraints} rules</small>
          <code>{plan.evidence_basis.trusted_state_digest.slice(0, 8)}</code>
        </div>
      )}
      <div className="plan-metrics">
        <span><small>{t("meanDelay")}</small><b>{plan.report.mean_delay_min}<i> min</i></b></span>
        <span><small>{t("p90")}</small><b>{plan.report.p90_delay_min}<i> min</i></b></span>
        <span><small>{missedLabel}</small><b>{missedValue}<i> pax</i></b></span>
      </div>
      <div className="risk-line"><span style={{ width: `${Math.min(100, plan.score * 3)}%` }} /></div>
    </button>
  );
}

function ActiveSensingCue({
  session,
  selectedPlanId,
  language,
  busy,
  onObserve,
}: {
  session: SessionState;
  selectedPlanId: string;
  language: Language;
  busy: boolean;
  onObserve: (queryId: string) => void;
}) {
  const information = session.decision_lab.information_value;
  const approvalGate = session.decision_lab.approval_gates?.[selectedPlanId];
  const requiredQueryId = approvalGate?.status === "needs_evidence"
    ? approvalGate.required_query_ids[0]
    : undefined;
  const query = information?.queries.find((item) => (
    item.id === (requiredQueryId ?? information.recommended_query_id) && !item.resolved
  ));
  const mandatory = Boolean(requiredQueryId && query?.id === requiredQueryId);
  if (
    session.phase !== "awaiting_approval"
    || (!mandatory && information?.status !== "observe_then_optimize")
    || !query
  ) return null;

  const stressLabel = {
    stable: language === "zh" ? "当前稳定" : "Currently stable",
    watch: language === "zh" ? "需要监控" : "Watch",
    replan: language === "zh" ? "可能重规划" : "May replan",
    manual: language === "zh" ? "可能转人工" : "May require human review",
    unknown: language === "zh" ? "待确认" : "Unknown",
  }[query.stress_status];
  return (
    <aside className={`active-sensing-cue ${query.safety_relevant ? "safety-relevant" : ""} ${mandatory ? "mandatory" : ""}`}>
      <span className="active-sensing-icon"><Radio size={15} /></span>
      <div className="active-sensing-copy">
        <small>DECISION-AWARE ACTIVE SENSING · VALUE OF INFORMATION</small>
        <strong>{mandatory
          ? (language === "zh" ? `审批闸门缺证据：${query.label}` : `Approval blocked pending: ${query.label_en}`)
          : (language === "zh" ? `审批前最值得确认：${query.label}` : `Best evidence before approval: ${query.label_en}`)}</strong>
        <p>{language === "zh" ? query.reveals : query.reveals_en}</p>
      </div>
      <div className="active-sensing-evidence">
        <span>
          <small>{query.safety_relevant
            ? (language === "zh" ? "观测作用" : "Purpose")
            : (language === "zh" ? "不观测的后悔" : "Regret if skipped")}</small>
          <b>{query.safety_relevant
            ? (language === "zh" ? "安全闸门" : "Safety gate")
            : query.regret_if_unobserved.toFixed(2)}</b>
        </span>
        <span>
          <small>{query.safety_relevant
            ? (language === "zh" ? "压力世界先验" : "Stress prior")
            : (language === "zh" ? "预计减少" : "Expected reduction")}</small>
          <b>{query.safety_relevant
            ? `${Math.round(query.prior * 100)}%`
            : query.expected_regret_reduction.toFixed(2)}</b>
        </span>
        <em className={query.stress_status}>{stressLabel}</em>
      </div>
      <button onClick={() => onObserve(query.id)} disabled={busy}>
        {busy ? <Activity className="spin" size={13} /> : <Database size={13} />}
        {mandatory
          ? (language === "zh" ? "补齐证据并重新求解" : "Acquire evidence and re-solve")
          : (language === "zh" ? query.action_label : query.action_label_en)}
      </button>
    </aside>
  );
}

function OutcomeAuditCard({
  audit,
  onOpen,
  committedCount,
  scenarioFlightCount,
  language,
}: {
  audit: OutcomeAudit;
  onOpen: () => void;
  committedCount: number;
  scenarioFlightCount: number;
  language: Language;
}) {
  const passengers = audit.passengers;
  const operations = audit.operations;
  const statusCopy = {
    passed: language === "zh" ? "全部验收通过" : "All checks passed",
    passed_with_watch: language === "zh" ? "硬约束通过 · 软风险待监控" : "Hard checks passed · monitor soft risk",
    human_required: language === "zh" ? "需要人工复核" : "Human review required",
    failed: language === "zh" ? "验收未通过" : "Acceptance failed",
  }[audit.status];
  const rate = auditRate(passengers.predicted_on_time_rate);
  const delta = audit.comparison;
  const signed = (value: number | null) => value == null ? "—" : `${value > 0 ? "+" : ""}${auditNumber(value)}`;

  return (
    <article className={`outcome-audit-card ${audit.status}`}>
      <header>
        <span><ShieldCheck size={17} /></span>
        <div>
          <small>FORECAST ACCEPTANCE · NOT OBSERVED OUTCOMES</small>
          <strong>{language === "zh" ? "预测验收 · 非实际旅客结果" : "Forecast acceptance"}</strong>
        </div>
        <em>{statusCopy}</em>
      </header>
      <div className="outcome-audit-metrics">
        <span>
          <small>{language === "zh" ? "硬约束" : "Hard constraints"}</small>
          <b>{audit.hard_constraints.passed}/{audit.hard_constraints.total}</b>
          <i>{audit.hard_constraints.all_passed ? "PASS" : "FAIL"}</i>
        </span>
        <span className="passenger-primary">
          <small>{language === "zh" ? "本轮预计按时转机" : "Scoped on-time connections"}</small>
          <b>{auditNumber(auditPassengerMetric(passengers, "on_time"), 2)}<i> / {passengers.decision_scope}</i></b>
          <i>{rate}</i>
        </span>
        <span className={auditPassengerMetric(passengers, "missed") ? "watch" : ""}>
          <small>{language === "zh" ? "平均预计错失转机" : "Mean expected missed"}</small>
          <b>{auditNumber(auditPassengerMetric(passengers, "missed"), 2)}<i> pax</i></b>
          <i>{language === "zh" ? "Monte Carlo 均值" : "Monte Carlo mean"}</i>
        </span>
        <span className={passengers.tail_risk_exposed ? "watch" : ""}>
          <small>{language === "zh" ? "尾部观察名单" : "Tail watchlist"}</small>
          <b>{passengers.tail_risk_exposed}<i> pax</i></b>
          <i>{language === "zh" ? "与错失重叠 · 不相加" : "overlaps · do not add"}</i>
        </span>
        <span className={operations.fuel_breaches ? "danger" : ""}>
          <small>{language === "zh" ? "燃油风险越界" : "Fuel-risk breaches"}</small>
          <b>{auditNumber(operations.fuel_breaches)}<i> flights</i></b>
          <i>{operations.fuel_breaches == null ? "UNKNOWN" : operations.fuel_breaches ? "FAIL" : "PASS"}</i>
        </span>
        <span>
          <small>{language === "zh" ? "已提交航班" : "Committed flights"}</small>
          <b>{committedCount}<i> / {scenarioFlightCount}</i></b>
          <i>{language === "zh" ? "场景航班口径 · 事件账本" : "scenario scope · event ledger"}</i>
        </span>
      </div>
      <div className="outcome-scope-equation">
        <span><small>SCENARIO</small><b>{passengers.scenario_total}</b></span>
        <i>→</i>
        <span><small>{language === "zh" ? "本轮范围" : "IN SCOPE"}</small><b>{passengers.decision_scope}</b></span>
        <i>=</i>
        <span className="good"><small>{language === "zh" ? "预计按时" : "ON TIME"}</small><b>{auditNumber(auditPassengerMetric(passengers, "on_time"), 2)}</b></span>
        <i>+</i>
        <span className="watch"><small>{language === "zh" ? "平均错失" : "MEAN MISSED"}</small><b>{auditNumber(auditPassengerMetric(passengers, "missed"), 2)}</b></span>
        <em><Layers3 size={11} />{language === "zh"
          ? `尾部观察 ${passengers.tail_risk_exposed} 人是重叠风险视角，不能相加`
          : `Tail watch ${passengers.tail_risk_exposed} overlaps; do not add`}</em>
      </div>
      <footer>
        <span>
          {language === "zh"
            ? `口径：本次决策 ${passengers.decision_scope} / 场景 ${passengers.scenario_total} 名转机旅客；范围外 ${passengers.out_of_scope} 人不计作“按时”；平均资源排队 ${operations.remaining_queue_conflicts} 次是额外等待，不是硬冲突`
            : `Scope: ${passengers.decision_scope} / ${passengers.scenario_total} transfer passengers; ${passengers.out_of_scope} out of scope`}
          {delta && (
            <small>
              {delta.comparable
                ? <>{language === "zh" ? "同范围重规划变化" : "Same-scope replan delta"} · P90 {signed(delta.p90_delay_delta_min)}m · {language === "zh" ? "超时" : "missed"} {signed(delta.missed_connections_delta)}</>
                : <>{language === "zh" ? "重规划范围改变" : "Replan scope changed"} · {delta.scope_changed.baseline_flights}→{delta.scope_changed.final_flights} {language === "zh" ? "架，差值不直接比较" : "flights; deltas are not directly comparable"}</>}
            </small>
          )}
          {audit.outcome_contract && (
            <small className={`outcome-contract-chip ${audit.outcome_contract.status}`}>
              {language === "zh" ? "运营目标契约" : "Outcome contract"} · {audit.outcome_contract.passed}/{audit.outcome_contract.total} PASS
            </small>
          )}
          {audit.runtime_contract && (
            <small className={`runtime-contract-chip ${audit.runtime_contract.status}`}>
              {language === "zh" ? "运行契约" : "Runtime contract"} · {audit.runtime_contract.passed}/{audit.runtime_contract.total} PASS
            </small>
          )}
        </span>
        <button onClick={onOpen}>{language === "zh" ? "查看验收证据" : "Open evidence"} →</button>
      </footer>
    </article>
  );
}

// IntentPreview lives in its own tested, source-evidence component.


function AgentRelay({
  traces,
  slots,
  language,
  onOpen,
}: {
  traces: AgentTrace[];
  slots: 1 | 2;
  language: Language;
  onOpen: () => void;
}) {
  const priority = ["Conformal Risk Shield", "Safety Critic", "Active Sensing Advisor", "Causal Impact Agent", "Robustness Agent"];
  const seen = new Set<string>();
  const featured = [...traces].reverse().filter((trace) => {
    if (trace.agent === "Recovery Coordinator" || seen.has(trace.agent)) return false;
    seen.add(trace.agent);
    return true;
  }).sort((left, right) => {
    const leftRank = priority.indexOf(left.agent);
    const rightRank = priority.indexOf(right.agent);
    return (leftRank < 0 ? 99 : leftRank) - (rightRank < 0 ? 99 : rightRank);
  }).slice(0, slots === 2 ? 3 : 2);
  const [activeAgent, setActiveAgent] = useState(featured[0]?.agent ?? "");
  const active = featured.find((trace) => trace.agent === activeAgent) ?? featured[0];
  const iconFor = (agent: string) => {
    if (agent.includes("Sensing")) return Database;
    if (agent.includes("Safety") || agent.includes("Risk Shield")) return ShieldCheck;
    if (agent.includes("Causal") || agent.includes("Robustness")) return BrainCircuit;
    if (agent.includes("Weather")) return CloudRain;
    if (agent.includes("Runway")) return Gauge;
    if (agent.includes("Gate")) return Wrench;
    return Users;
  };
  const ActiveIcon = active ? iconFor(active.agent) : BrainCircuit;
  return (
    <section className={`agent-relay span-${slots}`}>
      <header>
        <span><BrainCircuit size={15} /></span>
        <div><small>SPECIALIST EVIDENCE → SAFE SOLVER</small><strong>{language === "zh" ? "专业分析与求解接力" : "Specialists to Safe Solver"}</strong></div>
        <em>{featured.filter((trace) => trace.status === "completed").length}/{featured.length} VERIFIED</em>
        <button onClick={onOpen}>{language === "zh" ? `查看全部 ${traces.length}` : `View all ${traces.length}`} →</button>
      </header>
      <div className="agent-relay-tabs">
        {featured.map((trace) => {
          const Icon = iconFor(trace.agent);
          return (
            <button key={trace.agent} className={trace.agent === active?.agent ? "active" : ""} onClick={() => setActiveAgent(trace.agent)}>
              <Icon size={12} /><span>{trace.agent}</span><i />
            </button>
          );
        })}
      </div>
      {active && (
        <article className="agent-relay-detail">
          <span><ActiveIcon size={16} /></span>
          <div><small>{active.role}</small><strong>{active.agent}</strong><p>{runtimeText(active.summary, language)}</p></div>
          <code title={active.tool}>{active.tool ?? "validated schema"}</code>
        </article>
      )}
    </section>
  );
}

function ActionDiffStory({
  diff,
  committedCount,
  slots,
  language,
  expanded = false,
  onOpen,
}: {
  diff: ActionDiff;
  committedCount: number | undefined;
  slots: 1 | 2;
  language: Language;
  expanded?: boolean;
  onOpen?: () => void;
}) {
  const statusCopy: Record<ActionDiff["rows"][number]["status"], string> = language === "zh"
    ? { changed: "已调整", unchanged: "保持原计划", added: "新增", removed: "撤销" }
    : { changed: "Changed", unchanged: "Preserved", added: "Added", removed: "Retired" };
  const fieldCopy: Record<string, string> = language === "zh"
    ? {
      landing_minute: "落地时刻",
      runway: "跑道",
      gate: "机位",
      hold_minutes: "等待",
      taxi_minutes: "滑行",
      remote_stand: "远机位",
    }
    : {
      landing_minute: "landing",
      runway: "runway",
      gate: "gate",
      hold_minutes: "hold",
      taxi_minutes: "taxi",
      remote_stand: "remote stand",
    };
  const changedRows = diff.rows.filter((row) => row.status !== "unchanged");
  const rows = expanded
    ? diff.rows
    : (changedRows.length ? changedRows : diff.rows).slice(0, 3);
  const assignmentLabel = (assignment: ActionDiff["rows"][number]["before"]) => {
    if (!assignment) return language === "zh" ? "无动作" : "No action";
    return `${assignment.runway} · T+${assignment.landing_minute} · ${assignment.gate}`;
  };

  return (
    <section className={`action-diff-story span-${slots} ${expanded ? "expanded" : ""}`}>
      <header>
        <span><RotateCcw size={15} /></span>
        <div>
          <small>CAUSAL REPLAN · ACTION DIFF</small>
          <strong>{language === "zh" ? "故障后到底改了什么" : "What actually changed after the fault"}</strong>
        </div>
        <em>{diff.changed + diff.added + diff.removed} {language === "zh" ? "架改变" : "changed"}</em>
        {onOpen && <button onClick={onOpen}>{language === "zh" ? "查看证据" : "Evidence"} →</button>}
      </header>
      <div className="action-diff-counts">
        <span><small>{language === "zh" ? "故障时已执行并锁定" : "Committed at disruption"}</small><b>{diff.committed_count_at_replan ?? committedCount ?? "—"}</b></span>
        <span className="changed"><small>{language === "zh" ? "未来动作调整" : "Future changed"}</small><b>{diff.changed + diff.added + diff.removed}</b></span>
        <span><small>{language === "zh" ? "未来动作保持" : "Future preserved"}</small><b>{diff.unchanged}</b></span>
      </div>
      <div className="action-diff-rows">
        {rows.map((row) => (
          <div key={row.flight_id} className={row.status}>
            <strong>{row.flight_id}</strong>
            <span>{assignmentLabel(row.before)}</span>
            <i>→</i>
            <span>{assignmentLabel(row.after)}</span>
            <em>{statusCopy[row.status]}{row.changed_fields.length
              ? ` · ${row.changed_fields.map((field) => fieldCopy[field] ?? field).join(" / ")}`
              : ""}</em>
          </div>
        ))}
      </div>
      <footer>
        <ShieldCheck size={11} />
        {language === "zh"
          ? "只比较尚未执行的未来动作；已经落地或占用资源的飞机不会消失、回滚或换位。"
          : "Only uncommitted future actions are compared; committed aircraft cannot disappear, roll back, or move stands."}
      </footer>
    </section>
  );
}

function DecisionProvenanceGraph({
  traces, selectedIndex, onSelect, language,
}: {
  traces: AgentTrace[];
  selectedIndex: number;
  onSelect: (index: number) => void;
  language: Language;
}) {
  const findTrace = (agent: string, role?: string) => traces.findIndex((trace) =>
    trace.agent === agent && (!role || trace.role.includes(role))
  );
  const weather = findTrace("Weather Agent");
  const runway = findTrace("Runway Agent");
  const gate = findTrace("Gate & Ground Agent");
  const passenger = findTrace("Airline & Passenger Agent");
  const optimizer = findTrace("CP-SAT Decision Engine");
  const riskShield = findTrace("Conformal Risk Shield");
  const safety = findTrace("Safety Critic");
  const coordinator = findTrace("Recovery Coordinator", "监督");
  const causal = findTrace("Causal Impact Agent");
  const robustness = findTrace("Robustness Agent");
  const recourseMode = causal >= 0;

  const syntheticNode = (
    id: string,
    label: string,
    subtitle: string,
    position: { x: number; y: number },
    kind: string,
  ): Node => ({
    id,
    position,
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    draggable: false,
    selectable: false,
    className: `provenance-node synthetic ${id}`,
    data: { label: <div className="provenance-label"><small>{kind}</small><strong>{label}</strong><span>{subtitle}</span></div> },
  });
  const traceNode = (
    id: string,
    index: number,
    position: { x: number; y: number },
    kind: string,
  ): Node => {
    const trace = traces[index];
    return {
      id,
      position,
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      draggable: false,
      className: `provenance-node ${trace?.status ?? "missing"} ${selectedIndex === index ? "selected" : ""}`,
      data: {
        traceIndex: index,
        label: (
          <div className="provenance-label">
            <small>{kind}</small>
            <strong>{trace?.agent ?? id}</strong>
            <span>{trace?.role ?? (language === "zh" ? "等待输出" : "Awaiting output")}</span>
          </div>
        ),
      },
    };
  };
  const makeEdge = (source: string, target: string, label = "", accent = false): Edge => ({
    id: `${source}-${target}`,
    source,
    target,
    type: "smoothstep",
    label,
    animated: target === "optimizer" || source === "causal",
    markerEnd: { type: MarkerType.ArrowClosed, color: accent ? "#af8cff" : "#5eb9aa" },
    style: { stroke: accent ? "#af8cff" : "#5eb9aa", strokeWidth: accent ? 1.8 : 1.25, opacity: .72 },
    labelStyle: { fill: accent ? "#7450aa" : "#44766d", fontSize: 8, fontWeight: 700 },
    labelBgStyle: { fill: "#ffffff", fillOpacity: .92 },
  });

  const nodes: Node[] = [
    syntheticNode("fusion", language === "zh" ? "现场数据与目标" : "Airport facts & goal", "ScenarioSpec + Intent", { x: 0, y: 130 }, "INPUT"),
    traceNode("weather", weather, { x: 180, y: 0 }, "PARALLEL"),
    traceNode("runway", runway, { x: 180, y: 88 }, "PARALLEL"),
    traceNode("gate", gate, { x: 180, y: 176 }, "PARALLEL"),
    traceNode("passenger", passenger, { x: 180, y: 264 }, "PARALLEL"),
    traceNode("optimizer", optimizer, { x: 405, y: 130 }, "CP-SAT + SIMPY"),
    traceNode("safety", safety, { x: 610, y: 130 }, "HARD GATE"),
    traceNode("risk-shield", riskShield, { x: 815, y: 130 }, "UNCERTAINTY GATE"),
    traceNode("coordinator", coordinator, { x: 1020, y: 130 }, "SUPERVISOR"),
  ];
  if (recourseMode) {
    nodes.push(
      syntheticNode("failure", "TUG-3", language === "zh" ? "动态故障事件" : "Dynamic disruption", { x: 0, y: 304 }, "TRIGGER"),
      traceNode("causal", causal, { x: 180, y: 304 }, "NETWORKX SCOPE"),
    );
  }
  if (robustness >= 0) {
    nodes.push(traceNode("robustness", robustness, { x: 1220, y: 35 }, "STRESS WORLDS"));
  }
  nodes.push(syntheticNode(
    "approval",
    recourseMode ? (language === "zh" ? "自动恢复门" : "Auto-recourse gate") : (language === "zh" ? "操作员审批" : "Operator approval"),
    recourseMode ? "Safety Critic PASS" : "Human in the loop",
    { x: 1220, y: 220 },
    recourseMode ? "EXECUTION" : "HUMAN GATE",
  ));

  const edges: Edge[] = [
    makeEdge("fusion", "weather"),
    makeEdge("fusion", "runway"),
    makeEdge("fusion", "gate"),
    makeEdge("fusion", "passenger"),
    makeEdge("fusion", "optimizer", language==="zh"?"归一化字段 + 目标参数":"Facts + objective"),
    makeEdge("weather", "safety", language==="zh"?"核对":"Checks"),
    makeEdge("runway", "safety"),
    makeEdge("gate", "safety"),
    makeEdge("passenger", "safety"),
    makeEdge("optimizer", "safety", "CANDIDATES"),
    makeEdge("safety", "risk-shield", "SAFE ONLY"),
    makeEdge("risk-shield", "coordinator", "RISK TIER"),
  ];
  if (recourseMode) {
    edges.push(makeEdge("failure", "causal", "IMPACT", true), makeEdge("causal", "optimizer", "FROZEN BOUNDARY", true));
  }
  if (robustness >= 0) {
    edges.push(makeEdge("coordinator", "robustness", "STRESS"), makeEdge("robustness", "approval", "EVIDENCE"));
  } else {
    edges.push(makeEdge("coordinator", "approval", recourseMode ? "AUTO" : "PROPOSE"));
  }

  return (
    <section className="provenance-graph">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        fitViewOptions={{ padding: .12 }}
        minZoom={.42}
        maxZoom={1.65}
        nodesDraggable={false}
        nodesConnectable={false}
        onNodeClick={(_, node) => {
          const index = node.data.traceIndex;
          if (typeof index === "number" && index >= 0) onSelect(index);
        }}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#264441" gap={18} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
      <footer>
        <span><i className="parallel" /> {language === "zh" ? "并行分析" : "Parallel analysis"}</span>
        <span><i className="verified" /> {language === "zh" ? "已验证工具输出" : "Verified tool output"}</span>
        {recourseMode && <span><i className="causal" /> {language === "zh" ? "因果局部修复分支" : "Causal repair branch"}</span>}
        <em>{language === "zh" ? "拖动画布 · 滚轮缩放 · 点击节点看证据" : "Pan · zoom · click nodes for evidence"}</em>
      </footer>
    </section>
  );
}

function AgentRelayViewer({
  traces,
  language,
  onClose,
  session,
  planId,
  playbackPaused,
}: {
  traces: AgentTrace[];
  language: Language;
  onClose: () => void;
  session: SessionState;
  planId?: string;
  playbackPaused: boolean;
}) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [viewMode, setViewMode] = useState<"communication" | "graph" | "reader">("communication");
  const selected = traces[selectedIndex];
  const completed = traces.filter((trace) => trace.status === "completed").length;
  const stageFor = (trace: AgentTrace, index: number) => {
    const agent = trace.agent.toLowerCase();
    const tool = (trace.tool ?? "").toLowerCase();
    if (agent.includes("weather") || agent.includes("runway") || agent.includes("gate") || agent.includes("airline")) {
      return language === "zh" ? "并行专业分析" : "Parallel specialist analysis";
    }
    if (agent.includes("safety")) return language === "zh" ? "安全硬约束闸门" : "Hard-safety gate";
    if (agent.includes("risk shield")) return language === "zh" ? "不确定性运行闸门" : "Uncertainty execution gate";
    if (agent.includes("causal")) return language === "zh" ? "因果影响边界" : "Causal impact boundary";
    if (agent.includes("robustness")) return language === "zh" ? "反事实压力测试" : "Counterfactual stress test";
    if (tool.includes("cp-sat") || tool.includes("ortools")) return language === "zh" ? "候选方案求解" : "Candidate optimization";
    return index === traces.length - 1
      ? (language === "zh" ? "协调结论与执行门" : "Coordinator decision and execution gate")
      : (language === "zh" ? "监督协调" : "Supervisor coordination");
  };
  const handoffFor = (trace: AgentTrace) => {
    const agent = trace.agent;
    if (["Weather Agent", "Runway Agent", "Gate & Ground Agent", "Airline & Passenger Agent"].includes(agent)) {
      return {
        target: "CP-SAT Decision Engine",
        detail: language === "zh"
          ? "专业助手并行核对共享现场。求解器读取归一化的现场字段和目标参数；助手结论用于交叉审查。"
          : "Specialists check the shared airport state in parallel. The optimizer reads normalized airport facts and objective parameters; specialist findings support cross-checks.",
      };
    }
    if (agent === "Safety Critic") {
      return {
        target: "Conformal Risk Shield",
        detail: language === "zh" ? "硬约束通过后，候选进入独立的不确定性包络检查；安全证明与风险等级不会混为一谈。" : "After hard constraints pass, candidates enter a separate uncertainty-envelope check; safety proof and risk tier remain distinct.",
      };
    }
    if (agent === "Conformal Risk Shield") {
      return {
        target: "Recovery Coordinator",
        detail: language === "zh" ? "Shield 只给出 CLEAR / WATCH / ESCALATE 证据并限制无人值守执行；它不生成航班动作。" : "The shield only emits CLEAR / WATCH / ESCALATE evidence and limits unattended execution; it never generates flight actions.",
      };
    }

    if (agent === "Causal Impact Agent") {
      return {
        target: "CP-SAT · Local Repair",
        detail: language === "zh" ? "NetworkX 影响边界决定哪些航班可冻结、哪些航班允许重排，随后才启动局部求解。" : "The NetworkX impact boundary freezes unaffected flights and limits which flights may be replanned.",
      };
    }
    if (agent === "Robustness Agent") {
      return {
        target: traces.some((item) => item.agent === "Causal Impact Agent") ? (language === "zh" ? "自动恢复安全门" : "Auto-recourse safety gate") : (language === "zh" ? "操作员审批" : "Operator approval"),
        detail: language === "zh" ? "压力世界结果作为执行门的风险证据；它不会直接生成或修改飞机动作。" : "Stress-world results provide risk evidence to the execution gate; they never directly generate aircraft actions.",
      };
    }
    if (agent === "CP-SAT Decision Engine") {
      return {
        target: "Safety Critic",
        detail: language === "zh" ? "CP-SAT 候选和 SimPy Monte Carlo 指标进入独立安全校验，失败候选会被拒绝。" : "CP-SAT candidates and SimPy Monte Carlo metrics enter an independent safety gate; failed candidates are rejected.",
      };
    }
    return {
      target: traces.some((item) => item.agent === "Robustness Agent") ? "Robustness Agent" : (language === "zh" ? "操作员审批" : "Operator approval"),
      detail: language === "zh" ? "协调器只形成推荐与解释，最终动作仍需经过鲁棒性证据和相应的人机或自动安全门。" : "The coordinator forms recommendations and explanations; execution still requires robustness evidence and the applicable human or automated safety gate.",
    };
  };
  if (!selected) return null;
  const handoff = handoffFor(selected);
  const detailCard = (
    <article className="relay-reader">
      <div className="relay-reader-heading">
        <span><small>TRACE {String(selectedIndex + 1).padStart(2, "0")}</small><em>{stageFor(selected, selectedIndex)}</em></span>
        <h2>{selected.agent}</h2>
        <p>{selected.role}</p>
      </div>
      <section className="relay-full-summary">
        <small>{language === "zh" ? "该 Agent 的完整结论" : "Complete agent conclusion"}</small>
        <p>{runtimeText(selected.summary, language)}</p>
      </section>
      <div className="relay-evidence-grid">
        <span><small>{language === "zh" ? "实际工具 / 方法" : "Tool / method"}</small><code>{selected.tool ?? "validated schema"}</code></span>
        <span><small>{language === "zh" ? "输出状态" : "Output status"}</small><b><Check size={12} /> {selected.status}</b></span>
        <span><small>{language === "zh" ? "置信度" : "Confidence"}</small><b>{Math.round(selected.confidence * 100)}%</b></span>
        <span><small>{language === "zh" ? "记录时间" : "Recorded at"}</small><b>{new Date(selected.timestamp).toLocaleTimeString(language === "zh" ? "zh-CN" : "en-GB", { hour12: false })}</b></span>
      </div>
      <section className="relay-handoff">
        <span>{String(selectedIndex + 1).padStart(2, "0")}</span>
        <div>
          <small>{language === "zh" ? "真实数据流向" : "Actual data flow"}</small>
          <strong>{selected.agent} → {handoff.target}</strong>
          <p>{handoff.detail}</p>
        </div>
      </section>
      <footer>
        <button disabled={selectedIndex === 0} onClick={() => setSelectedIndex((index) => Math.max(0, index - 1))}>
          ← {language === "zh" ? "上一条" : "Previous"}
        </button>
        <span>{selectedIndex + 1} / {traces.length}</span>
        <button disabled={selectedIndex === traces.length - 1} onClick={() => setSelectedIndex((index) => Math.min(traces.length - 1, index + 1))}>
          {language === "zh" ? "下一条" : "Next"} →
        </button>
      </footer>
    </article>
  );
  return (
    <div className="modal-backdrop relay-viewer-backdrop" onMouseDown={onClose}>
      <section className="relay-viewer glass" role="dialog" aria-modal="true" aria-label={language === "zh" ? "Agent 决策与沟通" : "Agent decisions and communication"} onKeyDown={event => { if (event.key === "Escape") onClose(); }} onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <div className="relay-header-title">
            <span><BrainCircuit size={18} /></span>
            <span>
              <small>VERIFIABLE TOOLCHAIN · LANGGRAPH + CP-SAT</small>
              <strong>{language === "zh" ? "Agent 怎样检查这份计划" : "Agent Toolchain & Plan Provenance"}</strong>
              <p>{language === "zh" ? "先看具体航班的检查，再看全部飞机的输入信息、时间和位置。" : "Follow one flight from input data through optimization to simulation results."}</p>
            </span>
          </div>
          <div className="relay-header-actions">
            <span role="group" aria-label={language === "zh" ? "溯源查看模式" : "Provenance view mode"}>
              <button className={viewMode === "communication" ? "active" : ""} onClick={() => setViewMode("communication")}>{language === "zh" ? "沟通过程" : "Handoffs"}</button>
              <button className={viewMode === "graph" ? "active" : ""} onClick={() => setViewMode("graph")}>{language === "zh" ? "拓扑图" : "Graph"}</button>
              <button className={viewMode === "reader" ? "active" : ""} onClick={() => setViewMode("reader")}>{language === "zh" ? "逐条阅读" : "Reader"}</button>
            </span>
            <button autoFocus onClick={onClose} aria-label={language === "zh" ? "关闭决策溯源" : "Close decision provenance"}><X size={18} /></button>
          </div>
        </header>
        {viewMode === "reader" && <div className="relay-overview">
          <span><small>{language === "zh" ? "可验证记录" : "Verified traces"}</small><b>{traces.length}</b></span>
          <span><small>{language === "zh" ? "已完成" : "Completed"}</small><b>{completed}/{traces.length}</b></span>
          <span><small>{language === "zh" ? "当前选中" : "Selected"}</small><b>{String(selectedIndex + 1).padStart(2, "0")}</b></span>
          <span><small>{language === "zh" ? "置信度" : "Confidence"}</small><b>{Math.round(selected.confidence * 100)}%</b></span>
        </div>}
        {viewMode === "communication" ? <AgentCommunication session={session} planId={planId} language={language} playbackPaused={playbackPaused}/> : viewMode === "graph" ? (
          <div className="relay-graph-mode pipeline-layout">
            <DecisionProvenanceGraph traces={traces} selectedIndex={selectedIndex} onSelect={setSelectedIndex} language={language} />
            <SolverPipeline session={session} planId={planId} language={language} trace={selected}/>
          </div>
        ) : (
          <div className="relay-viewer-body">
            <nav aria-label={language === "zh" ? "Agent 可验证记录" : "Agent verified traces"}>
              {traces.map((trace, index) => (
                <button
                  key={`${trace.agent}-${trace.timestamp}-${index}`}
                  className={index === selectedIndex ? "active" : ""}
                  onClick={() => setSelectedIndex(index)}
                >
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <div>
                    <strong>{trace.agent}</strong>
                    <small>{stageFor(trace, index)}</small>
                  </div>
                  <i className={trace.status === "completed" ? "complete" : ""}>
                    {trace.status === "completed" ? <Check size={11} /> : <Activity size={11} />}
                  </i>
                </button>
              ))}
            </nav>
            {detailCard}
          </div>
        )}
      </section>
    </div>
  );
}

function SystemEventFlow({
  event, session, language,
}: {
  event: ExecutionEvent;
  session: SessionState;
  language: Language;
}) {
  const robustness = session.decision_lab.robustness;
  const [selectedWorldId, setSelectedWorldId] = useState("");
  const selectedWorld = robustness?.worlds.find((world) => world.id === selectedWorldId) ?? robustness?.worlds[0];
  const statusLabels = language === "zh"
    ? { stable: "可直行", watch: "需监控", replan: "需重规划", manual: "人工处置" }
    : { stable: "Proceed", watch: "Watch", replan: "Replan", manual: "Manual" };

  if (event.type.includes("replan") && robustness && selectedWorld) {
    return (
      <div className="briefing-horizon">
        <div className="stress-world-tabs" role="group" aria-label={language === "zh" ? "未来压力世界" : "Future stress worlds"}>
          {robustness.worlds.map((world) => (
            <button
              key={world.id}
              className={"stress-world-tab " + (world.id === selectedWorld.id ? "active " : "") + world.status}
              aria-pressed={world.id === selectedWorld.id}
              onClick={() => setSelectedWorldId(world.id)}
            >
              <span><strong>{language === "zh" ? world.label : world.label_en}</strong><em>{statusLabels[world.status]}</em></span>
              <small>{language === "zh" ? world.change : world.change_en}</small>
            </button>
          ))}
        </div>
        <div className="stress-world-proof">
          <span><small>FIXED PLAN · P90</small><b>{selectedWorld.fixed.p90_delay}<i>min</i></b></span>
          <i>→</i>
          <span><small>ADAPTIVE REPLAN · P90</small><b>{selectedWorld.adaptive.p90_delay}<i>min</i></b></span>
          <strong className={selectedWorld.fixed_safe ? "" : "unsafe"}>
            {selectedWorld.fixed_safe
              ? (language === "zh" ? `安全可执行 · 挽救 ${selectedWorld.saved_missed} 名转机旅客` : `Safe to execute · ${selectedWorld.saved_missed} connections saved`)
              : (language === "zh" ? "固定方案越过安全边界 · 必须重规划" : "Fixed plan crosses a safety boundary · replan required")}
          </strong>
        </div>
      </div>
    );
  }

  const systemSteps = event.type === "trusted_state_reconciled"
    ? (language === "zh" ? ["接收确认报告", "标记失效事实", "重新编译约束", "交给 CP-SAT"] : ["Receive confirmed report", "Invalidate old facts", "Recompile constraints", "Hand off to CP-SAT"])
    : event.type.includes("failure")
      ? (language === "zh" ? ["检测故障", "隔离资源", "锁定未受影响航班", "触发因果修复"] : ["Detect fault", "Isolate resources", "Lock unaffected flights", "Trigger causal repair"])
    : event.type === "plan_approved"
      ? (language === "zh" ? ["人工批准", "安全证明", "下发调度"] : ["Human approval", "Safety proof", "Dispatch schedule"])
      : event.type === "recovered"
        ? (language === "zh" ? ["完成动作", "封存事件日志", "确定性回放就绪"] : ["Actions complete", "Seal event log", "Deterministic replay ready"])
        : (language === "zh" ? ["记录事件", "验证状态", "更新数字孪生"] : ["Record event", "Validate state", "Update digital twin"]);
  return (
    <div className="system-event-flow">
      {systemSteps.map((step, index) => (
        <span key={step} className={index === systemSteps.length - 1 ? "active" : "done"}><b>{String(index + 1).padStart(2, "0")}</b>{step}{index < systemSteps.length - 1 && <i>→</i>}</span>
      ))}
    </div>
  );
}

function ExecutionGuide({
  language, cue, next, session, slots,
}: {
  language: Language;
  cue: DirectorCue;
  next?: ExecutionEvent;
  session: SessionState;
  slots: number;
}) {
  const event = cue.event;
  const payload = event.payload;
  const flightId = String(payload.flight_id ?? "");
  const steps = language === "zh"
    ? ["等待放行", "进近跑道", "落地占用", "Taxi C 滑行", "进入机位"]
    : ["Release", "Approach", "Runway occupied", "Taxi C", "At gate"];
  const chapters = language === "zh"
    ? ["方案放行", "第一波执行", "二次故障", "局部修复", "稳定恢复"]
    : ["Approval", "Initial wave", "Secondary fault", "Local repair", "Recovered"];
  const runway = String(payload.runway ?? "");
  const gate = String(payload.gate ?? "");
  const flight = session.scenario.flights.find((item) => item.id === flightId);
  const recourse = (payload.recourse ?? {}) as Record<string, unknown>;
  const scheduleSwitch = (payload.schedule_switch ?? {}) as Record<string, unknown>;
  const actionDiff = (payload.action_diff ?? session.decision_lab.action_diff) as ActionDiff | undefined;
  const actionDiffRows = actionDiff?.rows
    .filter((row) => row.status !== "unchanged").slice(0, 3) ?? [];
  const stage = cue.step ?? 0;
  const isFlight = event.type === "execution_tick";
  const flowStage = isFlight ? stage : event.type === "plan_approved" ? 0 : -1;
  const stageTitles = language === "zh"
    ? [`${flightId} 等待受控放行`, `${flightId} 进入 ${runway} 进近序列`, `${flightId} 在 ${runway} 落地，跑道尚未释放`, `${flightId} 经 Taxi C 滑向 ${gate}`, `${flightId} 进入 ${gate} 机位`]
    : [`${flightId} awaits release`, `${flightId} joins the ${runway} approach`, `${flightId} lands; ${runway} remains occupied`, `${flightId} taxis via Taxi C to ${gate}`, `${flightId} enters gate ${gate}`];
  let title = stageTitles[stage] ?? event.type;
  let detail = `${runway} → Taxi C → ${gate}`, reason = flight?.connections ? (language === "zh" ? `保护 ${flight.connections} 名转机旅客` : `Protect ${flight.connections} connecting passengers`) : (language === "zh" ? "满足燃油与间隔约束" : "Meet fuel and separation constraints"), guard = language === "zh" ? "跑道、燃油与机位硬约束已验证" : "Runway, fuel and gate hard constraints verified";
  if (event.type === "trusted_state_reconciled") {
    title = language === "zh" ? "可信运行状态已重建" : "Trusted operational state rebuilt";
    detail = language === "zh"
      ? `${payload.changed_facts ?? 0} 项事实变化 · ${payload.invalidated_constraints ?? 0} 条约束失效并重新编译`
      : `${payload.changed_facts ?? 0} facts changed · ${payload.invalidated_constraints ?? 0} constraints recompiled`;
    reason = language === "zh" ? "冲突、过期或失效信息不能直接进入求解器" : "Conflicted, stale or invalidated claims cannot enter the solver";
    guard = language === "zh" ? `CP-SAT 输入已绑定证据摘要 ${String(payload.digest ?? "").slice(0, 10)}` : `CP-SAT input bound to evidence digest ${String(payload.digest ?? "").slice(0, 10)}`;
  } else if (event.type === "clock_advanced") {
    title = language === "zh"
      ? `执行时钟推进到 T+${event.sim_minute}`
      : `Execution clock advanced to T+${event.sim_minute}`;
    detail = language === "zh"
      ? "后端只提交到下一个真实资源里程碑"
      : "The backend committed only through the next real resource milestone";
    reason = language === "zh"
      ? "让画面、燃油时钟和资源状态保持同一时间"
      : "Keep the scene, fuel clocks and resources on one time axis";
    guard = language === "zh"
      ? "未来动作尚未提交，不会提前出现在画面"
      : "Future actions remain uncommitted and cannot leak into the scene";
  } else if (event.type === "runway_released") {
    title = language === "zh"
      ? `${String(payload.flight_id)} 已脱离 ${String(payload.runway)}`
      : `${String(payload.flight_id)} cleared ${String(payload.runway)}`;
    detail = language === "zh" ? "该跑道容量已归还，可接收下一架飞机" : "Runway capacity is released for the next aircraft";
    reason = language === "zh" ? "落地与跑道占用是两个独立提交阶段" : "Landing and runway occupancy are separate commit stages";
    guard = language === "zh" ? "尾流间隔继续由 CP-SAT 硬约束保护" : "Wake separation remains protected by CP-SAT";
  } else if (event.type === "gate_arrived") {
    title = language === "zh"
      ? `${String(payload.flight_id)} 已到达 ${String(payload.gate)}`
      : `${String(payload.flight_id)} reached ${String(payload.gate)}`;
    detail = language === "zh"
      ? `机位占用到 T+${String(payload.gate_release_minute ?? "—")}`
      : `Stand occupied through T+${String(payload.gate_release_minute ?? "—")}`;
    reason = language === "zh" ? "滑行完成后才正式占用机位" : "The stand is committed only after taxi-in";
    guard = language === "zh" ? "同一时刻一个机位只允许一架飞机" : "At most one aircraft may occupy a stand";
  } else if (event.type === "gate_released") {
    title = language === "zh"
      ? `${String(payload.flight_id)} 周转完成并释放 ${String(payload.gate)}`
      : `${String(payload.flight_id)} completed turnaround at ${String(payload.gate)}`;
    detail = language === "zh" ? "这架飞机已完成本轮下客与地面服务" : "Turnaround ended and the stand is available; no takeoff clearance is implied";
    reason = language === "zh" ? "后续航班现在才能安全进入该机位" : "A following aircraft may now safely enter the stand";
    guard = language === "zh" ? "已提交飞机不会被重规划回滚" : "Committed aircraft are never rolled back by replanning";
  } else if (event.type === "gate_recovered") {
    title = language === "zh"
      ? `${String(payload.resource_id)} 机位恢复可用`
      : `Stand ${String(payload.resource_id)} recovered`;
    detail = language === "zh" ? "设备检修窗口结束，资源状态已更新" : "The maintenance window ended and the resource state was updated";
    reason = language === "zh" ? "恢复后的容量可进入下一轮滚动决策" : "Recovered capacity enters the next rolling decision";
    guard = language === "zh" ? "恢复事实已写入事件日志" : "The recovery fact is recorded in the event log";
  } else if (event.type === "scheduled_event") {
    title = language === "zh" ? "动态事件监测规则已布置" : "Dynamic event monitor armed";
    detail = language === "zh" ? "若运行态势变化，游标会在事件时刻停下" : "The cursor will stop at the event time if operations change";
    reason = language === "zh" ? "把未知扰动作为在线事件处理，而非预先改写结局" : "Treat disruptions online instead of prewriting the outcome";
    guard = language === "zh" ? "事件生效前不会改变机场资源" : "No airport resource changes before the event takes effect";
  } else if (event.type === "plan_certificate_verified") {
    title = language === "zh" ? "携证方案验证通过" : "Proof-carrying plan verified";
    detail = language === "zh" ? "计划、可信状态和约束摘要一致" : "Plan, trusted state and constraint digest agree";
    reason = language === "zh" ? "只有可验证的求解器动作才能进入执行层" : "Only verifiable solver actions may enter execution";
    guard = language === "zh" ? "LLM 无权绕过硬约束" : "The LLM cannot bypass hard constraints";
  } else if (event.type.includes("failure")) {
    const nested = (payload.payload ?? {}) as Record<string, unknown>;
    const fuelClock = (nested.fuel_clock ?? {}) as Record<string, unknown>;
    const affected = Array.isArray(nested.affected_gates) ? nested.affected_gates.join(" / ") : "A3 / A4 / R1";
    const fuelAdvanced = typeof fuelClock.elapsed_min === "number";
    const expiredFuelFlights = Array.isArray(fuelClock.expired_flight_ids)
      ? fuelClock.expired_flight_ids.map(String)
      : [];
    const fuelOutcome = expiredFuelFlights.length
      ? (language === "zh"
        ? ` · ${expiredFuelFlights.join(" / ")} 越过截止，已转安全备降`
        : ` · ${expiredFuelFlights.join(" / ")} crossed deadline and diverted`)
      : (language === "zh" ? " · 截止时刻未后移" : " · deadlines preserved");
    title = language === "zh" ? "牵引车 TUG-3 故障，部分机位受影响" : "Tow tractor TUG-3 failed; some stands are affected";
    detail = language === "zh" ? `隔离受影响资源：${affected}` : `Isolate exposed resources: ${affected}`;
    reason = language === "zh" ? "阻断故障传播到后续机位" : "Stop fault propagation to downstream stands";
    guard = fuelAdvanced
      ? (language === "zh"
        ? `燃油时钟推进 ${fuelClock.elapsed_min} 分钟 · ${fuelClock.updated_flights} 架空中航班更新 · 最小余量 ${fuelClock.minimum_remaining_min} 分钟${fuelOutcome}`
        : `Fuel clock +${fuelClock.elapsed_min}m · ${fuelClock.updated_flights} airborne updated · minimum ${fuelClock.minimum_remaining_min}m${fuelOutcome}`)
      : (language === "zh" ? "未受影响航班保持原计划" : "Unaffected flights remain locked");
  } else if (event.type.includes("replan")) {
    title = runtimeText(String(payload.summary ?? (language === "zh" ? "因果局部重规划" : "Causal local repair")), language);
    const switchSuffix = typeof scheduleSwitch.replacement_future_actions === "number"
      ? (language === "zh"
        ? ` · 撤销旧未来动作 ${scheduleSwitch.superseded_future_actions} 条 → 新方案接管 ${scheduleSwitch.replacement_future_actions} 条`
        : ` · retire ${scheduleSwitch.superseded_future_actions} old future actions → activate ${scheduleSwitch.replacement_future_actions} revised actions`)
      : "";
    detail = language === "zh"
      ? `仅重排 ${recourse.affected_count ?? 0} 架 · 锁定 ${recourse.frozen_count ?? 0} 架 · 实改 ${recourse.changed_count ?? 0} 架${switchSuffix}`
      : `Replan ${recourse.affected_count ?? 0} · lock ${recourse.frozen_count ?? 0} · change ${recourse.changed_count ?? 0}${switchSuffix}`;
    reason = language === "zh" ? "先找出故障影响的航班，再只修复尚未执行的安排" : "Identify affected flights, then repair only unexecuted assignments";
    guard = language === "zh"
      ? `CP-SAT 修复已通过 Safety Critic${switchSuffix ? "；后续动作已绑定新计划版本" : ""}`
      : `CP-SAT repair passed the Safety Critic${switchSuffix ? "; future actions are bound to the revised plan" : ""}`;
    if (event.type.includes("requires_approval")) {
      title = language === "zh" ? "新方案已生成，等待你批准" : "Repair proposed; waiting for your approval";
      guard = language === "zh" ? "新动作尚未执行；已有飞机位置与占用保持不变" : "New actions are not executing; committed positions and occupancy remain intact";
    } else if (event.type.includes("failed")) {
      title = language === "zh" ? "修复未通过，已暂停并等待人工处理" : "Repair failed; paused for operator review";
      detail = runtimeText(String(payload.summary ?? (language === "zh" ? "请查看失败原因，不会执行未通过的方案" : "Inspect the failure; no rejected plan will execute")), language);
      guard = language === "zh" ? "不能把生成候选当成安全验证通过" : "Generating a candidate is not safety approval";
    }
  } else if (event.type === "runway_reopened") {
    const runwayId = String(payload.resource_id ?? "07R");
    title = language === "zh" ? `${runwayId} 跑道恢复开放` : `Runway ${runwayId} reopened`;
    detail = language === "zh" ? "雷击检查窗口结束，红色关闭标识已解除" : "The inspection window ended and the closure marker was cleared";
    reason = language === "zh" ? "后续航班现在才可进入该跑道" : "Only now may later flights use this runway";
    guard = language === "zh" ? `最早分配落地 T+${payload.earliest_assigned_landing ?? "—"}，不早于恢复时刻` : `Earliest assigned landing T+${payload.earliest_assigned_landing ?? "—"}, not before reopening`;
  } else if (event.type === "recovered") {
    title = language === "zh" ? "本轮演练执行结束，请核对结果" : "Execution ended; review the outcome";
    detail = language === "zh" ? "查看安全约束、航班执行记录和预计转机情况" : "Inspect safety checks, flight records and connection forecasts";
    reason = language === "zh" ? "运行恢复不等于所有旅客都能按时转机" : "Recovery does not mean every passenger makes their connection";
    guard = language === "zh" ? "事件日志已封存，可确定性回放" : "Event log sealed for deterministic replay";
  } else if (event.type === "plan_approved") {
    title = language === "zh" ? "操作员批准恢复方案" : "Operator approved the recovery plan";
    detail = language === "zh" ? "CP-SAT 分配进入数字孪生执行层" : "CP-SAT assignments enter the digital twin";
    reason = language === "zh" ? "人工审批完成" : "Human approval completed";
    guard = language === "zh" ? "执行前硬约束已全部验证" : "Hard constraints verified before execution";
  }
  const nextLabel = !next
    ? emptyPlaybackBatchLabel(event, language)
    : next.type === "execution_tick" ? `${String(next.payload.flight_id)} 放行`
      : next.type === "runway_reopened" ? (language === "zh" ? `${String(next.payload.resource_id)} 跑道恢复` : `Runway ${String(next.payload.resource_id)} reopens`)
        : next.type === "trusted_state_reconciled" ? (language === "zh" ? "重建可信事实" : "Reconcile trusted facts")
          : next.type.includes("failure") ? (language === "zh" ? "二次故障" : "Secondary disruption")
            : next.type.includes("replan") ? (language === "zh" ? "自动局部重规划" : "Automatic local repair")
            : next.type === "recovered" ? (language === "zh" ? "确认恢复" : "Confirm recovery")
              : executionEventLabel(next.type, language);
  return (
    <section className={`execution-guide span-${slots} ${event.type} ${cue.briefing ? "briefing" : ""}`} role="status" aria-live="polite">
      <header>
        <span><Activity size={14} /></span>
        <div>
          <small>AI MISSION DIRECTOR · EVENT #{event.sequence}</small>
          <strong>{language === "zh" ? "当前正在发生" : "Happening now"}</strong>
        </div>
        <em>T+{String(event.sim_minute).padStart(2, "0")}</em><i>{cue.briefing ? "BRIEFING" : "LIVE"}</i>
      </header>
      <div className="director-focus">
        <strong>{title}</strong><p>{detail}</p><div><span><b>{language === "zh" ? "原因" : "WHY"}</b>{reason}</span><span><b>{language === "zh" ? "保障" : "GUARD"}</b>{guard}</span></div>
      </div>
      <div className="mission-chapters" aria-label={language === "zh" ? "任务章节" : "Mission chapters"}>
        {chapters.map((chapter, index) => (
          <span key={chapter} className={index === (cue.chapter ?? 0) ? "active" : index < (cue.chapter ?? 0) ? "done" : ""}><b>0{index + 1}</b>{chapter}</span>
        ))}
      </div>
      {isFlight ? (
        <div className="execution-flow">
          {steps.map((step, index) => (
            <span key={step} className={flowStage === index ? "active" : flowStage > index ? "done" : ""}><b>{index + 1}</b>{step}{index < steps.length - 1 && <i>→</i>}</span>
          ))}
        </div>
      ) : (
        <SystemEventFlow event={event} session={session} language={language} />
      )}
      {event.type.includes("replan") && actionDiff && (
        <div className="execution-action-diff">
          <span>
            <small>{language === "zh" ? "已执行锁定" : "Committed"}</small>
            <b>{session.execution?.committed_operations.length ?? 0}</b>
          </span>
          <span>
            <small>{language === "zh" ? "未来调整" : "Future changed"}</small>
            <b>{actionDiff.changed + actionDiff.added + actionDiff.removed}</b>
          </span>
          {actionDiffRows.map((row) => (
            <em key={row.flight_id}>
              {row.flight_id} · T+{row.before?.landing_minute ?? "—"} → T+{row.after?.landing_minute ?? "—"}
              {row.before?.gate !== row.after?.gate ? ` · ${row.before?.gate ?? "—"}→${row.after?.gate ?? "—"}` : ""}
            </em>
          ))}
          <i>{language === "zh" ? "其余未来动作保持不变" : "All other future actions stay unchanged"}</i>
        </div>
      )}
      <footer>
        <span>{next && <>{language === "zh" ? "下一事件" : "Up next"} · <b>T+{next.sim_minute}</b> </>}{nextLabel}</span>
        <em>{cue.briefing ? (language === "zh" ? "关键节点已自动暂停 · 讲解后点击继续" : "Auto-paused at a key decision · resume when ready") : (language === "zh" ? "讲解模式会在关键节点自动暂停" : "Briefing mode pauses at key decisions")}</em>
      </footer>
    </section>
  );
}
function FlightLens({
  session, plan, flightId, language, onClose, onResume, onFlightChange,
}: {
  session: SessionState;
  plan?: Plan;
  flightId: string;
  language: Language;
  onClose: () => void;
  onResume?: () => void;
  onFlightChange: (flightId: string) => void;
}) {
  const [contrastive, setContrastive] = useState<FlightContrastiveExplanation>();
  const [contrastiveLoading, setContrastiveLoading] = useState(false);
  const [contrastiveError, setContrastiveError] = useState("");
  useEffect(() => {
    let active = true;
    setContrastive(undefined);
    setContrastiveError("");
    if (!plan) return () => { active = false; };
    setContrastiveLoading(true);
    getFlightExplanation(session.id, plan.id, flightId)
      .then((result) => {
        if (active) setContrastive(result);
      })
      .catch((reason: unknown) => {
        if (active) {
          setContrastiveError(
            reason instanceof Error ? reason.message : "反事实解释生成失败",
          );
        }
      })
      .finally(() => {
        if (active) setContrastiveLoading(false);
      });
    return () => { active = false; };
  }, [session.id, plan?.id, flightId]);

  const flight = session.scenario.flights.find((item) => item.id === flightId);
  if (!flight) return null;
  const flightIds = plan
    ? [...plan.assignments].sort((left, right) => left.landing_minute - right.landing_minute).map((item) => item.flight_id)
    : session.scenario.flights.map((item) => item.id);
  const flightIndex = Math.max(0, flightIds.indexOf(flightId));
  const assignment = plan?.assignments.find((item) => item.flight_id === flightId);
  const recourse = candidateDecisionEvidence(session, plan?.id ?? "").recourse;
  const affected = recourse?.affected_flight_ids.includes(flightId) ?? false;
  const preserved = recourse?.preserved_flight_ids.includes(flightId) ?? false;
  const changed = recourse?.changed_flight_ids.includes(flightId) ?? false;
  const path = recourse?.dependency_paths.find((item) => item.includes(flightId));
  const metrics = plan?.report.per_flight[flightId];
  const acdmLedger = (
    (plan && session.decision_lab.acdm_by_plan?.[plan.id])
    ?? session.decision_lab.acdm
  );
  const acdmRecord = acdmLedger?.records.find(
    (record) => record.flight_id === flightId,
  );
  const status = changed
    ? (language === "zh" ? "局部重规划已调整" : "Changed by local repair")
    : preserved
      ? (language === "zh" ? "因果边界外 · 原计划锁定" : "Outside causal scope · locked")
      : affected
        ? (language === "zh" ? "位于故障影响范围" : "Inside disruption scope")
        : (language === "zh" ? "当前批准计划" : "Current approved plan");
  const reason = changed && path
    ? (language === "zh" ? "故障传播路径：" : "Fault path: ") + path.join(" → ")
    : preserved
      ? (language === "zh" ? "因果图证明该航班未受 TUG-3 故障传播影响，因此保持原分配。" : "The causal graph proves this flight is outside TUG-3 propagation, so its assignment stays fixed.")
      : flight.medical_priority
        ? (language === "zh" ? "医疗优先是不可覆盖的硬约束。" : "Medical priority is a non-overridable hard constraint.")
        : (language === "zh" ? "CP-SAT 在转机保护、延误、燃油和资源冲突之间选择该分配。" : "CP-SAT chose this assignment across connections, delay, fuel and resource conflicts.");
  return (
    <div className="modal-backdrop flight-lens-backdrop" onMouseDown={onClose}>
      <section className="flight-lens glass" onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <span><Plane size={18} /></span>
          <div><small>FLIGHT LENS · EXPLAINABLE DECISION</small><strong>{language === "zh" ? "这架飞机为什么这样走" : "Why this aircraft moves this way"}</strong></div>
          <button onClick={onClose} aria-label={language === "zh" ? "关闭航班检查" : "Close flight inspection"}><X size={17} /></button>
        </header>
        <div className="flight-lens-hero">
          <div><small>{flight.origin} → NHI</small><h2>{flight.id}</h2><p>{flight.aircraft_type} · {flight.state.toUpperCase()}</p></div>
          <em className={changed ? "changed" : preserved ? "locked" : affected ? "affected" : ""}>{status}</em>
        </div>
        <div className="flight-lens-pager">
          <button disabled={flightIndex === 0} onClick={() => onFlightChange(flightIds[flightIndex - 1])}>← {language === "zh" ? "上一架" : "Previous"}</button>
          <span>{language === "zh" ? "按批准落地顺序" : "Approved landing order"} · {flightIndex + 1}/{flightIds.length}</span>
          <button disabled={flightIndex === flightIds.length - 1} onClick={() => onFlightChange(flightIds[flightIndex + 1])}>{language === "zh" ? "下一架" : "Next"} →</button>
        </div>
        <div className="flight-lens-route">
          <span><small>RUNWAY</small><b>{assignment?.runway ?? "TBD"}</b></span><i>→</i>
          <span><small>TAXI</small><b>Taxi C</b></span><i>→</i>
          <span><small>GATE</small><b>{assignment?.gate ?? flight.planned_gate}</b></span>
        </div>
        {acdmRecord && acdmLedger && (
          <div className="flight-acdm">
            <header>
              <span>
                <small>A-CDM MILESTONE CONTRACT</small>
                <strong>{language === "zh" ? "机场协同里程碑与求解血缘" : "Collaborative milestones and solver lineage"}</strong>
                <i>HELD-OUT CONFORMAL {Math.round(acdmLedger.uncertainty_calibration.target_coverage * 100)}% · {acdmLedger.uncertainty_calibration.calibration_samples} CAL / {acdmLedger.uncertainty_calibration.evaluation_samples ?? "—"} TEST · OBS {acdmLedger.uncertainty_calibration.empirical_coverage == null ? "LEGACY" : `${(acdmLedger.uncertainty_calibration.empirical_coverage * 100).toFixed(1)}%`}</i>
              </span>
              <em className={acdmLedger.status}>
                {acdmLedger.status === "committed" ? (language === "zh" ? "已批准" : "COMMITTED") : (language === "zh" ? "候选" : "CANDIDATE")}
              </em>
            </header>
            <div className="acdm-milestones">
              {acdmRecord.milestones.map((milestone) => {
                const role = {
                  solver_input: language === "zh" ? "CP-SAT 输入" : "CP-SAT INPUT",
                  solver_output: language === "zh" ? "CP-SAT 输出" : "CP-SAT OUTPUT",
                  derived: language === "zh" ? "规则推导" : "DERIVED",
                  external_missing: language === "zh" ? "系统未接入" : "NOT CONNECTED",
                }[milestone.role];
                return (
                  <span key={milestone.code} className={milestone.role} title={`${milestone.owner} · ${milestone.source} · ${milestone.note}`}>
                    <small>{milestone.code}</small>
                    <b>{milestone.minute === null ? "—" : `T+${milestone.minute}`}</b>
                    {milestone.uncertainty && (
                      <mark>
                        {Math.round(milestone.uncertainty.coverage * 100)}% · T+{milestone.uncertainty.lower_minute.toFixed(1)}–{milestone.uncertainty.upper_minute.toFixed(1)}
                      </mark>
                    )}
                    <i>{language === "zh" ? milestone.label : milestone.label_en}</i>
                    <em>{role}</em>
                    <u>{milestone.owner}</u>
                  </span>
                );
              })}
            </div>
            <div className="acdm-contract">
              <span><b>READ</b>{language === "zh" ? "STA、跑道/机位、燃油与优先级" : "STA, resources, fuel and priority"}</span>
              <span><b>WRITE</b>{language === "zh" ? "落地时隙、跑道、机位与等待" : "landing slot, runway, gate and hold"}</span>
              <span><b>BOUNDARY</b>{language === "zh" ? "TSAT 由离港排序系统负责" : "TSAT belongs to departure sequencing"}</span>
            </div>
            <p>{language === "zh" ? acdmLedger.caveat : "Training ledger, not an operational A-CDM system. TOBT is provisional; TSAT requires a departure sequencer."}</p>
          </div>
        )}
        <div className="flight-lens-metrics">
          <span><small>{language === "zh" ? "转机旅客" : "Connections"}</small><b>{flight.connections}<i> pax</i></b></span>
          <span><small>{language === "zh" ? "燃油余量" : "Fuel margin"}</small><b>{flight.fuel_margin_min}<i> min</i></b></span>
          <span><small>{language === "zh" ? "计划等待" : "Planned hold"}</small><b>{assignment?.hold_minutes ?? "—"}<i> min</i></b></span>
          <span><small>P90 {language === "zh" ? "延误" : "delay"}</small><b>{metrics?.p90_delay?.toFixed(1) ?? "—"}<i> min</i></b></span>
        </div>
        <article><small>WHY THIS MOVE</small><p>{reason}</p></article>
        {assignment && assignment.gate !== flight.planned_gate && <p className="flight-change">{language === "zh" ? "原计划机位" : "Original gate"} {flight.planned_gate} → {assignment.gate}</p>}
        <section className="flight-counterfactual">
          <header>
            <span>
              <small>CONSTRAINT COUNTERFACTUAL · ON-DEMAND RE-SOLVE</small>
              <strong>{language === "zh" ? "如果强行换一种安排，会怎样？" : "What if we force a different move?"}</strong>
            </span>
            {contrastive && <em>{contrastive.alternatives.filter((item) => item.status === "infeasible").length}/{contrastive.alternatives.length} {language === "zh" ? "被硬约束阻止" : "blocked"}</em>}
          </header>
          {contrastiveLoading && (
            <div className="contrastive-loading">
              <i /><span>{language === "zh" ? "正在构造反事实世界并重新求解…" : "Building counterfactual worlds and re-solving…"}</span>
            </div>
          )}
          {contrastiveError && <p className="contrastive-error">{contrastiveError}</p>}
          {contrastive && (
            <>
              <div className="constraint-witnesses">
                {contrastive.witnesses.filter((item) => item.hard).map((witness) => (
                  <span key={witness.id}>
                    <Check size={11} /><b>{witness.label}</b><small>{witness.detail}</small>
                  </span>
                ))}
              </div>
              <div className="contrastive-grid">
                {contrastive.alternatives.map((alternative) => (
                  <article key={alternative.id} className={alternative.status}>
                    <header>
                      <span><small>{alternative.field.replace("_", " ").toUpperCase()}</small><strong>{alternative.label}</strong></span>
                      <em>{alternative.status === "infeasible" ? (language === "zh" ? "不可行" : "BLOCKED") : (language === "zh" ? "可行" : "FEASIBLE")}</em>
                    </header>
                    <div className="contrastive-change"><b>{alternative.from}</b><i>→</i><b>{alternative.to}</b></div>
                    <p>{alternative.verdict}</p>
                    {alternative.delta ? (
                      <div className="contrastive-deltas">
                        <span><small>Δ {language === "zh" ? "均值" : "MEAN"}</small><b>{alternative.delta.mean_delay_min > 0 ? "+" : ""}{alternative.delta.mean_delay_min}m</b></span>
                        <span><small>Δ P90</small><b>{alternative.delta.p90_delay_min > 0 ? "+" : ""}{alternative.delta.p90_delay_min}m</b></span>
                        <span><small>{language === "zh" ? "联动" : "CHANGED"}</small><b>{alternative.changed_flights} flights</b></span>
                      </div>
                    ) : (
                      <code>{alternative.evidence[0]}</code>
                    )}
                  </article>
                ))}
              </div>
              <footer>
                <span>{contrastive.summary}</span>
                <small>{contrastive.safety_statement}</small>
              </footer>
            </>
          )}
        </section>
        <div className="flight-guards"><span><ShieldCheck size={12} /> {language === "zh" ? "燃油截止" : "Fuel deadline"}</span><span><ShieldCheck size={12} /> {language === "zh" ? "跑道间隔" : "Runway separation"}</span><span><ShieldCheck size={12} /> {language === "zh" ? "机位唯一" : "Gate uniqueness"}</span></div>
        <footer><button onClick={onClose}>{language === "zh" ? "关闭检查" : "Close"}</button>{onResume && <button className="primary" onClick={onResume}><Play size={13} /> {language === "zh" ? "关闭并继续演示" : "Close and resume"}</button>}</footer>
      </section>
    </div>
  );
}
function ParetoFrontier({
  analysis, language, selectedPlanId, onSelect,
}: {
  analysis: ParetoAnalysis;
  language: Language;
  selectedPlanId: string;
  onSelect: (planId: string) => void;
}) {
  const frontier = analysis.points.filter((point) => point.pareto_optimal);
  const dominated = analysis.points.filter((point) => !point.pareto_optimal);
  const recommended = analysis.points.find((point) => point.id === analysis.recommended_id) ?? analysis.points[0];
  const focused = analysis.points.find((point) => point.id === selectedPlanId) ?? recommended;
  return (
    <article className="pareto-section">
      <header className="pareto-head">
        <span><small>ε-PARETO · MULTI-OBJECTIVE COMPROMISE</small><strong>{language === "zh" ? "候选方案权衡前沿" : "Candidate trade-off frontier"}</strong></span>
        <p>{language === "zh" ? "越靠左下越优，点越大表示燃油风险越高。点击右侧候选切换整套证据；关闭后机场地图同步预览。" : "Lower-left is better; larger points mean more fuel risk. Select a candidate to switch all evidence and preview it on the map."}</p>
        <em><b>{focused?.ideal_distance.toFixed(3) ?? "—"}</b><small>{language === "zh" ? "当前理想点距离" : "Current ideal distance"}</small></em>
      </header>
      <div className="pareto-body">
        <div className="pareto-chart" aria-label={language === "zh" ? "P90 延误与错失转机 Pareto 散点图" : "Pareto scatter plot of P90 delay and missed connections"}>
          <ResponsiveContainer width="100%" height={190}>
            <ScatterChart margin={{ top: 24, right: 18, bottom: 12, left: 2 }}>
              <CartesianGrid stroke="rgba(118, 155, 149, .12)" strokeDasharray="3 5" />
              <XAxis type="number" dataKey="delay" name={language === "zh" ? "P90 延误" : "P90 delay"} unit="m" domain={["dataMin - 1", "dataMax + 1"]} tickCount={4} tick={{ fill: "#769690", fontSize: 7 }} axisLine={{ stroke: "rgba(118, 155, 149, .18)" }} />
              <YAxis type="number" dataKey="connections" name={language === "zh" ? "错失转机" : "Missed connections"} unit="pax" domain={[0, "dataMax + 3"]} tickCount={5} allowDecimals={false} tick={{ fill: "#769690", fontSize: 7 }} axisLine={{ stroke: "rgba(118, 155, 149, .18)" }} width={34} />
              <ZAxis type="number" dataKey="fuel_risk" range={[90, 260]} name={language === "zh" ? "燃油风险" : "Fuel risk"} />
              <Tooltip cursor={{ stroke: "rgba(172, 139, 255, .45)", strokeDasharray: "3 3" }} contentStyle={{ border: "1px solid rgba(172, 139, 255, .35)", borderRadius: 8, background: "rgba(5, 20, 24, .96)", fontSize: 9 }} formatter={(value, name) => [String(value), String(name)]} />
              {dominated.length > 0 && <Scatter name={language === "zh" ? "被支配候选" : "Dominated"} data={dominated} fill="#536b68">
                {dominated.map((point) => <Cell key={point.id} fill="#536b68" stroke={point.id === selectedPlanId ? "#d8caff" : "#667f7b"} strokeWidth={point.id === selectedPlanId ? 3 : 1} />)}
                <LabelList dataKey="title" position="left" fill="#667f7b" fontSize={7} />
              </Scatter>}
              <Scatter name={language === "zh" ? "Pareto 前沿" : "Pareto frontier"} data={frontier} fill="#64e7c7">
                {frontier.map((point) => <Cell key={point.id} fill={point.id === analysis.recommended_id ? "#ffc56c" : "#64e7c7"} stroke={point.id === selectedPlanId ? "#d8caff" : point.id === analysis.recommended_id ? "#fff0cf" : "#b8fff0"} strokeWidth={point.id === selectedPlanId ? 3 : 1} />)}
                <LabelList dataKey="title" position="right" fill="#c7dbd7" fontSize={7} />
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        </div>
        <div className="pareto-points">
          {analysis.points.map((point) => (
            <button
              type="button"
              key={point.id}
              aria-pressed={point.id === selectedPlanId}
              onClick={() => onSelect(point.id)}
              className={(point.id === analysis.recommended_id ? "recommended " : "") + (point.id === selectedPlanId ? "active " : "") + (point.pareto_optimal ? "frontier" : "dominated")}
            >
              <i />
              <span><strong>{planTitleLabel(point.title, undefined, language)}</strong><small>P90 {point.delay}m · {point.connections} pax · CO₂ {point.emissions}</small></span>
              <em>{point.id === selectedPlanId ? (language === "zh" ? "正在比较" : "Comparing") : point.id === analysis.recommended_id ? (language === "zh" ? "膝点" : "Knee") : point.pareto_optimal ? "PARETO" : (language === "zh" ? "被支配" : "Dominated")}</em>
            </button>
          ))}
        </div>
      </div>
    </article>
  );
}

function OperationalBaselinePanel({
  analysis,
  language,
}: {
  analysis: OperationalBaselineAnalysis;
  language: Language;
}) {
  const outcomeLabel = (status: string, abstained: boolean) => {
    if (abstained) return language === "zh" ? "安全停机" : "SAFE STOP";
    if (status === "safe") return language === "zh" ? "满足真值约束" : "SAFE";
    return language === "zh" ? "方案不可安全执行" : "UNSAFE PLAN";
  };

  return (
    <article className="operational-baseline">
      <header>
        <span><Activity size={18} /></span>
        <div>
          <small>REALITY BASELINE · CONTROLLED INFORMATION FAULTS</small>
          <strong>
            {language === "zh"
              ? "公平对照：静态方案与同输入重规划"
              : "Fair comparison: static plans and equal-input replanning"}
          </strong>
          <p>
            {language === "zh"
              ? "四组对照：静态 FCFS、静态 CP-SAT、正常重规划 CP-SAT、证据流程；动态两组输入与预算相同。"
              : "Static FCFS, static CP-SAT, updated CP-SAT, and evidence workflow; dynamic inputs and budgets match."}
          </p>
        </div>
        <em>{analysis.scope.fault_worlds} FAULT WORLDS</em>
      </header>

      <div className="operational-baseline-claim">
        <strong>
          {language === "zh" ? "要验证的不是“更好的优化器”" : "The claim is not a better optimizer"}
        </strong>
        <p>{analysis.dynamic_comparison
          ? (language === "zh" ? analysis.finding : analysis.finding_en)
          : (language === "zh" ? "旧版三组结果仅比较静态方案与信息更新，未包含正常重规划对照；请重新规划生成四组公平实验。" : "Legacy three-row results lack the updated-input baseline. Rerun planning for a fair four-way comparison.")}</p>
      </div>

      <div className="operational-baseline-methods">
        {analysis.rows.map((row) => {
          const trials = row.fault_trials;
          return (
            <section className={row.id === "stormshift" ? "evidence" : ""} key={row.id}>
              <header>
                <span>
                  <small>{row.id === "stormshift" ? "EVIDENCE-AWARE" : row.id.toUpperCase()}</small>
                  <strong>{baselineLabel(row.id, language)}</strong>
                </span>
                <em>{Math.round(trials.safe_decision_rate * 100)}% SAFE · {trials.safe_abstentions} STOP</em>
              </header>
              <div className="operational-baseline-stats">
                <span>
                  <small>{language === "zh" ? "正常平均延误" : "NOMINAL DELAY"}</small>
                  <b>{row.nominal.mean_delay_min == null ? "—" : row.nominal.mean_delay_min.toFixed(2)}</b>
                  <i>min</i>
                </span>
                <span>
                  <small>{language === "zh" ? "故障世界安全" : "SAFE WORLDS"}</small>
                  <b>{trials.hard_passed_worlds + trials.safe_abstentions}/{trials.worlds}</b>
                </span>
                <span className={trials.unsafe_execution_worlds ? "unsafe" : "safe"}>
                  <small>{language === "zh" ? "失效方案" : "INVALID PLANS"}</small>
                  <b>{trials.unsafe_execution_worlds}</b>
                </span>
                <span>
                  <small>{language === "zh" ? "平均改动航班" : "MEAN CHANGES"}</small>
                  <b>{trials.mean_changed_flights.toFixed(1)}</b>
                </span>
              </div>
              <p className="operational-baseline-quality">
                {language === "zh" ? "可比较均值" : "Eligible mean delay"}: {auditNumber(trials.mean_delay_min)} min
                · n={trials.metric_eligible_worlds}/{trials.worlds}
              </p>
              <div className="operational-baseline-rate">
                <i style={{ width: `${Math.round(trials.safe_decision_rate * 100)}%` }} />
              </div>
            </section>
          );
        })}
      </div>

      <details>
        <summary>
          {language === "zh"
            ? "查看三种迟到信息，以及每种方法是否仍可执行"
            : "Inspect delayed facts and executable outcomes"}
        </summary>
        <div className="operational-fault-worlds">
          {analysis.worlds.map((world) => (
            <section key={world.id}>
              <header>
                <span>
                  <strong>{language === "zh" ? world.label : world.label_en}</strong>
                  <code>{world.changed_fact}</code>
                </span>
                <p>
                  <s>{String(world.stale_value)}</s>
                  <b>→</b>
                  <strong>{String(world.truth_value)}</strong>
                </p>
              </header>
              <div>
                {analysis.rows.map((row) => {
                  const outcome = world.outcomes[row.id];
                  if (!outcome) return null;
                  const statusClass = outcome.safe_abstention
                    ? "abstain"
                    : outcome.hard_constraints_passed ? "safe" : "unsafe";
                  return (
                    <span className={statusClass} key={row.id}>
                      <small>{baselineLabel(row.id, language)}</small>
                      <strong>{outcomeLabel(outcome.status, outcome.safe_abstention)}</strong>
                      <i>
                        {outcome.violations[0]
                          ?? (language === "zh"
                            ? `重新规划 ${outcome.changed_flights} 架，硬约束通过`
                            : `Replanned ${outcome.changed_flights}; hard constraints pass`)}
                      </i>
                    </span>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      </details>

      <footer>
        <ShieldCheck size={13} />
        <p>{language === "zh" ? analysis.disclaimer : analysis.disclaimer_en}</p>
        <code>{analysis.scope.commercial_products_compared} COMMERCIAL PRODUCTS COMPARED</code>
      </footer>
    </article>
  );
}

function DecisionLab({
  events,
  session,
  selectedPlanId,
  onSelect,
  onObserve,
  onPreference,
  onResetPreference,
  observing,
  onClose,
  language,
}: {
  session: SessionState;
  selectedPlanId: string;
  onSelect: (planId: string) => void;
  onObserve: (queryId: string) => void;
  onPreference: (leftPlanId: string, rightPlanId: string, preferredPlanId: string) => void;
  onResetPreference: () => void;
  observing: boolean;
  onClose: () => void;
  language: Language;
  events: ExecutionEvent[];
}) {
  const t = (key: CopyKey) => copy[language][key];
  const [technical, setTechnical] = useState(false);
  useEffect(() => { const close=(e:KeyboardEvent)=>{if(e.key==="Escape")onClose()};window.addEventListener("keydown",close);return()=>window.removeEventListener("keydown",close); }, [onClose]);
  const recommended = session.plans[0];
  const best = session.plans.find((plan) => plan.id === selectedPlanId) ?? recommended;
  const counterfactuals = session.decision_lab.counterfactuals ?? [];
  const comparisonRows = best && recommended && best.id !== recommended.id
    ? [{ name: language === "zh" ? "推荐膝点" : "Recommended knee", mean_delay: recommended.report.mean_delay_min }, { name: best.title, mean_delay: best.report.mean_delay_min }]
    : counterfactuals;
  const signed = (value: number) => `${value > 0 ? "+" : ""}${Math.round(value * 100) / 100}`;
  const sensitivity = session.decision_lab.sensitivity ?? [];
  const pareto = session.decision_lab.pareto;
  const robustness = session.decision_lab.robustness;
  const regret = session.decision_lab.regret;
  const bargaining = session.decision_lab.bargaining;
  const preferenceLearning = session.decision_lab.preference_learning;
  const decisionEvidence = candidateDecisionEvidence(session, best?.id ?? "");
  const agentArbitration = decisionEvidence.arbitration;
  const informationValue = decisionEvidence.informationValue;
  const contractStatusLabel: Record<string, string> = language === "zh"
    ? {
      aligned: "一致",
      needs_evidence: "需证据",
      resolved_watch: "已观测·监控",
      human_required: "需人工",
      blocked: "已阻断",
    }
    : {
      aligned: "ALIGNED",
      needs_evidence: "NEEDS EVIDENCE",
      resolved_watch: "OBSERVED · WATCH",
      human_required: "HUMAN",
      blocked: "BLOCKED",
    };
  const policy = session.decision_lab.policy;
  const policyRuntime = session.decision_lab.policy_runtime;
  const policyActionLabel: Record<string, string> = language === "zh"
    ? {
      global_replan: "全局滚动重规划",
      causal_local_repair: "因果局部修复",
      priority_replan: "硬优先级重规划",
      continue_and_monitor: "继续执行并监控",
    }
    : {
      global_replan: "Global rolling replan",
      causal_local_repair: "Causal local repair",
      priority_replan: "Hard-priority replan",
      continue_and_monitor: "Continue and monitor",
    };
  const infeasible = session.decision_lab.infeasible;
  const recourse = decisionEvidence.recourse;
  const actionDiff = decisionEvidence.actionDiff;
  const outcomeAudit = session.decision_lab.outcome_audit;
  const runtimeContract = session.decision_lab.runtime_contract ?? outcomeAudit?.runtime_contract;
  const shadowTwin = session.decision_lab.shadow_twin;
  const twinTrust = best
    ? session.decision_lab.twin_trust_by_plan?.[best.id] ?? session.decision_lab.twin_trust
    : session.decision_lab.twin_trust;
  const causalAttribution = session.decision_lab.causal_attribution;
  const attributionScale = Math.max(
    1,
    ...(causalAttribution?.factors.map((item) => Math.abs(item.risk_points)) ?? []),
  );
  const passengerRescue = best
    ? session.decision_lab.passenger_rescue_by_plan?.[best.id] ?? session.decision_lab.passenger_rescue
    : session.decision_lab.passenger_rescue;
  const contractRepair = best
    ? session.decision_lab.contract_repair_by_plan?.[best.id] ?? session.decision_lab.contract_repair
    : session.decision_lab.contract_repair;
  const mitigation = best
    ? session.decision_lab.mitigation_by_plan?.[best.id] ?? session.decision_lab.mitigation
    : session.decision_lab.mitigation;
  const eventIntegrity = session.decision_lab.event_integrity;
  const trustedState = session.decision_lab.trusted_state;
  const operationalBaseline = session.decision_lab.operational_baseline;
  const planCertificate = best
    ? session.decision_lab.plan_certificates?.[best.id] ?? session.decision_lab.plan_certificate
    : session.decision_lab.plan_certificate;
  const proofVerification = session.decision_lab.proof_carrying_verification?.plan_id === best?.id
    ? session.decision_lab.proof_carrying_verification
    : undefined;
  const falsification = best
    ? session.decision_lab.falsification_by_plan?.[best.id] ?? session.decision_lab.falsification
    : session.decision_lab.falsification;
  const integrityLabels: Record<string, string> = {
    session_created: language === "zh" ? "会话创建" : "Session",
    operator_command: language === "zh" ? "指挥输入" : "Intent",
    plan_generated: language === "zh" ? "方案生成" : "Plan",
    plan_certificate_verified: language === "zh" ? "携证计划验证" : "Plan proof verified",
    plan_certificate_rejected: language === "zh" ? "携证计划拒绝" : "Plan proof rejected",
    trusted_state_compiled: language === "zh" ? "可信状态编译" : "Trusted state",
    trusted_state_reconciled: language === "zh" ? "故障证据重建" : "Evidence reconciled",
    preference_feedback: language === "zh" ? "偏好反馈" : "Preference",
    plan_approved: language === "zh" ? "人工批准" : "Approval",
    execution_tick: language === "zh" ? "执行动作" : "Execution",
    runway_reopened: language === "zh" ? "跑道恢复" : "Runway reopened",
    ground_vehicle_failure: language === "zh" ? "地服故障" : "Incident",
    automatic_replan: language === "zh" ? "自动重规划" : "Auto replan",
    automatic_local_replan: language === "zh" ? "局部重规划" : "Local replan",
    automatic_policy_replan: language === "zh" ? "策略重规划" : "Policy replan",
    planning_infeasible: language === "zh" ? "安全停机" : "Safe stop",
    recovered: language === "zh" ? "恢复确认" : "Recovered",
  };

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section className={`decision-lab glass ${technical ? "" : "brief-mode"}`} role="dialog" aria-modal="true" aria-label={t("decisionLab")} onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <div><FlaskConical size={18} /><span><strong>{t("decisionLab")}</strong><small>{language==="zh"?"先看结论，需要时再展开":"Key findings first"}</small></span></div>
          {technical && <button onClick={()=>setTechnical(false)}>{language==="zh"?"返回简要说明":"Back to summary"}</button>}
          <button onClick={onClose} aria-label={t("close")}><X size={18} /></button>
        </header>
        {!technical ? <DecisionSummary session={session} plan={best} language={language} events={events} onTechnical={()=>setTechnical(true)} onObserve={onObserve} observing={observing} onSelect={onSelect}/> : <>{infeasible ? (
          <div className="infeasible-panel exact-core-panel">
            <header>
              <span><AlertTriangle size={22} /></span>
              <div>
                <small>CP-SAT ASSUMPTION CORE · SAFE STOP</small>
                <h3>{language === "zh" ? "当前约束组合不可行" : "Constraint set is infeasible"}</h3>
                <p>{infeasible.summary ?? infeasible.reasons?.[0]}</p>
              </div>
              <em>{infeasible.core_irreducible ? "IRREDUCIBLE CORE" : "DIAGNOSTIC"}</em>
            </header>
            <div className="core-proof-strip">
              <span>
                <small>{language === "zh" ? "冲突核心" : "CORE"}</small>
                <b>{infeasible.conflict_core?.length ?? 0}</b>
                <i>{language === "zh" ? "条约束" : "rules"}</i>
              </span>
              <strong>→</strong>
              <span>
                <small>{language === "zh" ? "安全规则放宽" : "SAFETY RELAXATION"}</small>
                <b>{infeasible.hard_safety_relaxations_allowed ?? 0}</b>
                <i>{language === "zh" ? "始终禁止" : "never allowed"}</i>
              </span>
              <strong>→</strong>
              <span>
                <small>{language === "zh" ? "求解证明" : "SOLVER PROOF"}</small>
                <b>{infeasible.solver_calls ?? 0}</b>
                <i>{language === "zh" ? "次确定性检查" : "deterministic checks"}</i>
              </span>
            </div>
            {!!infeasible.conflict_core?.length ? (
              <section className="conflict-core-list">
                {infeasible.conflict_core.map((item, index) => (
                  <article className={item.relaxable ? "soft" : "hard"} key={item.id}>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <div>
                      <small>
                        {item.relaxable
                          ? (language === "zh" ? "可修改软目标" : "SOFT TARGET")
                          : (language === "zh" ? "不可放宽安全事实" : "HARD SAFETY")}
                      </small>
                      <strong>{item.label}</strong>
                      <code>{item.rule}</code>
                      {!!item.affected.length && <i>{item.affected.join(" · ")}</i>}
                    </div>
                    <footer>
                      <ShieldCheck size={14} />
                      <span>
                        <small>{language === "zh" ? "安全处置" : "SAFE REMEDY"}</small>
                        <p>{item.remedy}</p>
                      </span>
                    </footer>
                  </article>
                ))}
              </section>
            ) : (
              <section className="legacy-infeasible">
                {infeasible.reasons?.map((item) => <p key={item}>{item}</p>)}
              </section>
            )}
            <footer className="core-method">
              <code>{infeasible.method ?? "safe diagnostic"}</code>
              <p>
                {language === "zh"
                  ? "删除核心中的任意一条约束，诊断镜像都会恢复可行；Agent 只能建议处置，不能越过安全闸门直接生成动作。"
                  : "Removing any remaining core member restores feasibility in the diagnostic mirror; agents cannot bypass the safety gate."}
              </p>
            </footer>
          </div>
        ) : best && (
          <div className="lab-grid">
            {trustedState && (
              <TrustedStatePanel state={trustedState} language={language} />
            )}
            {operationalBaseline && (
              <OperationalBaselinePanel analysis={operationalBaseline} language={language} />
            )}
            {actionDiff && (
              <ActionDiffStory
                diff={actionDiff}
                committedCount={undefined}
                slots={2}
                language={language}
                expanded
              />
            )}
            {planCertificate && (
              <article className={`proof-carrying-plan ${
                planCertificate.failed_obligations
                  ? "blocked"
                  : proofVerification?.valid
                    ? "verified"
                    : "issued"
              }`}>
                <header>
                  <span><ShieldCheck size={18} /></span>
                  <div>
                    <small>PROOF-CARRYING SCHEDULE · EVIDENCE → ACTION</small>
                    <strong>{language === "zh" ? "携证调度方案" : "Proof-carrying plan"}</strong>
                    <p>
                      {language === "zh"
                        ? "审批与自动切换前，重新核对可信输入、求解动作、硬约束、仿真和风险闸门。"
                        : "Trusted input, executable actions, hard constraints, simulation and risk gates are rebound before execution."}
                    </p>
                  </div>
                  <em>
                    {proofVerification
                      ? (proofVerification.valid ? "VERIFIED" : "REJECTED")
                      : `${planCertificate.passed_obligations}/${planCertificate.obligation_count} READY`}
                  </em>
                </header>
                <div className="proof-binding-strip">
                  <span>
                    <small>{language === "zh" ? "可信输入" : "TRUSTED INPUT"}</small>
                    <code>{planCertificate.input_digest.slice(0, 12)}</code>
                  </span>
                  <b>→</b>
                  <span>
                    <small>{language === "zh" ? "可执行动作" : "EXECUTABLE"}</small>
                    <code>{planCertificate.executable_fingerprint.slice(0, 12)}</code>
                  </span>
                  <b>→</b>
                  <span>
                    <small>{language === "zh" ? "执行闸门" : "EXECUTION GATE"}</small>
                    <code>
                      {proofVerification
                        ? `${proofVerification.checks.filter((item) => item.passed).length}/${proofVerification.checks.length}`
                        : planCertificate.status.replaceAll("_", " ")}
                    </code>
                  </span>
                </div>
                <details>
                  <summary>
                    {language === "zh"
                      ? `查看 ${planCertificate.obligation_count} 项机器校验证明`
                      : `Inspect ${planCertificate.obligation_count} machine-checkable obligations`}
                  </summary>
                  <div className="proof-obligations">
                    {planCertificate.obligations.map((obligation) => (
                      <span className={obligation.satisfied ? obligation.status : "fail"} key={obligation.id}>
                        {obligation.satisfied ? <Check size={12} /> : <X size={12} />}
                        <span>
                          <strong>{obligation.label}</strong>
                          <small>{obligation.evidence}</small>
                        </span>
                      </span>
                    ))}
                  </div>
                </details>
                <footer>
                  <code>{planCertificate.certificate_id} · {planCertificate.certificate_hash.slice(0, 16)}…</code>
                  <p>
                    {language === "zh"
                      ? "核验证据、调度动作与执行记录的一致性。"
                      : "Checks consistency across evidence, scheduling actions and execution records."}
                  </p>
                </footer>
              </article>
            )}
            {falsification && (
              <FalsificationPanel analysis={falsification} language={language} />
            )}
            {eventIntegrity && (
              <article className={`event-integrity-spine ${eventIntegrity.valid ? "verified" : "broken"}`}>
                <header>
                  <span>{eventIntegrity.valid ? <ShieldCheck size={18} /> : <AlertTriangle size={18} />}</span>
                  <div>
                    <small>TAMPER-EVIDENT EVENT SOURCING · SHA-256</small>
                    <strong>{language === "zh" ? "演练证据链" : "Exercise evidence chain"}</strong>
                    <p>
                      {eventIntegrity.valid
                        ? (language === "zh"
                          ? `${eventIntegrity.event_count} 条事件从创建到当前状态连续可验证`
                          : `${eventIntegrity.event_count} events verify continuously from genesis`)
                        : (language === "zh"
                          ? `第 ${eventIntegrity.first_invalid_sequence} 条记录校验失败`
                          : `Verification fails at event ${eventIntegrity.first_invalid_sequence}`)}
                    </p>
                  </div>
                  <em>{eventIntegrity.status.toUpperCase()}</em>
                </header>
                <div className="integrity-chain" aria-label={language === "zh" ? "事件哈希链" : "Event hash chain"}>
                  {eventIntegrity.links.map((link) => (
                    <span className={link.valid ? "" : "invalid"} key={link.sequence}>
                      <small>T+{String(link.sim_minute).padStart(2, "0")}</small>
                      <b>
                        {integrityLabels[link.type] ?? link.type.replaceAll("_", " ")}
                        {link.count > 1 && <em>×{link.count}</em>}
                      </b>
                      <i>#{link.sequence} · {link.hash_prefix}</i>
                    </span>
                  ))}
                </div>
                <footer>
                  <code title={eventIntegrity.head_hash}>HEAD · {eventIntegrity.head_hash.slice(0, 18)}…</code>
                  <p>{eventIntegrity.caveat}</p>
                </footer>
              </article>
            )}

            {outcomeAudit && (
              <article className={`outcome-audit-evidence ${outcomeAudit.status}`}>
                <header>
                  <span><ShieldCheck size={18} /></span>
                  <div>
                    <small>POST-EXECUTION ACCEPTANCE · SCOPED LEDGER</small>
                    <strong>{language === "zh" ? "最终验收证据" : "Final acceptance evidence"}</strong>
                    <p>{outcomeAudit.scope_note}</p>
                  </div>
                  <em>
                    {outcomeAudit.status === "passed"
                      ? (language === "zh" ? "全部通过" : "PASSED")
                      : outcomeAudit.status === "passed_with_watch"
                        ? (language === "zh" ? "硬约束通过 · 继续监控" : "PASS · WATCH")
                        : outcomeAudit.status === "human_required"
                          ? (language === "zh" ? "需要人工复核" : "HUMAN REVIEW")
                          : (language === "zh" ? "未通过" : "FAILED")}
                  </em>
                </header>
                <div className="outcome-evidence-grid">
                  <span>
                    <small>{language === "zh" ? "硬约束证明" : "Hard-constraint proof"}</small>
                    <b>{outcomeAudit.hard_constraints.passed}/{outcomeAudit.hard_constraints.total}</b>
                    <i>{outcomeAudit.hard_constraints.all_passed ? "PASS" : "FAIL"}</i>
                  </span>
                  <span>
                    <small>{language === "zh" ? "本轮预计按时转机" : "Scoped on-time connections"}</small>
                    <b>{auditNumber(auditPassengerMetric(outcomeAudit.passengers, "on_time"), 2)}/{outcomeAudit.passengers.decision_scope}</b>
                    <i>{auditRate(outcomeAudit.passengers.predicted_on_time_rate)}</i>
                  </span>
                  <span>
                    <small>{language === "zh" ? "平均预计错失转机" : "Mean expected missed"}</small>
                    <b>{auditNumber(auditPassengerMetric(outcomeAudit.passengers, "missed"), 2)}<i> pax</i></b>
                    <i>MONTE CARLO MEAN</i>
                  </span>
                  <span className="watch">
                    <small>{language === "zh" ? "尾部观察名单" : "Tail watchlist"}</small>
                    <b>{outcomeAudit.passengers.tail_risk_exposed}<i> pax</i></b>
                    <i>{language === "zh" ? "重叠口径 · 不相加" : "overlapping view"}</i>
                  </span>
                  <span>
                    <small>{language === "zh" ? "燃油越界 / 平均资源排队" : "Fuel / mean resource queues"}</small>
                    <b>{outcomeAudit.operations.fuel_breaches}<i> / {outcomeAudit.operations.remaining_queue_conflicts}</i></b>
                    <i>{language === "zh" ? "安全闸门 / 额外等待" : "safety / extra waits"}</i>
                  </span>
                </div>
                <div className="outcome-scope-ledger">
                  <span><small>{language === "zh" ? "场景转机旅客" : "SCENARIO"}</small><b>{outcomeAudit.passengers.scenario_total}</b></span>
                  <i>−</i>
                  <span><small>{language === "zh" ? "范围外" : "OUT OF SCOPE"}</small><b>{outcomeAudit.passengers.out_of_scope}</b></span>
                  <i>=</i>
                  <span><small>{language === "zh" ? "本轮范围" : "IN SCOPE"}</small><b>{outcomeAudit.passengers.decision_scope}</b></span>
                  <i>=</i>
                  <span className="good"><small>{language === "zh" ? "预计按时" : "ON TIME"}</small><b>{auditNumber(auditPassengerMetric(outcomeAudit.passengers, "on_time"), 2)}</b></span>
                  <i>+</i>
                  <span className="watch"><small>{language === "zh" ? "平均错失" : "MEAN MISSED"}</small><b>{auditNumber(auditPassengerMetric(outcomeAudit.passengers, "missed"), 2)}</b></span>
                  <em><Layers3 size={12} />{language === "zh"
                    ? `尾部观察 ${outcomeAudit.passengers.tail_risk_exposed} 人是同一批旅客的另一种概率视角，不能加进等式`
                    : `Tail watch ${outcomeAudit.passengers.tail_risk_exposed} is an overlapping probabilistic view`}</em>
                </div>
                {outcomeAudit.outcome_contract && (
                  <section className={`outcome-contract ${outcomeAudit.outcome_contract.status}`}>
                    <header>
                      <div>
                        <small>PROBABILISTIC OPERATIONS CONTRACT</small>
                        <strong>{language === "zh" ? "两层验收：安全规则 + 运营目标" : "Two-layer acceptance contract"}</strong>
                        <p>{outcomeAudit.outcome_contract.summary}</p>
                      </div>
                      <em>{outcomeAudit.outcome_contract.passed}/{outcomeAudit.outcome_contract.total} PASS</em>
                    </header>
                    <div className="outcome-contract-rows">
                      {outcomeAudit.outcome_contract.rows.map((row) => (
                        <span key={row.id} className={`${row.passed == null ? "unknown" : row.passed ? "pass" : "fail"} ${row.layer}`}>
                          <i>{row.passed ? <Check size={12} /> : <X size={12} />}</i>
                          <div>
                            <small>{row.source === "operator"
                              ? (language === "zh" ? "操作员声明" : "OPERATOR")
                              : row.source === "system_safety"
                                ? (language === "zh" ? "系统不可放宽" : "SYSTEM SAFETY")
                                : (language === "zh" ? "演练默认值" : "DEMO DEFAULT")}</small>
                            <strong>{row.label}</strong>
                          </div>
                          <b>{auditNumber(row.actual)}{row.unit}</b>
                          <em>{row.comparison} {auditNumber(row.target)}{row.unit} · {auditCheckLabel(row.status, row.passed, language)}</em>
                        </span>
                      ))}
                    </div>
                    <footer>{outcomeAudit.outcome_contract.semantics}</footer>
                  </section>
                )}
                <div className="outcome-proof-rows">
                  {outcomeAudit.hard_constraints.rows.map((item) => (
                    <span key={item.label} className={item.passed ? "pass" : "fail"}>
                      {item.passed ? <Check size={12} /> : <X size={12} />}{item.label}
                    </span>
                  ))}
                  {outcomeAudit.operator_checks.map((item) => (
                    <span key={item.id} className={item.passed == null ? "unknown" : item.passed ? "pass" : "fail"}>
                      {item.passed === true ? <Check size={12} /> : item.passed === false ? <X size={12} /> : <History size={12} />}{item.label}
                      <i>{auditCheckLabel(item.status, item.passed, language)} · v{item.intent_version ?? "—"}</i>
                      {item.actual != null && <i>{auditNumber(item.actual)}{item.unit ?? ""}</i>}
                      {item.reason && <i>{item.reason}</i>}
                    </span>
                  ))}
                </div>
                <details className="metric-glossary">
                  <summary>
                    <span><ChevronDown size={12} />{language === "zh" ? "这些数字怎么算？" : "How are these metrics computed?"}</span>
                    <small>{language === "zh" ? "均值、尾部风险与场景范围是三种不同口径" : "Mean, tail risk, and scenario scope are different views"}</small>
                  </summary>
                  <div>
                    <span>
                      <b>RULES</b>
                      <p>{language === "zh" ? `${outcomeAudit.hard_constraints.passed}/${outcomeAudit.hard_constraints.total} 表示规则类别通过数，不是航班数量；每条具体证明列在上方。` : `${outcomeAudit.hard_constraints.passed}/${outcomeAudit.hard_constraints.total} counts rule categories, not flights; each proof is listed above.`}</p>
                    </span>
                    <span>
                      <b>MEAN</b>
                      <p>{language === "zh" ? `${auditNumber(auditPassengerMetric(outcomeAudit.passengers, "missed"), 2)} 人是 ${best.report.runs} 个固定种子仿真世界中的平均错失衔接人数（保留小数；旧记录缺原始均值时沿用取整值）；预计按时人数 = ${outcomeAudit.passengers.decision_scope} − ${auditNumber(auditPassengerMetric(outcomeAudit.passengers, "missed"), 2)}。` : `${auditNumber(auditPassengerMetric(outcomeAudit.passengers, "missed"), 2)} is the mean missed-connection count (fractional when available; legacy records may be rounded) over ${best.report.runs} seeded worlds; predicted on-time equals scope minus mean missed.`}</p>
                    </span>
                    <span>
                      <b>TAIL 90%</b>
                      <p>{language === "zh" ? `${outcomeAudit.passengers.tail_risk_exposed} 人位于“90% 到达时间上界超过转机缓冲”的航班组中，是需要重点监控的暴露人数；它与平均错失人数重叠，不能相加。` : `${outcomeAudit.passengers.tail_risk_exposed} passengers are on flights whose 90% arrival upper bound exceeds the transfer buffer; this overlaps with mean missed and must not be added to it.`}</p>
                    </span>
                    <span>
                      <b>FUEL</b>
                      <p>{language === "zh" ? `${outcomeAudit.operations.fuel_breaches} 架触发燃油风险升级阈值，检查依据为 90% 落地时间上界与预测概率。` : `${outcomeAudit.operations.fuel_breaches} flights trigger the fuel escalation threshold, checked against the 90% landing-time bound and predicted probability.`}</p>
                    </span>
                    <span>
                      <b>QUEUE</b>
                      <p>{language === "zh" ? `${outcomeAudit.operations.remaining_queue_conflicts} 是 ${best.report.runs} 次仿真中跑道或机位出现额外等待的平均次数；资源按队列依次使用。` : `${outcomeAudit.operations.remaining_queue_conflicts} is the mean extra runway/gate queue count across ${best.report.runs} simulations. Resources are used in queue order.`}</p>
                    </span>
                    <span>
                      <b>SCOPE</b>
                      <p>{language === "zh" ? `场景共有 ${outcomeAudit.passengers.scenario_total} 名转机旅客，本轮局部重规划覆盖 ${outcomeAudit.passengers.decision_scope} 人；其余 ${outcomeAudit.passengers.out_of_scope} 人没有被假定为按时或超时。` : `The scenario has ${outcomeAudit.passengers.scenario_total} transfer passengers; this local replan covers ${outcomeAudit.passengers.decision_scope}, leaving ${outcomeAudit.passengers.out_of_scope} unclassified.`}</p>
                    </span>
                  </div>
                </details>
              </article>
            )}
            {contractRepair && (
              <article className={`contract-repair-section ${contractRepair.status}`}>
                <header>
                  <span><Wrench size={18} /></span>
                  <div>
                    <small>ELASTIC CONSTRAINT REPAIR · NO SAFETY RELAXATION</small>
                    <strong>{language === "zh" ? "目标契约修复与反提案" : "Outcome contract reconciliation"}</strong>
                    <p>{contractRepair.recommendation}</p>
                  </div>
                  <em>{contractRepair.status === "contract_met"
                    ? (language === "zh" ? "无需修复" : "CONTRACT MET")
                    : contractRepair.status === "evidence_required"
                      ? (language === "zh" ? "待补证据" : "EVIDENCE REQUIRED")
                    : contractRepair.status === "switch_plan"
                      ? (language === "zh" ? "建议换方案" : "SWITCH PLAN")
                      : contractRepair.status === "safety_block"
                        ? (language === "zh" ? "安全阻断" : "SAFETY BLOCK")
                        : (language === "zh" ? "最小反提案" : "COUNTEROFFER")}</em>
                </header>
                <div className="contract-repair-flow">
                  <span><small>{language === "zh" ? "保持硬安全" : "HARD SAFETY"}</small><b>{contractRepair.hard_safety_relaxations}</b><i>{language === "zh" ? "项放宽" : "relaxations"}</i></span>
                  <strong>→</strong>
                  <span><small>{contractRepair.status === "evidence_required"
                    ? (language === "zh" ? "尚未核实" : "UNVERIFIED")
                    : (language === "zh" ? "当前未达标" : "UNMET TARGETS")}</small><b>{contractRepair.status === "evidence_required"
                    ? contractRepair.unknown_target_ids?.length ?? "—" : contractRepair.unmet_target_ids.length}</b><i>{language === "zh" ? "项要求" : "requirements"}</i></span>
                  <strong>→</strong>
                  <span><small>{language === "zh" ? "需要授权" : "AUTHORITY"}</small><b>HITL</b><i>{language === "zh" ? "人工确认" : "human approval"}</i></span>
                </div>
                <div className="contract-alternatives">
                  {contractRepair.alternatives.slice(0, 3).map((item) => (
                    <button
                      key={item.plan_id}
                      className={`${item.selected ? "selected" : ""} ${item.plan_id === contractRepair.recommended_plan_id ? "recommended" : ""}`}
                      onClick={() => onSelect(item.plan_id)}
                      disabled={item.plan_id === best.id || item.evidence_complete === false || !item.hard_safe}
                    >
                      <span><small>{item.frontier ? "ELASTIC FRONTIER" : item.profile.toUpperCase()}</small><strong>{planTitleLabel(item.title, item.profile, language)}</strong></span>
                      <b>{item.evidence_complete === false
                        ? (language === "zh" ? "未核实" : "Unverified")
                        : item.failed_targets}<small>{item.evidence_complete === false
                          ? "" : language === "zh" ? " 项未达标" : " unmet"}</small></b>
                      <em>{item.evidence_complete === false
                        ? (item.comparable_scope === false
                          ? (language === "zh" ? "旅客范围不同" : "Different passenger scope")
                          : (language === "zh" ? "缺少完整证据" : "Incomplete evidence"))
                        : language === "zh" ? `偏离 ${item.normalized_violation.toFixed(3)}` : `gap ${item.normalized_violation.toFixed(3)}`}</em>
                      <i>{language === "zh" ? `改动 ${item.schedule_changes} 架` : `${item.schedule_changes} changes`}</i>
                    </button>
                  ))}
                </div>
                {contractRepair.repairs.length > 0 && (
                  <div className="contract-amendments">
                    <small>{language === "zh" ? "当前方案的最小目标修订（必须确认）" : "Minimum target amendments · confirmation required"}</small>
                    <div>
                      {contractRepair.repairs.map((item) => (
                        <span key={item.target_id}>
                          <i>{item.source === "operator" ? (language === "zh" ? "操作员" : "OPERATOR") : (language === "zh" ? "演练默认" : "DEFAULT")}</i>
                          <strong>{item.label}</strong>
                          <b>{item.from_target}{item.unit} → {item.to_target}{item.unit}</b>
                        </span>
                      ))}
                    </div>
                    {contractRepair.suggested_command && <code>{contractRepair.suggested_command}</code>}
                  </div>
                )}
                <footer>
                  <span><ShieldCheck size={12} />{contractRepair.proof}</span>
                  <p>{contractRepair.tradeoff}</p>
                </footer>
              </article>
            )}
            {mitigation && (
              <article className={`mitigation-section ${mitigation.status}`}>
                <header>
                  <span><Activity size={18} /></span>
                  <div>
                    <small>SECOND-STAGE INTERVENTION · FROZEN AIRCRAFT PLAN</small>
                    <strong>{language === "zh" ? "二阶段运行干预" : "Second-stage operations intervention"}</strong>
                    <p>{language === "zh" ? mitigation.summary : mitigation.summary_en}</p>
                  </div>
                  <em>
                    {mitigation.approved
                      ? (language === "zh" ? "已随方案批准" : "APPROVED")
                      : mitigation.status === "target_met"
                        ? (language === "zh" ? "保护线已达成" : "TARGET MET")
                      : mitigation.status === "partial"
                        ? (language === "zh" ? "部分缓解" : "PARTIAL")
                        : (language === "zh" ? "无需加资源" : "NOT NEEDED")}
                  </em>
                </header>
                <div className="mitigation-flow">
                  <span className="before">
                    <small>{language === "zh" ? "原方案" : "BASELINE"}</small>
                    <strong>{mitigation.baseline.predicted_missed}<i> pax</i></strong>
                    <em>{language === "zh" ? "预计超时" : "expected missed"}</em>
                    <b>TAIL {mitigation.baseline.tail_risk_exposed}</b>
                  </span>
                  <i>→</i>
                  <span className="portfolio">
                    <small>CP-SAT PORTFOLIO</small>
                    <strong>{mitigation.selected_actions.length}<i>{language === "zh" ? " 项" : " actions"}</i></strong>
                    <em>{language === "zh" ? "最小资源组合" : "minimum portfolio"}</em>
                    <b>{mitigation.candidate_action_count} CANDIDATES</b>
                  </span>
                  <i>→</i>
                  <span className="after">
                    <small>{language === "zh" ? "成对仿真复验" : "PAIRED VALIDATION"}</small>
                    <strong>{mitigation.mitigated.predicted_missed}<i> pax</i></strong>
                    <em>{language === "zh" ? "预计超时" : "expected missed"}</em>
                    <b>TAIL {mitigation.mitigated.tail_risk_exposed}</b>
                  </span>
                  <span className="target">
                    <small>{mitigation.targets.source === "operator"
                      ? (language === "zh" ? "操作员目标" : "OPERATOR TARGET")
                      : (language === "zh" ? "主动保护线" : "PROACTIVE GUARDRAIL")}</small>
                    <strong>≥{Number((mitigation.targets.minimum_on_time_rate * 100).toFixed(1))}%</strong>
                    <em>{language === "zh" ? "按时衔接" : "on-time connection"}</em>
                    <b>TAIL ≤ {Math.round(mitigation.targets.maximum_tail_exposure_rate * 100)}%</b>
                  </span>
                </div>
                <div className="mitigation-resources">
                  {mitigation.resources.map((resource) => (
                    <span className={resource.used ? "used" : ""} key={resource.id}>
                      <small>{language === "zh" ? resource.label : resource.label_en}</small>
                      <b>{resource.used}<i> / {resource.budget}</i></b>
                    </span>
                  ))}
                </div>
                {mitigation.selected_actions.length > 0 && (
                  <div className="mitigation-actions">
                    {mitigation.selected_actions.map((action, index) => (
                      <span key={action.id}>
                        <i>{index + 1}</i>
                        <div>
                          <small>{action.kind.replaceAll("_", " ").toUpperCase()}</small>
                          <strong>{action.flight_id} · {language === "zh" ? action.label : action.label_en}</strong>
                        </div>
                        <em>+{action.bonus_min}<small> min buffer</small></em>
                        <b>−{action.estimated_saved_passengers}<small> pax est.</small></b>
                      </span>
                    ))}
                  </div>
                )}
                <div className="mitigation-proof">
                  <span className={mitigation.proof.assignments_unchanged ? "pass" : "fail"}>
                    {mitigation.proof.assignments_unchanged ? <Check size={11} /> : <X size={11} />}
                    {language === "zh" ? "航班调度未改变" : "Aircraft plan unchanged"}
                  </span>
                  <span className={mitigation.proof.operational_metrics_unchanged ? "pass" : "fail"}>
                    {mitigation.proof.operational_metrics_unchanged ? <Check size={11} /> : <X size={11} />}
                    {language === "zh" ? "运行指标隔离" : "Operational metrics isolated"}
                  </span>
                  <span><ShieldCheck size={11} />{mitigation.proof.solver_status}</span>
                  <em>{mitigation.proof.validation_runs} {language === "zh" ? "个同种子世界" : "paired worlds"} · SHA {mitigation.proof.assignment_fingerprint_after.slice(0, 10)}</em>
                </div>
                <footer>{mitigation.caveat}</footer>
              </article>
            )}
            {passengerRescue && (
              <article className={"passenger-rescue-section " + passengerRescue.status}>
                <header>
                  <span><Users size={18} /></span>
                  <div>
                    <small>PASSENGER CONTINUITY · ROBUST MIN-COST FLOW</small>
                    <strong>{language === "zh" ? "转机旅客挽救计划" : "Passenger continuity rescue"}</strong>
                    <p>{passengerRescue.summary}</p>
                  </div>
                  <em>
                    {passengerRescue.status === "ready"
                      ? (language === "zh" ? "容量已锁定" : "CAPACITY READY")
                      : passengerRescue.status === "capacity_shortfall"
                        ? (language === "zh" ? "仍有容量缺口" : "CAPACITY SHORTFALL")
                        : (language === "zh" ? "无需启动" : "NOT NEEDED")}
                  </em>
                </header>
                <div className="rescue-ledger">
                  <span className="risk">
                    <small>{language === "zh" ? "预计错失" : "Expected missed"}</small>
                    <b>{passengerRescue.at_risk_passengers}<i> pax</i></b>
                  </span>
                  <i>→</i>
                  <span className="rescued">
                    <small>{language === "zh" ? "已找到同日替代" : "Same-day protected"}</small>
                    <b>{passengerRescue.reaccommodated_passengers}<i> pax</i></b>
                  </span>
                  <span className={passengerRescue.remaining_stranded ? "stranded" : "clear"}>
                    <small>{language === "zh" ? "仍需人工处置" : "Human-care queue"}</small>
                    <b>{passengerRescue.remaining_stranded}<i> pax</i></b>
                  </span>
                  <span>
                    <small>{language === "zh" ? "P90 额外等待" : "P90 extra wait"}</small>
                    <b>{passengerRescue.p90_extra_wait_min}<i> min</i></b>
                  </span>
                  <span>
                    <small>{language === "zh" ? "尾部候补监控" : "Tail watchlist"}</small>
                    <b>{passengerRescue.tail_watch_only}<i> pax</i></b>
                  </span>
                </div>
                {passengerRescue.flows.length > 0 && (
                  <div className="rescue-flows">
                    {passengerRescue.flows.slice(0, 6).map((flow) => (
                      <span key={flow.from_flight + flow.to_option}>
                        <i>{flow.passengers}</i>
                        <div>
                          <strong>{flow.from_flight}<b>→</b>{flow.to_option}</strong>
                          <small>{flow.destination} · {flow.label}</small>
                        </div>
                        <em>T+{flow.departure_minute}<small>WAIT {flow.robust_wait_min}m</small></em>
                      </span>
                    ))}
                  </div>
                )}
                <div className="rescue-proof">
                  <span className={passengerRescue.constraints.flow_conservation ? "pass" : "fail"}>
                    {passengerRescue.constraints.flow_conservation ? <Check size={11} /> : <X size={11} />}{language === "zh" ? "旅客流守恒" : "Flow conserved"}
                  </span>
                  <span className={passengerRescue.constraints.seat_capacity ? "pass" : "fail"}>
                    {passengerRescue.constraints.seat_capacity ? <Check size={11} /> : <X size={11} />}{language === "zh" ? "未虚构座位" : "Seat capacity"}
                  </span>
                  <span className={passengerRescue.constraints.robust_connection_window ? "pass" : "fail"}>
                    {passengerRescue.constraints.robust_connection_window ? <Check size={11} /> : <X size={11} />}{language === "zh" ? "满足 90% 到达上界 + 12 分钟" : "90% arrival bound + 12m"}
                  </span>
                  <em>{language === "zh" ? "演练处置成本" : "Exercise intervention cost"} {passengerRescue.estimated_intervention_cost} units</em>
                </div>
                {passengerRescue.options.length > 0 && (
                  <details className="rescue-inventory">
                    <summary>
                      <span><ChevronDown size={12} />{language === "zh" ? "查看合成后续航班与座位证明" : "Inspect synthetic onward inventory"}</span>
                      <small>{passengerRescue.options.reduce((sum, item) => sum + item.allocated, 0)} / {passengerRescue.options.reduce((sum, item) => sum + item.capacity, 0)} seats used</small>
                    </summary>
                    <div>
                      {passengerRescue.options.map((option) => (
                        <span key={option.id}>
                          <strong>{option.id}<i>{option.route}</i></strong>
                          <small>T+{option.departure_minute} · {option.label}</small>
                          <em>{option.allocated}/{option.capacity}</em>
                        </span>
                      ))}
                    </div>
                  </details>
                )}
                <footer>
                  <span>{passengerRescue.source}</span>
                  <p>{passengerRescue.caveat}</p>
                </footer>
              </article>
            )}

            {runtimeContract && (
              <article className={`runtime-contract-section ${runtimeContract.status}`}>
                <header>
                  <span><History size={18} /></span>
                  <div>
                    <small>BOUNDED EVENT-TIME RUNTIME VERIFICATION</small>
                    <strong>{language === "zh" ? "运行时安全契约" : "Runtime safety contract"}</strong>
                    <p>{runtimeContract.summary}</p>
                  </div>
                  <em>{runtimeContract.passed}/{runtimeContract.total} PASS</em>
                </header>
                <div className="runtime-contract-timeline">
                  {runtimeContract.timeline.map((event) => (
                    <span key={`${event.sequence}-${event.type}`} className={event.type.includes("failure") ? "incident" : event.type.includes("replan") ? "replan" : event.type === "recovered" ? "recovered" : ""}>
                      <i />
                      <small>T+{event.minute}</small>
                      <strong>{event.label}</strong>
                    </span>
                  ))}
                </div>
                {runtimeContract.execution_envelope && (
                  <details className={`runtime-envelope-detail ${runtimeContract.execution_envelope.passed ? "pass" : "fail"}`}>
                    <summary>
                      <span>{language === "zh" ? "逐航班动作证据" : "Per-flight action evidence"}</span>
                      <em>{runtimeContract.execution_envelope.checked_actions} ACTIONS · {runtimeContract.execution_envelope.violations.length} VIOLATIONS</em>
                    </summary>
                    {Object.values(runtimeContract.execution_envelope.runway_windows).some((windows) => windows.length > 0) && (
                      <div className="runtime-runway-windows">
                        {Object.entries(runtimeContract.execution_envelope.runway_windows).flatMap(([runwayId, windows]) => windows.map((window, index) => (
                          <span key={`${runwayId}-${window.closed_from}-${index}`}>
                            <small>{language === "zh" ? "事件溯源关闭窗口" : "EVENT-SOURCED CLOSURE"}</small>
                            <strong>RWY {runwayId}</strong>
                            <code>T+{window.closed_from} {language === "zh" ? "关闭" : "CLOSED"} → {window.reopened_at == null ? (language === "zh" ? "无开放时刻" : "NO ETA") : `T+${window.reopened_at} ${language === "zh" ? "开放" : "OPEN"}`}</code>
                          </span>
                        )))}
                      </div>
                    )}
                    <div className="runtime-action-checks">
                      {runtimeContract.execution_envelope.checks.map((check, index) => (
                        <span key={`${check.flight_id}-${check.minute}-${index}`} className={check.passed ? "pass" : "fail"}>
                          {check.passed ? <Check size={12} /> : <X size={12} />}
                          <strong>{check.flight_id || "UNKNOWN"}</strong>
                          <code>T+{check.minute} · RWY {check.runway || "?"} → {check.gate || "?"}</code>
                          <small>{check.schedule_revision === "automatic_replan" ? "REVISED" : "APPROVED"} · {check.reasons.length ? check.reasons.join(" / ") : (language === "zh" ? "资源与版本核验通过" : "Resource and revision checks passed")}</small>
                        </span>
                      ))}
                    </div>
                  </details>
                )}
                <div className="runtime-contract-rules">
                  {runtimeContract.rules.map((rule) => (
                    <span key={rule.id} className={rule.passed ? "pass" : "fail"}>
                      {rule.passed ? <Check size={13} /> : <X size={13} />}
                      <strong>{rule.label}</strong>
                      <code>{rule.formula}</code>
                      <small>{rule.evidence}</small>
                    </span>
                  ))}
                </div>
                <footer>{runtimeContract.caveat}</footer>
              </article>
            )}
            {shadowTwin && (
              <article className={"shadow-twin-section " + shadowTwin.status}>
                <header>
                  <span><Copy size={18} /></span>
                  <div>
                    <small>PAIRED SHADOW EVALUATION · COMMON RANDOM NUMBERS</small>
                    <strong>{language === "zh" ? "重规划时的影子方案评估" : "Shadow evaluation at replanning"}</strong>
                    <p>{shadowTwin.summary}</p>
                    <p>{language === "zh" ? "两份方案的评估对照。已采纳的安排见批准事件与执行记录。" : "Compare the two evaluated plans. Approval events and execution records show which plan was adopted."}</p>
                  </div>
                  <em>
                    {shadowTwin.status === "mandatory_switch"
                      ? (language === "zh" ? "必须切换" : "MANDATORY SWITCH")
                      : shadowTwin.status === "evidence_switch"
                        ? (language === "zh" ? "证据支持切换" : "EVIDENCE SWITCH")
                        : shadowTwin.status === "keep_incumbent"
                          ? (language === "zh" ? "保持原方案" : "KEEP INCUMBENT")
                          : (language === "zh" ? "安全停机" : "SAFE STOP")}
                  </em>
                </header>
                {shadowTwin.incumbent && shadowTwin.challenger && shadowTwin.paired_gain && (
                  <div className="shadow-twin-duel">
                    <span className="incumbent">
                      <small>{language === "zh" ? "评估时的原批准方案" : "INCUMBENT AT EVALUATION"}</small>
                      <strong>{planTitleLabel(shadowTwin.incumbent.title, undefined, language)}</strong>
                      <b>{shadowTwin.incumbent.mean_loss_points}<i> risk pts</i></b>
                      <p>{shadowTwin.incumbent.mean_delay_min}m · {shadowTwin.incumbent.mean_missed_connections} pax</p>
                    </span>
                    <span className="paired-evidence">
                      <small>{language === "zh" ? "同随机世界" : "PAIRED WORLDS"} × {shadowTwin.runs}</small>
                      <strong>{signed(shadowTwin.paired_gain.mean_points)}<i>{language === "zh" ? " 软目标净收益" : " soft gain"}</i></strong>
                      <code>95% CI [{signed(shadowTwin.paired_gain.ci95_low)}, {signed(shadowTwin.paired_gain.ci95_high)}]</code>
                      <p>{language === "zh" ? "切换成本" : "switch cost"} {shadowTwin.switch_cost_points} pts</p>
                    </span>
                    <span className="challenger">
                      <small>{language === "zh" ? "当时比较的影子候选" : "CHALLENGER AT EVALUATION"}</small>
                      <strong>{planTitleLabel(shadowTwin.challenger.title, undefined, language)}</strong>
                      <b>{shadowTwin.challenger.mean_loss_points}<i> risk pts</i></b>
                      <p>{shadowTwin.challenger.mean_delay_min}m · {shadowTwin.challenger.mean_missed_connections} pax</p>
                    </span>
                  </div>
                )}
                {shadowTwin.hard_gate && (
                  <div className="shadow-twin-gates">
                    <span className={shadowTwin.hard_gate.incumbent_operationally_valid ? "pass" : "fail"}>
                      {shadowTwin.hard_gate.incumbent_operationally_valid ? <Check size={12} /> : <X size={12} />}
                      {shadowTwin.hard_gate.incumbent_operationally_valid
                        ? (language === "zh" ? "原方案事件后仍可行" : "Incumbent feasible after event")
                        : (language === "zh" ? "原方案事件后失效" : "Incumbent invalid after event")}
                    </span>
                    <span className={shadowTwin.hard_gate.challenger_valid ? "pass" : "fail"}>
                      {shadowTwin.hard_gate.challenger_valid ? <Check size={12} /> : <X size={12} />}
                      {shadowTwin.hard_gate.challenger_valid
                        ? (language === "zh" ? "影子方案硬约束通过" : "Challenger hard-safe")
                        : (language === "zh" ? "影子方案硬约束未通过" : "Challenger failed hard gate")}
                    </span>
                    <span><Activity size={12} />{shadowTwin.scope?.common_flights ?? 0} flights · {shadowTwin.scope?.passengers ?? 0} pax</span>
                  </div>
                )}
                {shadowTwin.reason && <p className="shadow-twin-reason">{shadowTwin.reason}</p>}
                <footer>{shadowTwin.caveat}</footer>
              </article>
            )}
            <article className="lab-explanation">
              <span className="section-kicker">{best.rank === 1 ? "COORDINATOR RECOMMENDATION" : "CANDIDATE EVIDENCE · COMPARING"}</span>
              <h2>{planTitleLabel(best.title, best.profile, language)}</h2>
              <p>{best.explanation}</p>
              {best.rank !== 1 && (
                <div className={`candidate-delta ${best.pareto_optimal ? "" : "dominated"}`}>
                  <strong>{language === "zh" ? "相对推荐膝点" : "Versus recommended knee"}</strong>
                  <span>ΔP90 <b>{signed(best.report.p90_delay_min - (recommended?.report.p90_delay_min ?? best.report.p90_delay_min))}m</b></span>
                  <span>{language === "zh" ? "Δ转机" : "ΔConnections"} <b>{signed(best.report.missed_connections - (recommended?.report.missed_connections ?? best.report.missed_connections))}</b></span>
                  <span>ΔCO₂ <b>{signed(best.report.emissions_index - (recommended?.report.emissions_index ?? best.report.emissions_index))}</b></span>
                  {!best.pareto_optimal && <em>{language === "zh" ? "该方案被其他候选全面支配" : "Dominated across the objective set"}</em>}
                </div>
              )}
              <div className="proof-list">
                <h3><ShieldCheck size={15} /> {t("safeProof")}</h3>
                {best.constraint_proof.map((proof) => (
                  <span key={proof}><Check size={13} />{proof.replace(/^PASS\s*/, "")}</span>
                ))}
              </div>
            </article>
            <article>
              <span className="section-kicker">{t("counterfactual").toUpperCase()}</span>
              <div className="comparison-bars">
                {comparisonRows.map((row) => {
                  const delay = Number(row.mean_delay ?? 0);
                  return (
                    <div className="comparison-row" key={String(row.name)}>
                      <span>{row.name}</span>
                      <div><i style={{ width: `${Math.min(100, delay * 3)}%` }} /></div>
                      <b>{delay}m</b>
                    </div>
                  );
                })}
              </div>
              <span className="section-kicker sensitivity-title">{language === "zh" ? "推荐膝点敏感性" : "RECOMMENDED KNEE SENSITIVITY"}</span>
              <div className="sensitivity-grid">
                {sensitivity.map((row) => (
                  <span key={String(row.label)}>
                    <small>{row.label}</small>
                    <b>{row.mean_delay == null ? "INFEASIBLE" : `${row.mean_delay} min`}</b>
                  </span>
                ))}
              </div>
            </article>
            {best.risk_shield?.rows?.length > 0 && (
              <article className={`risk-shield-section ${best.risk_shield.status}`}>
                <div className="risk-shield-head">
                  <span className="risk-shield-icon"><ShieldCheck size={18} /></span>
                  <span>
                    <small>CONFORMAL RISK SHIELD · HELD-OUT 90%</small>
                    <strong>{language === "zh" ? "不确定性运行闸门" : "Uncertainty execution gate"}</strong>
                    <p>{best.risk_shield.summary}</p>
                  </span>
                  <em>{best.risk_shield.status.toUpperCase()}</em>
                </div>
                <div className="risk-shield-stats">
                  <span><small>{language === "zh" ? "燃油越界" : "Fuel breaches"}</small><b>{best.risk_shield.fuel_escalations}<i> flights</i></b></span>
                  <span><small>{language === "zh" ? "尾部转机暴露" : "Transfer exposure"}</small><b>{best.risk_shield.connection_passengers_exposed}<i> pax</i></b></span>
                  <span className={(best.risk_shield.runway_clearance?.conditional_actions ?? 0) > 0 ? "danger" : ""}><small>{language === "zh" ? "跑道条件动作" : "Conditional runway uses"}</small><b>{best.risk_shield.runway_clearance?.conditional_actions ?? 0}<i> actions</i></b></span>
                  <span><small>{language === "zh" ? "校准 / 留出验证" : "Calibration / holdout"}</small><b>{best.risk_shield.calibration_samples}<i> / {best.risk_shield.evaluation_samples ?? "—"}</i></b></span>
                  <span><small>{language === "zh" ? "留出覆盖率" : "Holdout coverage"}</small><b>{best.risk_shield.empirical_coverage == null ? (language === "zh" ? "历史快照" : "LEGACY") : (best.risk_shield.empirical_coverage * 100).toFixed(1)}<i>{best.risk_shield.empirical_coverage == null ? "" : `% · ${best.risk_shield.calibration_health}`}</i></b></span>
                  <span><small>{language === "zh" ? "执行权限" : "Execution mode"}</small><b>{best.risk_shield.automation_mode.replaceAll("_", " ")}</b></span>
                </div>
                {best.risk_shield.adaptive_envelope && best.risk_shield.adaptive_envelope.status !== "not_required" ? (
                  <div className={`adaptive-envelope-strip ${best.risk_shield.adaptive_envelope.status}`}>
                    <header>
                      <span>{language === "zh" ? "漂移感知安全包络" : "SHIFT-AWARE SAFETY ENVELOPE"}</span>
                      <strong>{best.risk_shield.adaptive_envelope.status.replaceAll("_", " ").toUpperCase()}</strong>
                    </header>
                    <p>{best.risk_shield.adaptive_envelope.summary}</p>
                    <div>
                      <span>
                        <small>{language === "zh" ? "监测 → 求解" : "MONITOR → SOLVER"}</small>
                        <b>{best.risk_shield.adaptive_envelope.trigger_status.toUpperCase()} → CP-SAT</b>
                      </span>
                      <span>
                        <small>{language === "zh" ? "保护里程碑" : "PROTECTED MILESTONES"}</small>
                        <b>{best.risk_shield.adaptive_envelope.protected_milestones?.join(" / ") || "—"}</b>
                      </span>
                      <span>
                        <small>{language === "zh" ? "额外工程余量" : "ENGINEERING RESERVE"}</small>
                        <b>+{best.risk_shield.adaptive_envelope.engineering_reserve_min} min</b>
                      </span>
                      <span>
                        <small>{language === "zh" ? "方案变化" : "PLAN CHANGES"}</small>
                        <b>{best.risk_shield.adaptive_envelope.changed_assignments ?? 0} {language === "zh" ? "架航班" : "flights"}</b>
                      </span>
                    </div>
                  </div>
                ) : null}
                {best.risk_shield.runway_clearance ? (
                  <div className={`runway-contract-strip ${best.risk_shield.runway_clearance.status}`}>
                    <header>
                      <span>{language === "zh" ? "时间化跑道许可" : "TIME-INDEXED RUNWAY CLEARANCE"}</span>
                      <strong>{best.risk_shield.runway_clearance.status === "clear"
                        ? (language === "zh" ? "P90 许可内" : "INSIDE P90")
                        : (language === "zh" ? "就绪时间仍不确定" : "READINESS UNCERTAIN")}</strong>
                    </header>
                    <p>{best.risk_shield.runway_clearance.summary}</p>
                    {(best.risk_shield.runway_clearance.windows ?? []).map((window) => (
                      <span className={window.status} key={window.runway}>
                        <b>{window.runway}</b>
                        <small>{language === "zh"
                          ? `点预测开放 T+${window.point_ready_minute} · P90 T+${window.p90_ready_minute} · 首次计划使用 ${window.first_planned_use_minute == null ? "未使用" : `T+${window.first_planned_use_minute}`}`
                          : `Point T+${window.point_ready_minute} · P90 T+${window.p90_ready_minute} · first use ${window.first_planned_use_minute == null ? "NONE" : `T+${window.first_planned_use_minute}`}`}</small>
                        <em>{window.status === "unused"
                          ? (language === "zh" ? "本方案未使用" : "NOT USED")
                          : window.status === "clear"
                            ? (language === "zh" ? "可释放" : "CLEARED")
                            : (language === "zh" ? "条件动作" : "CONDITIONAL")}</em>
                      </span>
                    ))}
                  </div>
                ) : null}
                <div className="risk-flight-strip">
                  {[...best.risk_shield.rows]
                    .sort((left, right) => left.fuel_slack_min - right.fuel_slack_min)
                    .slice(0, 4)
                    .map((row) => (
                      <span className={row.fuel_level} key={row.flight_id}>
                        <strong>{row.flight_id}</strong>
                        <small>ELDT90 T+{row.eldt_upper_90} · LIMIT T+{row.fuel_reserve_deadline}</small>
                        <em>{row.fuel_slack_min > 0 ? "+" : ""}{row.fuel_slack_min}m</em>
                      </span>
                    ))}
                </div>
                <footer>{best.risk_shield.caveat}</footer>
              </article>
            )}

            {twinTrust && twinTrust.rows.length > 0 && (
              <article className={"twin-trust-section " + twinTrust.status}>
                <header>
                  <span><Radio size={18} /></span>
                  <div>
                    <small>ANYTIME-VALID TWIN TRUST · E-PROCESS</small>
                    <strong>{language === "zh" ? "数字孪生可信度哨兵" : "Digital-twin trust sentinel"}</strong>
                    <p>{twinTrust.summary}</p>
                  </div>
                  <em>
                    {twinTrust.status === "trusted"
                      ? (language === "zh" ? "可信运行" : "TRUSTED")
                      : twinTrust.status === "watch"
                        ? (language === "zh" ? "局部关注" : "LOCAL WATCH")
                        : twinTrust.status === "drift"
                          ? (language === "zh" ? "失配停机" : "DRIFT STOP")
                          : (language === "zh" ? "证据不足" : "NO EVIDENCE")}
                  </em>
                </header>
                <div className="twin-trust-stats">
                  <span>
                    <small>{language === "zh" ? "整体留出覆盖率" : "Overall holdout coverage"}</small>
                    <b>{twinTrust.overall_empirical_coverage == null ? "—" : (twinTrust.overall_empirical_coverage * 100).toFixed(1)}<i>% / target {(twinTrust.target_coverage * 100).toFixed(0)}%</i></b>
                  </span>
                  <span>
                    <small>{language === "zh" ? "最强局部证据" : "Strongest local evidence"}</small>
                    <b>{twinTrust.local_max_e_value}<i> e-value</i></b>
                  </span>
                  <span className={twinTrust.blocks_unattended_execution ? "danger" : ""}>
                    <small>{language === "zh" ? "多重校正停机证据" : "Family-wise stop evidence"}</small>
                    <b>{twinTrust.familywise_e_value}<i> / {twinTrust.stop_threshold}</i></b>
                  </span>
                  <span>
                    <small>{language === "zh" ? "序贯监测规模" : "Sequential monitor scale"}</small>
                    <b>{twinTrust.familywise_streams}<i> streams × {twinTrust.samples_per_stream}</i></b>
                  </span>
                </div>
                <div className="twin-trust-streams">
                  {twinTrust.rows.slice(0, 4).map((row) => (
                    <span className={row.status} key={row.id}>
                      <i>{row.milestone}</i>
                      <strong>{row.flight_id}</strong>
                      <small>{(row.empirical_coverage * 100).toFixed(0)}% · {row.exceedances}/{row.samples} {language === "zh" ? "越界" : "outside"}</small>
                      <em>e {row.max_e_value}</em>
                    </span>
                  ))}
                </div>
                <div className="twin-trust-logic">
                  <code>LOCAL E ≥ {twinTrust.local_watch_threshold} → WATCH</code>
                  <code>max(E) / {twinTrust.familywise_streams} ≥ {twinTrust.stop_threshold} → SAFE STOP</code>
                  <span>{language === "zh" ? "可在任意时刻检查；局部波动不会直接触发全局停机" : "May be checked at any time; a local fluctuation does not directly stop the system"}</span>
                </div>
                <footer>
                  <span>{twinTrust.guarantee}</span>
                  <p>{twinTrust.caveat}</p>
                </footer>
              </article>
            )}

            {causalAttribution && causalAttribution.factors.length > 0 && (
              <article className={"causal-attribution-section " + causalAttribution.status}>
                <header>
                  <span><Activity size={18} /></span>
                  <div>
                    <small>RE-OPTIMIZED COALITIONS · EXACT SHAPLEY</small>
                    <strong>{language === "zh" ? "恢复负担反事实归因" : "Counterfactual recovery attribution"}</strong>
                    <p>{causalAttribution.summary}</p>
                  </div>
                  <em className={causalAttribution.additivity.passed ? "passed" : "partial"}>
                    {causalAttribution.additivity.passed
                      ? (language === "zh" ? "加和校验通过" : "ADDITIVITY PASS")
                      : (language === "zh" ? "部分可行" : "PARTIAL")}
                  </em>
                </header>
                <div className="attribution-ledger">
                  <span>
                    <small>{language === "zh" ? "无扰动反事实" : "Frictionless baseline"}</small>
                    <b>{causalAttribution.baseline.risk_points.toFixed(1)}<i> risk pts</i></b>
                  </span>
                  <i>→</i>
                  <span>
                    <small>{language === "zh" ? "当前完整场景" : "Full current world"}</small>
                    <b>{causalAttribution.full.risk_points.toFixed(1)}<i> risk pts</i></b>
                  </span>
                  <span className="delta">
                    <small>{language === "zh" ? "可归因恢复负担" : "Attributed burden"}</small>
                    <b>{signed(causalAttribution.total_burden.risk_points)}<i> pts</i></b>
                  </span>
                  <em>{causalAttribution.coalition_count} {language === "zh" ? "种组合" : "coalitions"} × {causalAttribution.runs_per_coalition} {language === "zh" ? "个世界" : "worlds"}</em>
                </div>
                <div className="attribution-factors">
                  {causalAttribution.factors.map((factor) => (
                    <div className={factor.risk_points >= 0 ? "burden" : "relief"} key={factor.id}>
                      <span>
                        <strong>{language === "zh" ? factor.label : factor.label_en}</strong>
                        <small>{factor.detail}</small>
                      </span>
                      <i><b style={{ width: Math.max(4, Math.abs(factor.risk_points) / attributionScale * 100) + "%" }} /></i>
                      <em>{signed(factor.risk_points)}<small> pts</small></em>
                      <span className="factor-effects">
                        <small>P90 {signed(factor.p90_delay_min)}m</small>
                        <small>PAX {signed(factor.missed_passengers)}</small>
                        <small>QUEUE {signed(factor.queue_triggers)}</small>
                      </span>
                    </div>
                  ))}
                </div>
                <details className="attribution-worlds">
                  <summary>
                    <span><ChevronDown size={12} />{language === "zh" ? "查看逐组合重求解证据" : "Inspect coalition re-solves"}</span>
                    <small>Σ φ = {causalAttribution.additivity.attributed_sum_points.toFixed(2)} · error {causalAttribution.additivity.absolute_error}</small>
                  </summary>
                  <div>
                    {causalAttribution.coalitions.map((world) => (
                      <span className={world.feasible ? "feasible" : "infeasible"} key={world.id}>
                        <strong>{world.active_factors.length ? world.active_factors.map((id) => causalAttribution.factors.find((factor) => factor.id === id)?.label ?? id).join(" + ") : (language === "zh" ? "无扰动" : "Frictionless")}</strong>
                        <small>RISK {world.metrics.risk_points.toFixed(1)} · P90 {world.metrics.p90_delay_min.toFixed(1)}m · PAX {world.metrics.missed_passengers}</small>
                        <em>{world.feasible ? "CP-SAT ✓" : "PENALTY"}</em>
                      </span>
                    ))}
                  </div>
                </details>
                <footer>
                  <span>{causalAttribution.interpretation}</span>
                  <p>{causalAttribution.caveat}</p>
                </footer>
              </article>
            )}


            {preferenceLearning && preferenceLearning.candidates.length > 1 && (
              <article className="preference-learning-section">
                <header>
                  <span><BrainCircuit size={19} /></span>
                  <div>
                    <small>ACTIVE BAYESIAN PREFERENCE ELICITATION</small>
                    <strong>{language === "zh" ? "让系统学习你的取舍" : "Teach the system your trade-offs"}</strong>
                    <p>{language === "zh" ? preferenceLearning.summary : preferenceLearning.summary_en}</p>
                  </div>
                  <em className={preferenceLearning.human_override ? "override" : ""}>
                    {preferenceLearning.approved_plan_id
                      ? preferenceLearning.human_override
                        ? (language === "zh" ? "人工覆盖推荐" : "HUMAN OVERRIDE")
                        : (language === "zh" ? "审批与后验一致" : "APPROVAL ALIGNED")
                      : language === "zh"
                        ? preferenceLearning.comparison_count + " 次反馈"
                        : preferenceLearning.comparison_count + " choices"}
                  </em>
                </header>
                <div className="preference-memory-strip">
                  <span><History size={13} /><b>{language === "zh" ? "可撤销偏好记忆" : "Revocable preference memory"}</b><small>{language === "zh" ? `历史 ${preferenceLearning.historical_comparison_count ?? 0} · 本轮 ${preferenceLearning.session_comparison_count ?? 0}` : `history ${preferenceLearning.historical_comparison_count ?? 0} · this run ${preferenceLearning.session_comparison_count ?? 0}`}</small></span>
                  <button disabled={observing || preferenceLearning.comparison_count < 1} onClick={onResetPreference}><RotateCcw size={12} />{language === "zh" ? "撤销已存记忆" : "Forget saved memory"}</button>
                </div>

                <div className="preference-posterior">
                  {preferenceLearning.posterior.weights.map((weight) => (
                    <span key={weight.id}>
                      <small>{language === "zh" ? weight.label : weight.label_en}</small>
                      <i>
                        <b style={{ width: (weight.mean * 100) + "%" }} />
                        <u
                          style={{
                            left: (weight.low90 * 100) + "%",
                            width: ((weight.high90 - weight.low90) * 100) + "%",
                          }}
                        />
                      </i>
                      <strong>{Math.round(weight.mean * 100)}%</strong>
                      <em>{Math.round(weight.low90 * 100)}–{Math.round(weight.high90 * 100)}%</em>
                    </span>
                  ))}
                </div>
                <div className="preference-ranking">
                  {preferenceLearning.candidates.map((candidate) => (
                    <button
                      key={candidate.id}
                      className={(candidate.id === preferenceLearning.recommended_id ? "recommended " : "") + (candidate.id === best.id ? "active" : "")}
                      onClick={() => onSelect(candidate.id)}
                    >
                      <small>#{candidate.preference_rank} · {candidate.profile.toUpperCase()}</small>
                      <strong>{planTitleLabel(candidate.title, candidate.profile, language)}</strong>
                      <span>
                        <b>{Math.round(candidate.optimal_probability * 100)}%</b>
                        <i>{language === "zh" ? "后验最优概率" : "posterior best"}</i>
                      </span>
                    </button>
                  ))}
                </div>
                {preferenceLearning.query && session.phase === "awaiting_approval" ? (
                  <section className="preference-query">
                    <header>
                      <span>
                        <small>{language === "zh" ? "信息量最大的下一问" : "MOST INFORMATIVE NEXT QUESTION"}</small>
                        <strong>{language === "zh" ? "如果现在必须选，你更愿意执行哪个？" : "Which plan would you rather execute now?"}</strong>
                      </span>
                      <em>{preferenceLearning.query.expected_information_bits.toFixed(3)} bit</em>
                    </header>
                    <div>
                      <button
                        disabled={observing}
                        onClick={() => {
                          onSelect(preferenceLearning.query!.left_plan_id);
                          onPreference(
                            preferenceLearning.query!.left_plan_id,
                            preferenceLearning.query!.right_plan_id,
                            preferenceLearning.query!.left_plan_id,
                          );
                        }}
                      >
                        <small>OPTION A</small>
                        <strong>{preferenceLearning.query.left_title}</strong>
                        <em>{language === "zh" ? "我选这个" : "Choose A"}</em>
                      </button>
                      <span>OR</span>
                      <button
                        disabled={observing}
                        onClick={() => {
                          onSelect(preferenceLearning.query!.right_plan_id);
                          onPreference(
                            preferenceLearning.query!.left_plan_id,
                            preferenceLearning.query!.right_plan_id,
                            preferenceLearning.query!.right_plan_id,
                          );
                        }}
                      >
                        <small>OPTION B</small>
                        <strong>{preferenceLearning.query.right_title}</strong>
                        <em>{language === "zh" ? "我选这个" : "Choose B"}</em>
                      </button>
                    </div>
                    <p>
                      <ShieldCheck size={12} />
                      {language === "zh"
                        ? "回答只更新软目标后验并重排当前安全候选；不会修改任何航班动作或硬约束。"
                        : "Your answer updates only soft preferences and re-ranks safe candidates; no schedule or hard rule is changed."}
                    </p>
                  </section>
                ) : (
                  <p className="preference-locked">
                    <ShieldCheck size={12} />
                    {session.phase === "awaiting_approval"
                      ? (language === "zh"
                        ? "当前安全候选的全部成对比较已完成；调整目标并重规划后可开始新一轮学习。"
                        : "All safe-candidate pairs are complete; a new replan starts a fresh learning round.")
                      : (language === "zh"
                        ? "偏好后验已冻结为本次演练证据；执行后不再反向修改决策。"
                        : "The preference posterior is frozen as exercise evidence after execution.")}
                  </p>
                )}
                <footer>{preferenceLearning.caveat}</footer>
              </article>
            )}

            {pareto && pareto.points.length > 0 && (
              <ParetoFrontier analysis={pareto} language={language} selectedPlanId={best.id} onSelect={onSelect} />
            )}
            {bargaining && bargaining.candidates.length > 0 && (
              <article className="bargaining-section">
                <div className="bargaining-head">
                  <span className="bargaining-icon"><Handshake size={19} /></span>
                  <span>
                    <small>MULTI-STAKEHOLDER NASH BARGAINING</small>
                    <strong>{language === "zh" ? "四方可解释协商" : "Explainable four-party bargaining"}</strong>
                    <p>{language === "zh" ? bargaining.summary : bargaining.summary_en}</p>
                  </span>
                  <em className={bargaining.recommendation_changed ? "changed" : ""}>
                    {bargaining.recommendation_changed
                      ? (language === "zh" ? "发现不同折中" : "ALTERNATE COMPROMISE")
                      : (language === "zh" ? "推荐一致" : "ALIGNED")}
                  </em>
                </div>
                <div className="bargaining-stakeholders">
                  {bargaining.stakeholders.map((stakeholder) => (
                    <span key={stakeholder.id}>
                      <i>{stakeholder.id === "airport" ? "OPS" : stakeholder.id === "passenger" ? "PAX" : stakeholder.id === "safety" ? "SAFE" : "CO₂"}</i>
                      <strong>{language === "zh" ? stakeholder.label : stakeholder.label_en}</strong>
                      <small>{language === "zh" ? stakeholder.objective : stakeholder.objective_en}</small>
                      <em>{Math.round(stakeholder.weight * 100)}%</em>
                    </span>
                  ))}
                </div>
                <div className="bargaining-candidates">
                  {bargaining.candidates.map((candidate) => (
                    <button
                      key={candidate.id}
                      className={`${candidate.id === best.id ? "active" : ""} ${candidate.id === bargaining.recommended_id ? "recommended" : ""}`}
                      onClick={() => onSelect(candidate.id)}
                    >
                      <header>
                        <span>
                          <strong>{planTitleLabel(candidate.title, candidate.profile, language)}</strong>
                          <small>{candidate.profile.toUpperCase()}</small>
                        </span>
                        {candidate.id === bargaining.recommended_id && <em>{language === "zh" ? "协商折中" : "NASH COMPROMISE"}</em>}
                        {candidate.autonomy_blocked && <i>{language === "zh" ? "需人工" : "HUMAN"}</i>}
                      </header>
                      <div className="utility-bars">
                        {bargaining.stakeholders.map((stakeholder) => {
                          const utility = candidate.utilities[stakeholder.id] ?? 0;
                          return (
                            <span key={stakeholder.id}>
                              <small>{stakeholder.id === "airport" ? "OPS" : stakeholder.id === "passenger" ? "PAX" : stakeholder.id === "safety" ? "SAFE" : "CO₂"}</small>
                              <i><b style={{ width: `${utility * 100}%` }} /></i>
                              <em>{utility.toFixed(2)}</em>
                            </span>
                          );
                        })}
                      </div>
                      <footer>
                        <span><small>NASH WELFARE</small><b>{candidate.nash_welfare.toFixed(3)}</b></span>
                        <span><small>{language === "zh" ? "最弱方效用" : "MIN UTILITY"}</small><b>{candidate.minimum_utility.toFixed(3)}</b></span>
                        <i>{candidate.id === best.id ? (language === "zh" ? "正在比较" : "COMPARING") : (language === "zh" ? "点击预览" : "PREVIEW")}</i>
                      </footer>
                    </button>
                  ))}
                </div>
                <footer>{bargaining.caveat}</footer>
              </article>
            )}
            {regret && regret.candidates.length > 0 && (
              <article className="regret-section">
                <div className="regret-head">
                  <span className="robustness-icon"><BrainCircuit size={18} /></span>
                  <span>
                    <small>SHARED FUTURES · MINIMAX REGRET</small>
                    <strong>{language === "zh" ? "跨情景稳健选择" : "Robust cross-world choice"}</strong>
                    <p>{language === "zh" ? regret.summary : regret.summary_en}</p>
                  </span>
                  <em className={regret.recommendation_changed ? "changed" : ""}>
                    {regret.recommendation_changed
                      ? (language === "zh" ? "稳健推荐变化" : "ROBUST PICK CHANGED")
                      : (language === "zh" ? "双重验证一致" : "NOMINAL = ROBUST")}
                  </em>
                </div>
                <div className="regret-candidates">
                  {regret.candidates.map((candidate) => {
                    const isRobust = candidate.id === regret.recommended_id;
                    return (
                      <button
                        className={`${isRobust ? "robust-pick" : ""} ${candidate.id === best.id ? "selected" : ""}`}
                        key={candidate.id}
                        onClick={() => onSelect(candidate.id)}
                      >
                        <span>
                          <b>{planTitleLabel(candidate.title, undefined, language)}</b>
                          <small>{candidate.safe_worlds}/{candidate.total_worlds} {language === "zh" ? "情景安全" : "worlds safe"}</small>
                        </span>
                        <i><u style={{ width: `${Math.max(4, Math.min(100, candidate.max_regret))}%` }} /></i>
                        <em><b>{candidate.max_regret.toFixed(1)}</b><small>MAX REGRET</small></em>
                        {isRobust && <strong>{language === "zh" ? "稳健推荐" : "ROBUST PICK"}</strong>}
                      </button>
                    );
                  })}
                </div>
                <footer>
                  {language === "zh"
                    ? "Agent 构造所有候选共享的压力情景；可执行动作始终来自 CP-SAT，并逐情景通过硬约束审查。"
                    : "Agents construct shared stress worlds; executable actions always come from CP-SAT and pass hard-constraint checks in every world."}
                </footer>
              </article>
            )}
            {agentArbitration && (
              <article className={`agent-arbitration-section ${agentArbitration.status}`}>
                <div className="agent-arbitration-head">
                  <span className="agent-arbitration-icon"><Layers3 size={18} /></span>
                  <span>
                    <small>TYPED AGENT PROPOSALS · INTERFACE CONTRACTS</small>
                    <strong>{language === "zh" ? "跨 Agent 假设仲裁" : "Cross-agent assumption arbitration"}</strong>
                    <p>{agentArbitration.summary}</p>
                  </span>
                  <em>{agentArbitration.aligned_count}/{agentArbitration.checks_total} {language === "zh" ? "已对齐" : "ALIGNED"}</em>
                </div>
                <div className="agent-contracts">
                  {agentArbitration.checks.map((check) => (
                    <section className={check.status} key={check.id}>
                      <header>
                        <span>{check.status === "aligned" || check.status === "resolved_watch"
                          ? <Check size={12} />
                          : <AlertTriangle size={12} />}</span>
                        <div><small>{check.producer} → {check.consumer}</small><strong>{check.contract}</strong></div>
                        <em>{contractStatusLabel[check.status]}</em>
                      </header>
                      <p>{check.evidence}</p>
                      <footer>{check.consequence}</footer>
                    </section>
                  ))}
                </div>
                <footer>
                  <span><ShieldCheck size={11} />{agentArbitration.policy}</span>
                  <small>{agentArbitration.caveat}</small>
                </footer>
              </article>
            )}
            {!informationValue && decisionEvidence.gate && (
              <article className="information-value-section">
                <div className="information-value-head"><span>
                  <strong>{language === "zh" ? "所选候选的证据要求" : "Evidence required by the selected candidate"}</strong>
                  <p>{decisionEvidence.gate.summary}</p>
                </span></div>
                <p>{language === "zh"
                  ? "当前没有与本候选绑定的信息价值评分，不借用其他候选的取证指令。以下仅显示本方案审批门要求。"
                  : "No sensing scores are bound to this candidate. Only its own approval requirements are shown; another plan's advice is not reused."}</p>
                {decisionEvidence.gate.required_queries.map(query => (
                  <div key={query.query_id}><p>{query.evidence} · {query.consequence}</p>
                    <button disabled={observing || session.phase !== "awaiting_approval"} onClick={() => onObserve(query.query_id)}>
                      {language === "zh" ? "获取这项证据并重新规划" : "Acquire this evidence and replan"}
                    </button>
                  </div>
                ))}
              </article>
            )}
            {informationValue && (
              <article className="information-value-section">
                <div className="information-value-head">
                  <span className="information-value-icon"><Database size={18} /></span>
                  <span>
                    <small>ACTIVE EVIDENCE ACQUISITION · VALUE OF INFORMATION</small>
                    <strong>{language === "zh" ? "下一条最值得获取的证据" : "Most valuable next evidence"}</strong>
                    <p>{language === "zh" ? informationValue.summary : informationValue.summary_en}</p>
                  </span>
                  <em>{informationValue.status === "sufficient" ? (language === "zh" ? "证据充足" : "SUFFICIENT") : (language === "zh" ? "先观测再优化" : "OBSERVE → OPTIMIZE")}</em>
                </div>
                <div className="information-priors">
                  <small>{language === "zh" ? "显式情景先验" : "EXPLICIT WORLD PRIORS"}</small>
                  {Object.entries(informationValue.assumed_world_priors).map(([world, prior]) => (
                    <span key={world}><i style={{ width: `${prior * 100}%` }} /><b>{world.replaceAll("_", " ")}</b><em>{Math.round(prior * 100)}%</em></span>
                  ))}
                  <strong>{language === "zh" ? `当前加权后悔 ${informationValue.baseline_expected_regret.toFixed(2)}` : `Current weighted regret ${informationValue.baseline_expected_regret.toFixed(2)}`}</strong>
                </div>
                <div className="information-queries">
                  {informationValue.queries.map((query) => {
                    const recommendedQuery = query.id === informationValue.recommended_query_id;
                    return (
                      <div className={`information-query ${recommendedQuery ? "recommended" : ""} ${query.resolved ? "resolved" : ""}`} key={query.id}>
                        <header>
                          <span><small>{query.world_id.replaceAll("_", " ")}</small><strong>{language === "zh" ? query.label : query.label_en}</strong></span>
                          <em>{query.resolved ? (language === "zh" ? "已获取" : "ACQUIRED") : recommendedQuery ? (language === "zh" ? "推荐下一步" : "NEXT BEST") : (language === "zh" ? "备选观测" : "ALTERNATIVE")}</em>
                        </header>
                        <p><b>{query.target}</b> · {language === "zh" ? query.reveals : query.reveals_en}</p>
                        <div>
                          <span><small>{language === "zh" ? "情景先验" : "Prior"}</small><b>{Math.round(query.prior * 100)}%</b></span>
                          <span><small>{language === "zh" ? "软目标后悔↓" : "Soft regret ↓"}</small><b>{query.expected_regret_reduction.toFixed(2)}</b></span>
                          <span><small>{language === "zh" ? "压力状态" : "Stress status"}</small><b>{query.stress_status}</b></span>
                        </div>
                        <footer>
                          <code>{query.source}</code>
                          {query.resolved ? (
                            <span><Check size={12} /> {language === "zh" ? "证据已写入当前快照" : "Evidence added to snapshot"}</span>
                          ) : (
                            <button
                              className={recommendedQuery ? "recommended" : ""}
                              disabled={observing || session.phase !== "awaiting_approval"}
                              onClick={() => onObserve(query.id)}
                            >
                              {observing && recommendedQuery ? <Activity className="spin" size={12} /> : <Database size={12} />}
                              {language === "zh" ? query.action_label : query.action_label_en}
                            </button>
                          )}
                        </footer>
                      </div>
                    );
                  })}
                </div>
                <footer>{informationValue.caveat}</footer>
              </article>
            )}
            {policy && (
              <article className="policy-section">
                <div className="policy-head">
                  <span className="policy-icon"><ShieldCheck size={18} /></span>
                  <span>
                    <small>EVENT-TRIGGERED MPC · PRE-SOLVED CONTINGENCY ATLAS</small>
                    <strong>{language === "zh" ? "自适应恢复策略" : "Adaptive recovery policy"}</strong>
                    <p>{language === "zh" ? policy.summary : policy.summary_en}</p>
                  </span>
                  <em className={policy.approved ? "approved" : ""}>
                    {policy.approved
                      ? (language === "zh" ? "已授权安全包络" : "ENVELOPE APPROVED")
                      : (language === "zh" ? "随方案一并审批" : "APPROVAL REQUIRED")}
                  </em>
                </div>
                {policyRuntime && (
                  <div className={`policy-runtime ${policyRuntime.matched ? "matched" : ""}`}>
                    <i>{policyRuntime.matched ? "LIVE MATCH" : "MONITOR"}</i>
                    <span>
                      <b>{policyRuntime.branch_label ?? (language === "zh" ? "继续监控" : "Continue monitoring")}</b>
                      <small>{policyRuntime.reason}</small>
                      <u className={policyRuntime.warm_start_flights ? "ready" : "cold"}>
                        {policyRuntime.warm_start_flights
                          ? "CP-SAT WARM START · " + policyRuntime.warm_start_flights + " FLIGHTS"
                          : (language === "zh" ? "当前事件无可复用预案 · 从零求解" : "No reusable branch · cold solve")}
                      </u>
                    </span>
                    <em>{policyActionLabel[policyRuntime.action] ?? policyRuntime.action}</em>
                  </div>
                )}
                <div className="policy-flow">
                  <div className="policy-root">
                    <small>{language === "zh" ? "批准方案" : "APPROVED PLAN"}</small>
                    <b>{planTitleLabel(policy.source_plan_title, undefined, language)}</b>
                    <span>{language === "zh" ? "默认监控 · " + policy.evidence.prepared_branches + "/" + policy.branch_count + " 分支已热启动" : "Monitor by default · " + policy.evidence.prepared_branches + "/" + policy.branch_count + " branches pre-solved"}</span>
                  </div>
                  <i>→</i>
                  <div className="policy-branches">
                    {policy.branches.map((branch, index) => (
                      <div key={branch.id} className={policyRuntime?.branch_id === branch.id ? "active" : ""}>
                        <i>{String(index + 1).padStart(2, "0")}</i>
                        <span>
                          <b>{language === "zh" ? branch.label : branch.label_en}</b>
                          <small>{language === "zh" ? branch.condition : branch.condition_en}</small>
                          <u className={branch.prepared_response.status === "ready" ? "ready" : "cold"}>
                            {branch.prepared_response.status === "ready"
                              ? "HOT START READY · " + branch.prepared_response.assignment_count + " FLIGHTS · P90 " + (branch.prepared_response.expected_p90_delay_min ?? "—") + "m"
                              : (language === "zh" ? "事件触发后现算" : "SOLVE ON TRIGGER")}
                          </u>
                        </span>
                        <em>{policyActionLabel[branch.action] ?? branch.action}</em>
                      </div>
                    ))}
                  </div>
                </div>
                <footer>
                  <ShieldCheck size={12} />
                  <span>
                    {language === "zh"
                      ? "策略只决定何时、对多大范围重新求解；航班动作仍由 CP-SAT 生成，并强制通过硬约束安全闸门。"
                      : "The policy decides when and how broadly to re-solve; flight actions still come from CP-SAT and must pass the hard-safety gate."}
                  </span>
                </footer>
              </article>
            )}
            {robustness && (
              <article className="robustness-section">
                <div className="robustness-head">
                  <span className="robustness-icon"><BrainCircuit size={18} /></span>
                  <span>
                    <small>COUNTERFACTUAL STRESS TEST · {robustness.runs_per_stress_world}× EACH</small>
                    <strong>{t("robustHorizon")}</strong>
                    <p>{language === "zh" ? robustness.summary : robustness.summary_en}</p>
                  </span>
                  <em>
                    <b>{robustness.direct_worlds}/{robustness.total_worlds}</b>
                    <small>{t("directExecute")}</small>
                  </em>
                </div>
                <div className="stress-world-grid">
                  {robustness.worlds.map((world) => {
                    const statusKey: CopyKey = {
                      stable: "stressStable",
                      watch: "stressWatch",
                      replan: "stressReplan",
                      manual: "stressManual",
                    }[world.status] as CopyKey;
                    return (
                      <div className={`stress-world ${world.status}`} key={world.id}>
                        <header>
                          <strong>{language === "zh" ? world.label : world.label_en}</strong>
                          <em>{t(statusKey)}</em>
                        </header>
                        <p>{language === "zh" ? world.change : world.change_en}</p>
                        <div className="stress-compare">
                          <span><small>{t("fixedPlan")}</small><b>{world.fixed.p90_delay}<i>m</i></b></span>
                          <i>→</i>
                          <span><small>{t("adaptiveReplan")}</small><b>{world.adaptive.p90_delay}<i>m</i></b></span>
                        </div>
                        <footer>
                          <span>{t("savedPassengers")} <b>{world.saved_missed}</b></span>
                          <strong className={world.p90_delta > 0 ? "cost" : ""}>
                            {!world.fixed_safe && `${t("safetyGate")} · `}
                            ΔP90 {world.p90_delta > 0 ? "+" : ""}{world.p90_delta}m
                          </strong>
                        </footer>
                      </div>
                    );
                  })}
                </div>
              </article>
            )}
            {recourse && (
              <article className="recourse-section">
                <div className="recourse-head">
                  <span className="recourse-icon"><BrainCircuit size={18} /></span>
                  <span>
                    <small>VERIFIED AGENTIC RECOURSE</small>
                    <strong>{t("causalRecourse")}</strong>
                    <p>{runtimeText(recourse.summary, language)}</p>
                  </span>
                  <em>{recourse.preserved_verified ? "BOUNDARY PASS" : "CHECK REQUIRED"}</em>
                </div>
                <div className="recourse-stats">
                  <span><small>{t("affectedScope")}</small><b>{recourse.affected_count}<i> flights</i></b></span>
                  <span><small>{t("lockedSchedule")}</small><b>{recourse.frozen_count}<i> / {recourse.pending_count}</i></b></span>
                  <span><small>{t("actualChanges")}</small><b>{recourse.changed_count}<i> flights</i></b></span>
                </div>
                <div className="recourse-paths">
                  <small>{t("dependencyPath")}</small>
                  {recourse.dependency_paths.slice(0, 3).map((path) => (
                    <code key={path.join("-")}>{path.join("  →  ")}</code>
                  ))}
                </div>
                <div className="recourse-proof"><ShieldCheck size={14} /><span><small>{t("boundaryProof")}</small>{recourse.boundary_proof}</span></div>
              </article>
            )}
            <article className="trace-section">
              <span className="section-kicker">TOOL TRACE</span>
              {session.traces.map((trace, index) => (
                <div className="trace-row" key={index}>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <strong>{trace.agent}</strong>
                  <p>{runtimeText(trace.summary, language)}</p>
                  <code>{trace.tool ?? "validated schema"}</code>
                </div>
              ))}
            </article>
          </div>
        )}</>}
      </section>
    </div>
  );
}

const sandboxEvents = [
  { type: "storm", label: "stormEvent", icon: CloudLightning, severity: 5, duration_min: 20 },
  { type: "gate_outage", label: "gateEvent", icon: Wrench, severity: 3, duration_min: 25, resource_id: "A4" },
  { type: "ground_vehicle_failure", label: "vehicleEvent", icon: AlertTriangle, severity: 3, duration_min: 15, resource_id: "TUG-3" },
  { type: "medical_diversion", label: "medicalEvent", icon: Stethoscope, severity: 5, duration_min: 0, resource_id: "MED902" },
] as const;

function SandboxTray({
  session,
  language,
  onSession,
  onError,
}: {
  session: SessionState;
  language: Language;
  onSession: (session: SessionState) => void;
  onError: (message: string) => void;
}) {
  const t = (key: CopyKey) => copy[language][key];
  const [open, setOpen] = useState(false);
  const [injecting, setInjecting] = useState(false);
  const [severity, setSeverity] = useState(session.scenario.weather.severity);
  const [closure, setClosure] = useState(0);
  const [passengers, setPassengers] = useState(1);
  const [fuel, setFuel] = useState(0);
  const apply = async () => {
    try {
      onSession(await updateSandbox(session.id, {
        weather_severity: severity,
        runway_closure_min: closure,
        passenger_multiplier: passengers,
        fuel_margin_delta: fuel,
      }));
    } catch (error) {
      onError(error instanceof Error ? error.message : "更新失败");
    }
  };
  const injectTemplate = async (
    template: (typeof sandboxEvents)[number],
    minute: number,
    source: "palette_click" | "timeline_drag",
  ) => {
    if (injecting) return;
    setInjecting(true);
    try {
      onSession(await injectEvent(session.id, {
        type: template.type,
        minute,
        severity: template.severity,
        duration_min: template.duration_min,
        resource_id: "resource_id" in template ? template.resource_id : undefined,
        payload: { source },
      }));
    } catch (error) {
      onError(error instanceof Error ? error.message : "事件注入失败");
    } finally {
      setInjecting(false);
    }
  };
  const drop = async (event: React.DragEvent<HTMLDivElement>) => {
    if (injecting) return;
    event.preventDefault();
    const type = event.dataTransfer.getData("stormshift/event");
    const template = sandboxEvents.find((item) => item.type === type);
    if (!template) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const minute = session.scenario.current_minute + Math.round(((event.clientX - rect.left) / rect.width) * 30);
    await injectTemplate(template, minute, "timeline_drag");
  };
  return (
    <section className={`sandbox-tray glass ${open ? "open" : ""} ${injecting ? "processing" : ""} ${session.plans.length ? "with-plans" : ""}`}>
      <button className="tray-handle" onClick={() => setOpen(!open)}>
        <GripHorizontal size={17} /> SANDBOX <ChevronDown size={14} />
      </button>
      {injecting && <div className="sandbox-policy-busy"><BrainCircuit size={13} /><span>RUNTIME POLICY</span><b>{language === "zh" ? "匹配事件并滚动重规划…" : "Matching event and replanning…"}</b></div>}
      {open && (
        <div className="tray-content">
          <div className="sandbox-sliders">
            <label>天气等级 <b>{severity}</b><input type="range" min="0" max="5" value={severity} onChange={(e) => setSeverity(Number(e.target.value))} /></label>
            <label>07R 关闭 <b>{closure}m</b><input type="range" min="0" max="60" step="5" value={closure} onChange={(e) => setClosure(Number(e.target.value))} /></label>
            <label>旅客系数 <b>{passengers.toFixed(1)}×</b><input type="range" min=".5" max="2" step=".1" value={passengers} onChange={(e) => setPassengers(Number(e.target.value))} /></label>
            <label>燃油余量 <b>{fuel >= 0 ? "+" : ""}{fuel}m</b><input type="range" min="-15" max="30" step="5" value={fuel} onChange={(e) => setFuel(Number(e.target.value))} /></label>
            <button className="mini-primary" onClick={apply}>应用状态</button>
          </div>
          <div className="event-palette">
            <span>{t("eventPalette")}<small>{t("dragHint")}</small></span>
            {sandboxEvents.map((template) => {
              const { type, label, icon: Icon } = template;
              return (
                <button
                  key={type}
                  draggable
                  disabled={injecting}
                  title={language === "zh" ? "点击在 T+10 注入；也可拖到指定时间" : "Click to inject at T+10, or drag to a chosen time"}
                  onClick={() => void injectTemplate(template, session.scenario.current_minute + 10, "palette_click")}
                  onDragStart={(event) => event.dataTransfer.setData("stormshift/event", type)}
                >
                  <Icon size={14} /> {t(label)}
                </button>
              );
            })}
          </div>
          <div className="event-timeline" onDragOver={(event) => event.preventDefault()} onDrop={drop}>
            <span className="now-marker">NOW</span>
            <i /><i /><i /><i /><i /><i />
            <small>+5</small><small>+10</small><small>+15</small><small>+20</small><small>+25</small><small>+30</small>
          </div>
        </div>
      )}
    </section>
  );
}


export default function App() {
  const [language, setLanguage] = useState<Language>("zh");
  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  }, [language]);

  const t = (key: CopyKey) => copy[language][key];
  const [session, setSession] = useState<SessionState>();
  const [busy, setBusy] = useState(false);
  const [command, setCommand] = useState("");
  const pendingClarification = Boolean(session?.intent?.needs_confirmation && session.intent.issues?.length);
  const [layer, setLayer] = useState<MapLayer>("conflicts");
  const [retuneOpen, setRetuneOpen] = useState(false);
  const [deckExpanded, setDeckExpanded] = useState(false);
  const [storyGuideOpen, setStoryGuideOpen] = useState(false);
  const [storyIntroAcknowledged, setStoryIntroAcknowledged] = useState(false);
  const [storyPreviewKey, setStoryPreviewKey] = useState("");
  const [previewReceipt, setPreviewReceipt] = useState<PreviewSelectionReceiptData | null>(null);
  const [previewOpenRequest, setPreviewOpenRequest] = useState(0);
  const commandInputRef = useRef<HTMLInputElement>(null);
  const [selectedPlanId, setSelectedPlanId] = useState("");
  const [labOpen, setLabOpen] = useState(false);
  const [relayOpen, setRelayOpen] = useState(false);
  const [passengersOpen, setPassengersOpen] = useState(false);
  const [briefView, setBriefView] = useState<"passengers"|"agents"|null>(null);
  const [repairReviewedPlanId, setRepairReviewedPlanId] = useState("");
  useEffect(() => { setBriefView(null); setRepairReviewedPlanId(""); }, [session?.id]);
  useEffect(() => { const close=(e:KeyboardEvent)=>{if(e.key==="Escape")setBriefView(null)}; window.addEventListener("keydown",close);return()=>window.removeEventListener("keydown",close); }, []);
  const [replayOpen, setReplayOpen] = useState(false);
  const [replayFocus, setReplayFocus] = useState<ReplayFocus>("latest");
  const [replayEvents, setReplayEvents] = useState<ExecutionEvent[]>([]);
  const [visibleExecutionEvents, setVisibleExecutionEvents] = useState<ExecutionEvent[]>([]);
  const [historicalPlayback, setHistoricalPlayback] = useState(false);
  const [playbackEvents, setPlaybackEvents] = useState<ExecutionEvent[]>([]);
  const [playbackToken, setPlaybackToken] = useState(0);
  const [displayPhase, setDisplayPhase] = useState<Phase | null>(null);
  const [displayMinute, setDisplayMinuteState] = useState<number | null>(null);
  const displayMinuteRef = useRef<number | null>(null);
  const setDisplayMinute = (minute: number | null) => {
    displayMinuteRef.current = minute;
    setDisplayMinuteState(minute);
  };
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackPaused, setPlaybackPaused] = useState(false);
  const [pedestriansPaused, setPedestriansPaused] = useState(false);
  const [visualPassenger,setVisualPassenger] = useState<{sessionId:string;sourceFlightId:string;stage:ConnectingDeparture["passenger_stage"]}|null>(null);
  const [playbackRate, setPlaybackRateState] = useState(.5);
  const [guidedPlayback, setGuidedPlayback] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [storyBeat, setStoryBeat] = useState("");
  const [directorCue, setDirectorCue] = useState<DirectorCue | null>(null);
  const [directorNext, setDirectorNext] = useState<ExecutionEvent>();
  const [flightLensId, setFlightLensId] = useState("");
  const [resumeAfterFlightLens, setResumeAfterFlightLens] = useState(false);
  const playbackTimers = useRef<PlaybackTask[]>([]);
  const playbackRateRef = useRef(.5);
  const guidedPlaybackRef = useRef(true);
  const isPlayingRef = useRef(false);
  const playbackPausedRef = useRef(false);
  const onlineRunRef = useRef(0);
  const advanceInFlightRef = useRef(false);
  const replayEventsRef = useRef<ExecutionEvent[]>([]);
  const visibleExecutionEventsRef = useRef<ExecutionEvent[]>([]);
  const pendingAdvanceRef = useRef<{
    response: AdvanceExecutionResponse;
    runToken: number;
  } | null>(null);
  const [machineState, send] = useMachine(recoveryMachine);

  const mergeReplay = (events: ExecutionEvent[]) => {
    setReplayEvents((current) => {
      const next = mergeExecutionEvents(current, events);
      replayEventsRef.current = next;
      return next;
    });
  };

  const revealExecutionEvent = (event: ExecutionEvent) => {
    const next = mergeExecutionEvents(visibleExecutionEventsRef.current, [event]);
    visibleExecutionEventsRef.current = next;
    setVisibleExecutionEvents(next);
  };

  useEffect(() => {
    if (!session?.id) return;
    return connectStream(
      session.id,
      (next) => { if (!isPlayingRef.current) setSession(next); },
      (event) => mergeReplay([event]),
    );
  }, [session?.id]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 3400);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => () => {
    onlineRunRef.current += 1;
    playbackTimers.current.forEach((task) => window.clearTimeout(task.id));
  }, []);

  const armPlaybackTask = (task: PlaybackTask) => {
    task.startedAt = performance.now();
    task.id = window.setTimeout(() => {
      playbackTimers.current = playbackTimers.current.filter((item) => item !== task);
      task.callback();
    }, task.remainingMs);
  };

  const clearPlaybackTimers = () => {
    playbackTimers.current.forEach((task) => window.clearTimeout(task.id));
    playbackTimers.current = [];
  };

  const resumeSession = async (next: SessionState) => {
    setStoryGuideOpen(false);
    setStoryIntroAcknowledged(true);
    setStoryPreviewKey("");
    setPreviewOpenRequest(0);
    setDeckExpanded(false);
    setHistoricalPlayback(false);
    clearPlaybackTimers();
    onlineRunRef.current += 1;
    advanceInFlightRef.current = false;
    setDirectorCue(null);
    setDirectorNext(undefined);
    setFlightLensId("");
    setResumeAfterFlightLens(false);
    setRetuneOpen(false);
    setSession(next);
    setPreviewReceipt(null);
    setSelectedPlanId(next.selected_plan_id ?? observationReplacement(next)?.id ?? "");
    setPlaybackEvents([]);
    setPlaybackToken((current) => current + 1);
    setDisplayPhase(next.phase);
    setDisplayMinute(next.scenario.current_minute);
    setIsPlaying(false);
    setPlaybackPaused(false);
    setPedestriansPaused(false);
    playbackPausedRef.current = false;
    setLayer(next.plans.length ? "routes" : "conflicts");
    setStoryBeat(
      next.phase === "recovered"
        ? "已恢复保存的演练 · 可查看 Decision Lab 或任务回放"
        : next.plans.length
          ? "已恢复保存的决策快照"
          : "已恢复保存的机场态势",
    );
    try {
      const history = await getReplay(next.id);
      replayEventsRef.current = history;
      setReplayEvents(history);
      setVisibleExecutionEvents(history);
      visibleExecutionEventsRef.current = history;
      const last = history.at(-1);
      if (next.phase === "recovered" && last) setDisplayMinute(last.sim_minute);
    } catch {
      setReplayEvents([]);
      setVisibleExecutionEvents([]);
      replayEventsRef.current = [];
      visibleExecutionEventsRef.current = [];
    }
  };

  const resetToLobby = () => {
    setStoryGuideOpen(false);
    setStoryIntroAcknowledged(false);
    setStoryPreviewKey("");
    setPreviewOpenRequest(0);
    setDeckExpanded(false);
    setHistoricalPlayback(false);
    clearPlaybackTimers();
    onlineRunRef.current += 1;
    advanceInFlightRef.current = false;
    setRetuneOpen(false);
    setDirectorCue(null);
    setDirectorNext(undefined);
    setFlightLensId("");
    setResumeAfterFlightLens(false);
    setCommand("");
    setSession(undefined);
    setSelectedPlanId("");
    setPlaybackEvents([]);
    setReplayEvents([]);
    setVisibleExecutionEvents([]);
    replayEventsRef.current = [];
    visibleExecutionEventsRef.current = [];
    pendingAdvanceRef.current = null;
    setDisplayPhase(null);
    setDisplayMinute(null);
    setIsPlaying(false);
    isPlayingRef.current = false;
    setPlaybackPaused(false);
    setPedestriansPaused(false);
    playbackPausedRef.current = false;
    playbackRateRef.current = .5;
    setPlaybackRateState(.5);
    guidedPlaybackRef.current = true;
    setGuidedPlayback(true);
    setStoryBeat("");
    send({ type: "RESET" });
  };

  const schedulePlayback = (callback: () => void, delay: number) => {
    const task: PlaybackTask = {
      id: 0,
      callback,
      remainingMs: delay / playbackRateRef.current,
      startedAt: performance.now(),
    };
    playbackTimers.current.push(task);
    armPlaybackTask(task);
  };

  const freezePlayback = (briefing = false) => {
    const now = performance.now();
    playbackTimers.current.forEach((task) => {
      window.clearTimeout(task.id);
      task.remainingMs = Math.max(0, task.remainingMs - (now - task.startedAt));
    });
    playbackPausedRef.current = true;
    setPlaybackPaused(true);
    setPedestriansPaused(!briefing);
    if (briefing) {
      setDirectorCue((current) => current ? { ...current, briefing: true } : current);
    }
  };

  const pausePlayback = () => {
    if (!isPlaying || playbackPaused) return;
    freezePlayback();
  };

  const resumePlayback = () => {
    if (!isPlaying || !playbackPaused) return;
    playbackPausedRef.current = false;
    setPlaybackPaused(false);
    setPedestriansPaused(false);
    setDirectorCue((current) => current ? { ...current, briefing: false } : current);
    const pending = pendingAdvanceRef.current;
    if (pending) {
      pendingAdvanceRef.current = null;
      beginPlayback(
        pending.response.execution_events,
        false,
        pending.response.session,
        () => void continueOnlineExecution(
          pending.response.session,
          pending.response.next_event_minute,
          pending.runToken,
        ),
      );
      return;
    }
    playbackTimers.current.forEach(armPlaybackTask);
  };

  const changePlaybackRate = (rate: number) => {
    if (rate === playbackRateRef.current) return;
    const previousRate = playbackRateRef.current;
    const now = performance.now();
    playbackTimers.current.forEach((task) => {
      if (!playbackPaused) {
        window.clearTimeout(task.id);
        task.remainingMs = Math.max(0, task.remainingMs - (now - task.startedAt));
      }
      task.remainingMs = task.remainingMs * previousRate / rate;
    });
    playbackRateRef.current = rate;
    setPlaybackRateState(rate);
    if (!playbackPaused) playbackTimers.current.forEach(armPlaybackTask);
  };

  const inspectFlight = (flightId: string) => {
    const shouldResume = isPlaying && !playbackPaused;
    if (shouldResume) pausePlayback();
    setResumeAfterFlightLens(shouldResume);
    setFlightLensId(flightId);
  };

  const closeFlightLens = () => {
    setFlightLensId("");
    setResumeAfterFlightLens(false);
  };

  const resumeFromFlightLens = () => {
    setFlightLensId("");
    setResumeAfterFlightLens(false);
    resumePlayback();
  };

  const beginPlayback = (
    events: ExecutionEvent[],
    replay = false,
    finalSession?: SessionState,
    onComplete?: () => void,
  ) => {
    clearPlaybackTimers();
    playbackPausedRef.current = false;
    setPlaybackPaused(false);
    setPedestriansPaused(false);
    if (replay) {
      setHistoricalPlayback(true);
      setVisibleExecutionEvents([]);
      visibleExecutionEventsRef.current = [];
    }
    mergeReplay(events);
    setPlaybackEvents(events);
    setPlaybackToken((current) => current + 1);
    setLayer("routes");
    const hasFlightMovement = events.some((event) => event.type === "execution_tick");
    isPlayingRef.current = true;
    setIsPlaying(true);
    setDisplayPhase(hasFlightMovement || events.some((event) => event.type === "plan_approved") || session?.phase === "executing"
      ? "executing" : "monitoring");
    setReplayOpen(false);
    setStoryBeat(replay
      ? "正在从事件日志重建机场运行…"
      : "执行游标已同步 · 正在等待下一个真实里程碑");

    const hasFailure = events.some((event) => event.type === "ground_vehicle_failure");
    const hasReplan = events.some((event) => event.type.includes("replan"));
    const hasRecovered = events.some((event) => event.type === "recovered");
    const needsApproval = finalSession ? executionNeedsApproval(finalSession) : false;
    const clockStart = replay ? (events[0]?.sim_minute ?? 0)
      : (displayMinuteRef.current ?? session?.scenario.current_minute ?? 0);
    const schedule = buildPlaybackSchedule(events, clockStart);
    const firstMinute = schedule[0]?.event.sim_minute
      ?? session?.scenario.current_minute
      ?? 0;
    const lastMinute = schedule.at(-1)?.event.sim_minute ?? firstMinute;
    const failureDelay = schedule.find(
      ({ event }) => event.type === "ground_vehicle_failure"
    )?.delayMs ?? 0;
    const replanDelay = schedule.find(
      ({ event }) => event.type.includes("replan")
    )?.delayMs ?? 0;
    setDirectorCue(null);
    setDirectorNext(schedule[0]?.event);
    schedule.forEach(({ event, delayMs }, index) => {
      schedulePlayback(() => {
        revealExecutionEvent(event);
        setDisplayMinute(event.sim_minute);
        setDirectorCue({
          event,
          chapter: missionChapterForEvent(visibleExecutionEventsRef.current, event),
          briefing: false,
          step: event.type === "execution_tick" ? 1 : undefined,
        });
        setDirectorNext(schedule[index + 1]?.event);
      }, delayMs);
      if (isMissionBriefingEvent(event)) {
        schedulePlayback(() => {
          if (!guidedPlaybackRef.current) return;
          freezePlayback(true);
          setStoryBeat(event.type.includes("failure")
            ? "讲解停顿：故障已生效，已提交飞机保持不变"
            : needsApproval
              ? "讲解停顿：风险闸门要求人工确认，系统没有擅自继续"
              : "讲解停顿：未来动作已重算，已执行前缀保持不变");
        }, delayMs + 850);
      }
    });
    if (hasFailure) {
      schedulePlayback(() => {
        setDisplayPhase("replanning");
        if (!replay) send({ type: "SECONDARY_FAILURE" });
        setStoryBeat("拖车故障：先暂停受影响的航班，调整剩余安排");
      }, failureDelay);
    }
    if (hasReplan) {
      schedulePlayback(() => {
        if (finalSession && !replay) {
          setSession(finalSession);
          setSelectedPlanId(
            finalSession.selected_plan_id ?? finalSession.plans[0]?.id ?? ""
          );
        }
        const nextPhase = finalSession?.phase
          ?? (needsApproval ? "awaiting_approval" : "monitoring");
        setDisplayPhase(nextPhase);
        if (!replay) send({
          type: needsApproval ? "HUMAN_REVIEW" : "AUTO_REPLAN",
        });
        setStoryBeat(needsApproval
          ? "新方案已生成 · 安全闸门等待操作员批准"
          : "Safety Critic 已验证新未来 · 执行游标可以继续");
      }, replanDelay);
    }
    playbackClockSamples(events, clockStart).forEach(({ delayMs, minute }) => {
      schedulePlayback(() => setDisplayMinute(minute), delayMs);
    });
    const finishDelay = events.length ? playbackDuration(events, clockStart) : 320;
    schedulePlayback(() => {
      if (finalSession && !replay) {
        setSession(finalSession);
        setSelectedPlanId(
          finalSession.selected_plan_id ?? finalSession.plans[0]?.id ?? ""
        );
      }
      playbackPausedRef.current = false;
      setPlaybackPaused(false);
    setPedestriansPaused(false);
      setPlaybackEvents([]);
      setDisplayMinute(lastMinute);
      if (hasRecovered) {
        setDisplayPhase("recovered");
        if (!replay) send({ type: "RECOVERED" });
        setStoryBeat(replay
          ? "数字孪生回放完成"
          : "恢复完成，查看航班进度和旅客登机结果");
        setToast(replay
          ? "已从事件日志复现完整运行链路"
          : "真实执行账本已完成并通过资源校验");
      } else {
        const nextPhase = finalSession?.phase
          ?? (hasReplan ? "monitoring" : hasFailure ? "replanning" : "executing");
        if (!replay && nextPhase === "monitoring") send({ type: "MONITOR" });
        setDisplayPhase(nextPhase);
      }
      if (onComplete) {
        onComplete();
      } else {
        isPlayingRef.current = false;
        setIsPlaying(false);
      }
    }, finishDelay);
  };

  const settleOnlineExecution = (current: SessionState, reason?: string) => {
    setSession(current);
    setSelectedPlanId(current.selected_plan_id ?? current.plans[0]?.id ?? "");
    setDisplayPhase(current.phase);
    setDisplayMinute(current.scenario.current_minute);
    setPlaybackEvents([]);
    advanceInFlightRef.current = false;
    pendingAdvanceRef.current = null;
    isPlayingRef.current = false;
    setIsPlaying(false);
    playbackPausedRef.current = false;
    setPlaybackPaused(false);
    setPedestriansPaused(false);
    if (executionNeedsApproval(current)) {
      send({ type: "HUMAN_REVIEW" });
      setStoryBeat("原安排暂时无法继续，请审阅调整后的方案");
      setToast("Safety Critic 要求人工确认；已提交飞机保持原状态");
      return;
    }
    if (current.phase === "recovered") {
      send({ type: "RECOVERED" });
      setStoryBeat("机场运行稳定 · 恢复完成");
      return;
    }
    if (current.phase === "infeasible") {
      send({ type: "INFEASIBLE" });
      setStoryBeat("跑道、停机位或燃油要求有冲突，请先处理");
      setLabOpen(true);
      return;
    }
    setStoryBeat(reason ?? "执行已停止，可从当前位置继续");
  };

  const continueOnlineExecution = async (
    current: SessionState,
    hintedTarget: number | null,
    runToken: number,
  ): Promise<void> => {
    if (runToken !== onlineRunRef.current) return;
    if (!shouldRequestAdvance(current, {
      paused: playbackPausedRef.current,
      inFlight: advanceInFlightRef.current,
    })) {
      if (!playbackPausedRef.current) settleOnlineExecution(current);
      return;
    }
    const targetMinute = hintedTarget ?? nextExecutionMinute(current);
    if (targetMinute === null) {
      settleOnlineExecution(current, "没有可提交的下一里程碑；执行已安全停止");
      return;
    }
    const revision = current.execution?.revision ?? 0;
    const requestId = executionRequestId(current.id, revision, targetMinute);
    advanceInFlightRef.current = true;
    send({ type: "ADVANCE" });
    setStoryBeat(`正在执行：观察飞机与旅客的下一步`);
    try {
      const response = await advanceExecution(current.id, targetMinute, revision, requestId);
      advanceInFlightRef.current = false;
      if (runToken !== onlineRunRef.current) return;
      mergeReplay(response.execution_events);
      if (!executionMadeProgress(current, response)) {
        settleOnlineExecution(response.session, "执行暂未前进，已暂停以避免重复请求；可从当前进度继续");
        setError("后端没有提交新的时间或事件，已停止重复推进，不会重置已执行航班。");
        return;
      }
      if (playbackPausedRef.current) {
        pendingAdvanceRef.current = { response, runToken };
        setStoryBeat("已暂停，点击“继续演示”接着看");
        return;
      }
      beginPlayback(
        response.execution_events,
        false,
        response.session,
        () => void continueOnlineExecution(
          response.session,
          response.next_event_minute,
          runToken,
        ),
      );
    } catch (reason) {
      advanceInFlightRef.current = false;
      if (runToken !== onlineRunRef.current) return;
      if (reason instanceof ApiError && reason.status === 409) {
        try {
          const [fresh, history] = await Promise.all([
            getSession(current.id),
            getReplay(current.id),
          ]);
          if (runToken !== onlineRunRef.current) return;
          replayEventsRef.current = mergeExecutionEvents(replayEventsRef.current, history);
          setReplayEvents(replayEventsRef.current);
          const seen = new Set(
            visibleExecutionEventsRef.current.map((event) => event.sequence),
          );
          const unseen = history.filter((event) => !seen.has(event.sequence));
          const recoveredResponse: AdvanceExecutionResponse = {
            session: fresh,
            execution_events: unseen,
            next_event_minute: nextExecutionMinute(fresh),
            stopped_reason: "cursor_resynchronized",
            revision: fresh.execution?.revision ?? 0,
          };
          if (playbackPausedRef.current) {
            pendingAdvanceRef.current = { response: recoveredResponse, runToken };
            setStoryBeat("已暂停，点击“继续演示”接着看");
            return;
          }
          if (unseen.length) {
            beginPlayback(
              unseen,
              false,
              fresh,
              () => void continueOnlineExecution(
                fresh,
                recoveredResponse.next_event_minute,
                runToken,
              ),
            );
            return;
          }
          setSession(fresh);
          window.setTimeout(() => {
            void continueOnlineExecution(
              fresh,
              recoveredResponse.next_event_minute,
              runToken,
            );
          }, 0);
          return;
        } catch (syncReason) {
          settleOnlineExecution(
            current,
            syncReason instanceof Error ? syncReason.message : "执行游标同步失败",
          );
          setError(syncReason instanceof Error ? syncReason.message : "执行游标同步失败");
          return;
        }
      }
      settleOnlineExecution(
        current,
        reason instanceof Error ? reason.message : "执行推进失败",
      );
      setError(reason instanceof Error ? reason.message : "执行推进失败");
    }
  };

  const start = async (options: {
    mode: Mode;
    scenario_id: string;
    data_mode: DataMode;
    imported_scenario?: unknown;
    sandbox_flights?: number;
  }) => {
    setBusy(true);
    setError("");
    clearPlaybackTimers();
    onlineRunRef.current += 1;
    advanceInFlightRef.current = false;
    pendingAdvanceRef.current = null;
    setDirectorCue(null);
    setDirectorNext(undefined);
    setFlightLensId("");
    setResumeAfterFlightLens(false);
    setRetuneOpen(false);
    setCommand("");
    setPlaybackEvents([]);
    setReplayEvents([]);
    setVisibleExecutionEvents([]);
    replayEventsRef.current = [];
    visibleExecutionEventsRef.current = [];
    isPlayingRef.current = false;
    setDisplayPhase(null);
    setDisplayMinute(null);
    setIsPlaying(false);
    setPlaybackPaused(false);
    setPedestriansPaused(false);
    playbackPausedRef.current = false;
    playbackRateRef.current = .5;
    setPlaybackRateState(.5);
    guidedPlaybackRef.current = true;
    setGuidedPlayback(true);
    try {
      const next = await createSession(options);
      setDeckExpanded(false);
      setStoryGuideOpen(false);
      if(next.mode==="guided")setCommand(t("mainStoryCommand"));
      setStoryIntroAcknowledged(true);
      setStoryPreviewKey("");
      setPreviewOpenRequest(0);
      setSession(next);
      setDisplayMinute(next.scenario.current_minute);
      send({ type: "INCIDENT" });
      setStoryBeat(next.mode === "guided" ? "一条跑道暂时关闭，一个停机位被占用，需要重新安排降落" : "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法启动演练");
    } finally {
      setBusy(false);
    }
  };

  const parseCommand = async () => {
    if (!session || busy || !command.trim()) return;
    setBusy(true);
    setError("");
    try {
      const next = await submitIntent(session.id, command.trim());
      setSession(next);
      setCommand("");
      setToast(next.intent?.issues?.length
        ? intentCommandCopy[language].pending : intentCommandCopy[language].ready);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "意图解析失败");
    } finally {
      setBusy(false);
    }
  };

  const clarifyIntentIssue = async (issueIndex: number, text: string) => {
    if (!session || busy || !text.trim() || !session.intent?.needs_confirmation) return;
    const expectedVersion = session.intent.version;
    if (expectedVersion == null) {
      setError(intentCommandCopy[language].missingVersion);
      return;
    }
    setBusy(true);
    setError("");
    setToast("");
    try {
      const next = await clarifyIntent(session.id, expectedVersion, issueIndex, text.trim());
      setSession(next);
      setToast(next.intent?.issues?.length
        ? intentCommandCopy[language].pending : intentCommandCopy[language].ready);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        setError(reason.message);
        try {
          setSession(await getSession(session.id));
        } catch {
          // Keep the current draft and answer visible if refreshing is unavailable.
        }
      } else {
        setError(reason instanceof Error ? reason.message : t("error"));
      }
    } finally {
      setBusy(false);
    }
  };

  const confirm = async (approved: boolean) => {
    if (!session || busy) return;
    setBusy(true); setError("");
    try {
      const next = await confirmIntent(session.id, approved);
      setSession(next);
      if (!approved) setCommand("");
      if (approved) { setCommand(""); setDeckExpanded(false); setStoryGuideOpen(false); }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("error"));
    } finally { setBusy(false); }
  };

  const runPlanning = async (deep = false, replan = false) => {
    if (!session) return;
    setBusy(true);
    setError("");
    setDisplayPhase(replan ? "replanning" : "analyzing");
    send({ type: "PLAN" });
    setStoryBeat(replan
      ? (deep ? "Objective Copilot 正在深度评估新目标…" : "Objective Copilot 正在按新目标重算…")
      : (deep ? "深度模型评估边界条件…" : "专业 Agent 并行分析…"));
    try {
      const next = await generatePlans(session.id, deep, replan);
      setSession(next);
      setDisplayPhase(next.phase);
      if (next.plans.length) {
        setBriefView("agents");
        setSelectedPlanId(next.plans[0].id);
        setDeckExpanded(false);
        setStoryGuideOpen(false);
        setLayer("routes");
        setRetuneOpen(false);
        setCommand("");
        send({ type: "PLANS_READY" });
        setStoryBeat(language === "zh"
          ? `已生成 ${next.plans.length} 个候选 · 请核对各自证据与审批状态`
          : `${next.plans.length} candidates generated · review each evidence and approval status`);
      } else {
        send({ type: "INFEASIBLE" });
        setLabOpen(true);
      }
    } catch (reason) {
      setDisplayPhase(session.phase);
      setError(reason instanceof Error ? reason.message : "规划失败");
    } finally {
      setBusy(false);
    }
  };

  const observeAndReplan = async (queryId: string, returnToStory = false) => {
    if (!session || busy || isPlayingRef.current) return;
    const sessionId = session.id;
    const previousPlan = session.plans.find(plan => plan.id === selectedPlanId) ?? session.plans[0];
    let observedState: SessionState | undefined;
    const labels = {
      zh: {progress:"正在获取证据；收到后重新求解", done:"观测已保存，已生成新候选；查看证据更新回执", failed:"获取证据或重新规划失败"},
      en: {progress:"Acquiring evidence, then re-solving", done:"Observation saved; new candidates generated. Review the evidence receipt.", failed:"Observation or re-planning failed"},
    }[language];
    setBusy(true);
    setError("");
    setLabOpen(false);
    setStoryGuideOpen(false);
    setStoryPreviewKey("");
    setPreviewReceipt(null);
    setDisplayPhase("analyzing");
    send({ type: "PLAN" });
    setStoryBeat(labels.progress);
    try {
      const observed = await acquireObservation(sessionId, queryId, previousPlan?.id);
      observedState = observed;
      setSession(observed);
      setSelectedPlanId("");
      const next = await generatePlans(sessionId, false, true);
      setSession(next);
      setDisplayPhase(next.phase);
      if (next.plans.length) {
        setSelectedPlanId(observationReplacement(next, previousPlan)?.id ?? "");
        setLayer("routes");
        send({ type: "PLANS_READY" });
        setStoryBeat(labels.done);
        setToast(labels.done);
        setStoryGuideOpen(false); setBriefView("agents");
      } else {
        send({ type: "INFEASIBLE" });
        setStoryGuideOpen(false); setBriefView("agents");
      }
    } catch (reason) {
      // The observation is durable even if the following solve fails.
      setDisplayPhase(observedState?.phase ?? session.phase);
      setError(reason instanceof Error ? reason.message : labels.failed);
      setStoryGuideOpen(false); setBriefView("agents");
    } finally {
      setBusy(false);
    }
  };

  const choosePreference = async (
    leftPlanId: string,
    rightPlanId: string,
    preferredPlanId: string,
  ) => {
    if (!session) return;
    setBusy(true);
    setError("");
    try {
      const next = await submitPreference(
        session.id,
        leftPlanId,
        rightPlanId,
        preferredPlanId,
      );
      setSession(next);
      setToast(
        language === "zh"
          ? "偏好后验已更新；只重排安全候选，硬约束未改变"
          : "Preference posterior updated; hard constraints are unchanged",
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "偏好学习失败");
    } finally {
      setBusy(false);
    }
  };

  const forgetPreferenceMemory = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await resetPreferenceMemory();
      setToast(
        language === "zh"
          ? `已撤销 ${result.revoked} 条偏好记忆；下次规划生效，硬约束未改变`
          : `Revoked ${result.revoked} preference memories; effective on the next plan`,
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "撤销偏好记忆失败");
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!session) return;
    const continuing = canAdvanceExecution(session);
    if (!continuing && !selectedPlanId) return;
    clearPlaybackTimers();
    const runToken = onlineRunRef.current + 1;
    onlineRunRef.current = runToken;
    advanceInFlightRef.current = false;
    pendingAdvanceRef.current = null;
    playbackPausedRef.current = false;
    setPlaybackPaused(false);
    setPedestriansPaused(false);
    setBusy(true);
    setError("");
    isPlayingRef.current = true;
    setIsPlaying(true);
    setDisplayPhase("executing");
    setPlaybackEvents([]);
    send({ type: "APPROVE" });
    setStoryBeat(continuing
      ? "正在从保存的执行游标继续…"
      : "恢复方案已批准 · 正在提交第一条执行事件…");
    try {
      let approvedSession = session;
      let approvalEvents: ExecutionEvent[] = [];
      if (!continuing) {
        const response = await approvePlan(session.id, selectedPlanId);
        approvedSession = response.session;
        approvalEvents = response.execution_events;
      }
      if (runToken !== onlineRunRef.current) return;
      mergeReplay(approvalEvents);
      setSession(approvedSession);
      beginPlayback(
        approvalEvents,
        false,
        approvedSession,
        () => void continueOnlineExecution(
          approvedSession,
          nextExecutionMinute(approvedSession),
          runToken,
        ),
      );
    } catch (reason) {
      isPlayingRef.current = false;
      setIsPlaying(false);
      playbackPausedRef.current = false;
      setPlaybackPaused(false);
    setPedestriansPaused(false);
      setDisplayPhase(session.phase);
      setError(reason instanceof Error ? reason.message : "执行失败");
    } finally {
      setBusy(false);
    }
  };

  const openReplay = async (focus: ReplayFocus = "latest") => {
    if (!session || busy || isPlayingRef.current) return;
    try {
      const history = await getReplay(session.id);
      replayEventsRef.current = history;
      setReplayEvents(history);
      setReplayFocus(focus);
      setReplayOpen(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "回放读取失败");
    }
  };

  const playReplay = (events: ExecutionEvent[]) => {
    if (!hasHistoricalWorlds(events)) {
      setError(language === "zh" ? "缺少历史现场证据，不能播放地图。" : "Historical world evidence is missing.");
      return;
    }
    onlineRunRef.current += 1;
    advanceInFlightRef.current = false;
    pendingAdvanceRef.current = null;
    beginPlayback(events, true);
  };

  const seekReplay = (sequence: number) => {
    if (!session || busy || isPlayingRef.current) return;
    const selection = historicalSelection(replayEventsRef.current, session.id, sequence);
    if (!selection) {
      setError(t("replaySeekUnavailable"));
      return;
    }
    clearPlaybackTimers();
    onlineRunRef.current += 1;
    advanceInFlightRef.current = false;
    pendingAdvanceRef.current = null;
    isPlayingRef.current = false;
    playbackPausedRef.current = false;
    setIsPlaying(false);
    setPlaybackPaused(false);
    setPedestriansPaused(false);
    setHistoricalPlayback(true);
    visibleExecutionEventsRef.current = selection.prefix;
    setVisibleExecutionEvents(selection.prefix);
    setPlaybackEvents([]);
    setPlaybackToken(value => value + 1);
    setDisplayMinute(selection.event.sim_minute);
    setDisplayPhase(selection.world.phase);
    setDirectorCue(null);
    setDirectorNext(undefined);
    setStoryGuideOpen(false);
    setLabOpen(false);
    setRelayOpen(false);
    setFlightLensId("");
    setResumeAfterFlightLens(false);
    setReplayOpen(false);
    setLayer("conflicts");
    setStoryBeat(`${t("replaySelectedMoment")} · #${sequence} · ${formatSimulationMinute(selection.event.sim_minute)}`);
  };

  const exitHistoricalPlayback = async () => {
    if (!session || busy) return;
    clearPlaybackTimers();
    onlineRunRef.current += 1;
    const returningRun = onlineRunRef.current;
    isPlayingRef.current = false;
    playbackPausedRef.current = false;
    setIsPlaying(false);
    setPlaybackPaused(false);
    setPedestriansPaused(false);
    setBusy(true);
    setError("");
    try {
      const fresh = await getSession(session.id);
      if (returningRun !== onlineRunRef.current) return;
      await resumeSession(fresh);
      setStoryBeat(language === "zh" ? "已返回当前现场；历史回放未修改执行账本" : "Live state restored; replay never changed the execution ledger");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  };

  const selectedPlan = useMemo(
    () => session?.plans.find((plan) => plan.id === selectedPlanId) ?? session?.plans[0],
    [session?.plans, selectedPlanId],
  );
  const selectedActionDiff = session && selectedPlan
    ? candidateDecisionEvidence(session, selectedPlan.id).actionDiff : undefined;
  const selectedApprovalGate = selectedPlan
    ? session?.decision_lab.approval_gates?.[selectedPlan.id]
    : undefined;
  const approvalBlocked = Boolean(session && !canAdvanceExecution(session) && selectedApprovalGate && !selectedApprovalGate.can_human_approve);
  const effectiveMinute = displayMinute ?? session?.scenario.current_minute ?? 0;
  const mapSession = session
    ? presentedWorld(session, visibleExecutionEvents, effectiveMinute, historicalPlayback, isPlaying)
    : undefined;
  const historicalMap = historicalPlayback ? mapSession : undefined;
  const presentedScenario = mapSession?.scenario;
  const effectivePhase = (historicalPlayback || isPlaying ? mapSession?.phase : undefined)
    ?? displayPhase ?? session?.phase ?? "normal";
  const outcomeAudit = session?.decision_lab.outcome_audit;
  const showFinalAudit = !historicalPlayback && !isPlaying && effectivePhase === "recovered" && Boolean(outcomeAudit);
  const visiblePlans = historicalPlayback ? [] : (isPlaying || showFinalAudit) && selectedPlan
    ? [selectedPlan]
    : session?.plans ?? [];
  const explainFlightId = session && selectedPlan
    ? [...session.scenario.flights]
      .filter((flight) => selectedPlan.assignments.some((item) => item.flight_id === flight.id))
      .sort((left, right) => right.connections - left.connections)[0]?.id ?? ""
    : "";
  const transferPassengers = presentedScenario?.flights.reduce((sum, flight) => sum + flight.connections, 0) ?? 0;
  const recourse = mapSession?.decision_lab.recourse;
  const modelLabel: CopyKey = session?.llm_tier === "deep"
    ? "modelDeep"
    : session?.llm_tier === "fast"
      ? "modelFast"
      : "modelFallback";
  const guidedStory = session ? deriveGuidedStory({session, phase: effectivePhase, minute: effectiveMinute,
    playing: isPlaying, revealedEvents: visibleExecutionEvents, introAcknowledged: storyIntroAcknowledged,
    selectedPlanId: selectedPlan?.id ?? "", reviewKey: storyPreviewKey}) : undefined;
  const handleStoryAction = (action: StoryAction) => {
    if (!session || busy) return;
    setStoryGuideOpen(false);
    if (action === "intent") {
      setStoryIntroAcknowledged(true);
      setDeckExpanded(true);
      setRetuneOpen(Boolean(session.plans.length));
      if (!session.intent && !command.trim()) setCommand(t("mainStoryCommand"));
      window.requestAnimationFrame(() => commandInputRef.current?.focus());
    } else if (action === "plan") void runPlanning(false, Boolean(session.plans.length));
    else if (action === "preview") setPreviewOpenRequest(value => value + 1);
    else if (action === "evidence" || action === "lab") setLabOpen(true);
    else if (action === "replay") void openReplay("disruption");
    else if (action === "continue") { if (isPlaying) resumePlayback(); else void approve(); }
    else if (action === "approve" && guidedStory?.canApprove) void approve();
  };
  const nextAction = session ? audienceNextAction({session,plan:selectedPlan,playing:isPlaying,paused:playbackPaused,
    previewSelected:Boolean(guidedStory?.previewSelected),repairReviewed:repairReviewedPlanId===selectedPlan?.id},language) : undefined;
  const handlePrimaryAction = () => {
    if(!session||busy||!nextAction)return;
    if(nextAction.action==="pause"){pausePlayback();return;}
    if(nextAction.action==="resume"){setBriefView(null);resumePlayback();return;}
    if(nextAction.action==="observe"){if(nextAction.queryId)void observeAndReplan(nextAction.queryId);return;}
    if(nextAction.action==="repair"){setBriefView("agents");setRepairReviewedPlanId(selectedPlan?.id??"");return;}
    if(nextAction.action==="preview"){setBriefView(null);setPreviewOpenRequest(v=>v+1);return;}
    if(nextAction.action==="approve"){setBriefView(null);void approve();return;}
    pausePlayback();setLabOpen(true);
  };
  const layers: Array<{ id: MapLayer; icon: typeof AlertTriangle; label: CopyKey }> = [
    { id: "conflicts", icon: AlertTriangle, label: "layerConflict" },
    { id: "routes", icon: Plane, label: "layerRoutes" },
    { id: "passengers", icon: Users, label: "layerPassengers" },
    { id: "risk", icon: Activity, label: "layerRisk" },
  ];

  return (
    <main className={`app-shell ${session ? "map-first audience-mode" : ""} ${deckExpanded || retuneOpen || session?.intent?.needs_confirmation ? "details-open" : "details-compact"} ${session?.intent && !session.intent.needs_confirmation && !session.plans.length && !command.trim() ? "goal-ready" : ""}`}>
      {(!session || mapSession) && <AirportWorld
        session={mapSession}
        layer={layer}
        selectedPlanId={historicalPlayback || isPlaying ? mapSession?.selected_plan_id ?? undefined : selectedPlan?.id}
        focusFlightId={session?.mode === "guided" ? mapSession?.execution?.connection?.source_flight_id ?? guidedStory?.focusFlight : undefined}
        executionEvents={visibleExecutionEvents}
        playbackEvents={playbackEvents}
        playbackToken={playbackToken}
        phase={effectivePhase}
        language={language}
        playbackMinute={effectiveMinute}
        playbackRate={playbackRate}
        playbackPaused={playbackPaused}
        pedestriansPaused={pedestriansPaused}
        historicalPlayback={historicalPlayback}
        onFlightSelect={historicalPlayback ? undefined : inspectFlight}
        onPassengerStageChange={(sessionId,sourceFlightId,stage)=>setVisualPassenger({sessionId,sourceFlightId,stage})}
      />}
      {(historicalPlayback || isPlaying) && !mapSession && <div role="status">{language === "zh" ? "等待事件现场快照" : "Waiting for event world snapshot"}</div>}
      {!session ? (
        <ModePicker language={language} setLanguage={setLanguage} onStart={start} onResume={resumeSession} busy={busy} />
      ) : (
        <>
          <header className="top-hud">
            <div className="wordmark"><Plane size={20} /><strong>{t("appName")}</strong><span>{session.scenario.airport}</span></div>
            <div className="mission-status glass">
              <span><small>{language==="zh"?"模拟时间":"MISSION"}</small><b>{formatSimulationMinute(effectiveMinute)}</b></span>
              <i />
              <span><small>{t("weather")}</small><b className="weather-level"><CloudLightning size={14} /> L{presentedScenario?.weather.severity ?? "—"}</b></span>
              <i />
              <span><small>{language==="zh"?"当前状态":"STATUS"}</small><b className={effectivePhase}>{phaseLabels[language][effectivePhase]}</b></span>
            </div>
            <div className="top-actions"><details className="header-menu"><summary>{language==="zh"?"更多":"More"} <ChevronDown size={14}/></summary><div className="more-items" onClick={(event)=>{if((event.target as HTMLElement).closest("button"))event.currentTarget.closest("details")?.removeAttribute("open");}}>
              <button aria-expanded={deckExpanded || retuneOpen || Boolean(session.intent?.needs_confirmation)} aria-controls="decision-deck" onClick={() => { setDeckExpanded((value) => !value); setRetuneOpen(false); }}>
                <Layers3 size={15} /> {deckExpanded ? t("collapseDecisionDetails") : t("decisionDetails")}
              </button>
              {historicalPlayback && <button onClick={exitHistoricalPlayback}>{language === "zh" ? "只读回放 · 返回当前现场" : "Read-only replay · Return live"}</button>}
              <button onClick={resetToLobby} disabled={isPlaying}><Plus size={15} /> {t("newExercise")}</button>
              <button onClick={() => void openReplay()} disabled={isPlaying || busy}><RotateCcw size={15} /> {t("replay")}</button>
              <button onClick={() => setLanguage(language === "zh" ? "en" : "zh")}>{language === "zh" ? "EN" : "中文"}</button>
            </div></details></div>
          </header>

          <section className="situation-strip glass">
            <span><CloudLightning size={15} /><small>{presentedScenario ? weatherLabel(presentedScenario.weather.label, presentedScenario.weather.severity, language) : "—"}</small></span>
            <span><Gauge size={15} /><small>{presentedScenario?.runways.filter((item) => runwayStateAt(item, effectiveMinute, true) !== "closed").length ?? "—"}/{presentedScenario?.runways.length ?? "—"} {t("runway")}</small></span>
            <span><Plane size={15} /><small>{presentedScenario?.flights.length ?? "—"} {t("flights")}{mapSession?.execution?.connection ? (language==="zh"?" + 1 班转机后出发":" + 1 connecting departure") : ""}</small></span>
            <span><Users size={15} /><small>{transferPassengers} {t("transferPassengers")}</small></span>
            <em className="rehearsal-label" title={t(modelLabel)+" · "+dataBadgeLabel(session.data_mode, session.data_badge, language)}>{language==="zh"?"模拟演练":"Simulation"}</em>
          </section>

          <div className="story-beat">
            {!historicalPlayback && session.mode === "guided" && guidedStory && <GuidedStory
              state={guidedStory} language={language} busy={busy} playing={isPlaying} paused={playbackPaused}
              plan={selectedPlan} plans={session.plans} gate={selectedApprovalGate} traces={session.traces}
              decisionReceipts={<>
                <EvidenceReceipt session={session} selectedPlanId={selectedPlan?.id ?? ""} language={language}
                  previewReviewed={Boolean(selectedPlan && storyPreviewKey === storyReviewKey(session, selectedPlan.id))} />
                <PreviewSelectionReceipt receipt={previewReceipt} session={session} selectedPlanId={selectedPlan?.id ?? ""} language={language} />
              </>}
              onSelectPlan={id => { setSelectedPlanId(id); setLayer("routes"); }}
              onObserve={queryId => void observeAndReplan(queryId, true)}
              canResume={canAdvanceExecution(session)} needsApproval={executionNeedsApproval(session)}
              fallback={session.llm_tier === "fallback"} open={storyGuideOpen}
              onOpen={() => { if (isPlaying && !playbackPaused) pausePlayback(); setStoryGuideOpen(true); }}
              onClose={() => setStoryGuideOpen(false)} onAction={handleStoryAction} />}
            <span />
            <b>{runtimeText(storyBeat, language) || phaseLabels[language][effectivePhase]}</b>
            {recourse && ["replanning", "monitoring", "recovered"].includes(effectivePhase) && (
              <em className="recourse-chip">
                {language==="zh"?`调整 ${recourse.affected_count} 架 · 保留 ${recourse.frozen_count} 架原安排`:`Adjust ${recourse.affected_count} · Keep ${recourse.frozen_count}`}
              </em>
            )}
            {!historicalPlayback && explainFlightId && (
              <button className="story-flight-lens" aria-label={language === "zh" ? "查看航班解释" : "Open Flight Lens"} onClick={() => inspectFlight(explainFlightId)}>
                <Plane size={12} /> {language === "zh" ? "航班解释" : "Flight Lens"}
              </button>
            )}
          </div>
          {!historicalPlayback && (
            <div className="playback-controls glass" role="group" aria-label={t("demoSpeed")}>
              <label htmlFor="playback-speed">{language === "zh" ? "画面速度" : "Speed"}</label>
              <select id="playback-speed" value={playbackRate}
                onChange={(event) => changePlaybackRate(Number(event.target.value))}>
                <option value={.25}>{language === "zh" ? "0.25× · 很慢" : "0.25× · Very slow"}</option>
                <option value={.5}>{language === "zh" ? "0.5× · 慢速" : "0.5× · Slow"}</option>
                <option value={1}>{language === "zh" ? "1× · 正常" : "1× · Normal"}</option>
                <option value={2}>{language === "zh" ? "2× · 快速" : "2× · Fast"}</option>
              </select>
              <i />
              <button
                className={`briefing-toggle ${guidedPlayback ? "active" : ""}`}
                aria-pressed={guidedPlayback}
                aria-label={language === "zh" ? "关键节点自动暂停" : "Auto-pause at key decisions"}
                onClick={() => setGuidedPlayback((current) => {
                  const next = !current;
                  guidedPlaybackRef.current = next;
                  return next;
                })}
              >
                <BrainCircuit size={12} />
                <b>{language === "zh" ? "讲解模式" : "Briefing"}</b>
              </button>
              <i />
              <button
                className="playback-toggle"
                aria-label={playbackPaused ? t("resumePlayback") : t("pausePlayback")}
                onClick={playbackPaused ? resumePlayback : pausePlayback}
              >
                {playbackPaused ? <Play size={12} /> : <Pause size={12} />}
                <b>{playbackPaused ? t("resumePlayback") : t("pausePlayback")}</b>
              </button>
            </div>
          )}
          {!historicalPlayback && <AgentDock session={session} language={language} onOpen={() => { pausePlayback(); setDeckExpanded(false); setBriefView(v=>v==="agents"?null:"agents"); setRepairReviewedPlanId(selectedPlan?.id??""); }} onPassengers={() => { pausePlayback(); setDeckExpanded(false); setLayer("passengers"); setBriefView(v=>v==="passengers"?null:"passengers"); }} />}
          <nav className="layer-switch glass" aria-label={language === "zh" ? "地图图层" : "Map layers"}>
            {layers.map(({ id, icon: Icon, label }) => (
              <button key={id} disabled={isPlaying} aria-label={t(label)} className={layer === id ? "active" : ""} onClick={() => setLayer(id)}>
                <Icon size={14} /><span>{t(label)}</span>
              </button>
            ))}
          </nav>

          {!historicalPlayback && session.mode === "sandbox" && (
            <SandboxTray session={session} language={language} onSession={setSession} onError={setError} />
          )}

          <section id="decision-deck" aria-label={t("decisionControls")} className={`command-deck glass ${session.plans.length ? "with-plans" : ""}`}>
            {session.plans.length > 0 ? (
              <>
                <div className="plans-row">
                  {briefView && mapSession ? <AudienceBrief view={briefView} session={session} plan={selectedPlan} world={mapSession} events={visibleExecutionEvents} minute={effectiveMinute} language={language} paused={isPlaying&&playbackPaused} onClose={()=>setBriefView(null)} onDetails={()=>{pausePlayback();if(briefView==="agents")setRelayOpen(true);else setPassengersOpen(true);}}/> : <>
                  {visiblePlans.map((plan) => (
                    <PlanCard
                      key={plan.id}
                      plan={plan}
                      selected={selectedPlan?.id === plan.id}
                      onSelect={() => {
                        setSelectedPlanId(plan.id);
                        setLayer("routes");
                      }}
                      mitigation={session.decision_lab.mitigation_by_plan?.[plan.id]}
                      approvalGate={session.decision_lab.approval_gates?.[plan.id]}
                      language={language}
                    />
                  ))}
                  {mapSession?.execution?.connection?.source_arrival_minute != null ? (
                    <ConnectionJourney connection={!historicalPlayback&&visualPassenger?.sessionId===mapSession.id&&visualPassenger.sourceFlightId===mapSession.execution.connection.source_flight_id
                      ? {...mapSession.execution.connection,passenger_stage:visualPassenger.stage}:mapSession.execution.connection} language={language} paused={pedestriansPaused}/>
                  ) : !historicalPlayback && isPlaying && directorCue && (
                    <ExecutionGuide
                      language={language}
                      cue={(() => {
                        const operation = milestonesFromEvent(directorCue.event);
                        const stage = operation ? aircraftStageAt(operation, effectiveMinute) : undefined;
                        return operation ? { ...directorCue,
                          step: stage === "runway" ? 2 : stage === "taxiing" ? 3 : stage === "at_gate" || stage === "released" ? 4 : 1,
                        } : directorCue;
                      })()}
                      next={directorNext}
                      session={session}
                      slots={historicalPlayback ? 3 : 2}
                    />
                  )}
                  {historicalPlayback && historicalMap && (
                    <section className="execution-guide span-3" role="status">
                      <header>
                        <span><History size={14} /></span>
                        <div><small>HISTORICAL WORLD · EVENT #{visibleExecutionEvents.at(-1)?.sequence}</small>
                          <strong>{language === "zh" ? "只读历史现场" : "Read-only historical world"}</strong></div>
                        <em>T+{historicalMap.scenario.current_minute}</em><i>REPLAY</i>
                      </header>
                      <div className="director-focus">
                        <strong>{phaseLabels[language][historicalMap.phase]}</strong>
                        <p>{historicalMap.scenario.runways.map((runway) => `${runway.id}: ${runway.status}`).join(" · ")}</p>
                        <div>
                          <span><b>{language === "zh" ? "已提交" : "COMMITTED"}</b>{historicalMap.execution?.committed_operations.length ?? 0} {language === "zh" ? "架航班" : "flights"}</span>
                          <span><b>{language === "zh" ? "事件" : "EVENT"}</b>{executionEventLabel(visibleExecutionEvents.at(-1)?.type ?? "", language)}</span>
                        </div>
                      </div>
                      <footer>{language === "zh"
                        ? "只显示该事件记录的现场；候选结论和当前验收报告不混入历史。退出回放不会改变执行记录。"
                        : "Only the recorded world is shown. Present-day conclusions and audit reports are excluded; replay never changes the execution ledger."}</footer>
                    </section>
                  )}
                  {showFinalAudit && outcomeAudit && !mapSession?.execution?.connection && (
                    <OutcomeAuditCard
                      audit={outcomeAudit}
                      committedCount={session.execution?.committed_operations.length ?? 0}
                      scenarioFlightCount={session.scenario.flights.length}
                      language={language}
                      onOpen={() => setLabOpen(true)}
                    />
                  )}
                  {!historicalPlayback && !isPlaying && !showFinalAudit && !mapSession?.execution?.connection?.source_arrival_minute && visiblePlans.length < 3 && (
                    selectedActionDiff ? (
                      <ActionDiffStory
                        diff={selectedActionDiff}
                        committedCount={undefined}
                        slots={visiblePlans.length === 1 ? 2 : 1}
                        language={language}
                        onOpen={() => setLabOpen(true)}
                      />
                    ) : session.traces.length > 0 ? (
                      <AgentRelay
                        traces={session.traces}
                        slots={visiblePlans.length === 1 ? 2 : 1}
                        language={language}
                        onOpen={() => { pausePlayback(); setRelayOpen(true); }}
                      />
                    ) : null
                  )}
                  </>}
                </div>
                <div className="plan-actions">
                  <p className="next-step-hint">{nextAction?.hint}</p>
                  <button className="primary primary-next" onClick={handlePrimaryAction} disabled={busy||historicalPlayback} aria-label={nextAction?.label}>{busy?<Activity className="spin" size={16}/>:<Play size={16}/>} {busy?(language==="zh"?"正在处理…":"Working…"):nextAction?.label}</button>
                  <details className="action-menu"><summary>{language==="zh"?"更多操作":"More options"} <ChevronDown size={13}/></summary><div className="more-items">
                  <BranchPreviewButton session={session} language={language} disabled={busy || isPlaying || historicalPlayback}
                    openRequest={previewOpenRequest}
                    onSelect={(id, receipt) => { setSelectedPlanId(id); setPreviewReceipt(receipt); setLayer("routes"); setStoryPreviewKey(storyReviewKey(session, id)); setStoryGuideOpen(false); setBriefView(null); }} />
                  <button className={`retune-button ${retuneOpen ? "active" : ""}`} onClick={() => setRetuneOpen((open) => !open)} disabled={isPlaying || historicalPlayback}>
                    <Radio size={15} /> {t("retune")}
                  </button>
                  <button className="lab-button" disabled={historicalPlayback} onClick={() => {pausePlayback();setLabOpen(true);}}>
                    <FlaskConical size={15} /> {t("decisionLab")}
                  </button>
                  </div></details>
                </div>
                {!historicalPlayback && !isPlaying && !showFinalAudit && (
                  <ActiveSensingCue
                    session={session}
                    selectedPlanId={selectedPlan?.id ?? ""}
                    language={language}
                    busy={busy}
                    onObserve={(queryId) => void observeAndReplan(queryId)}
                  />
                )}
                {retuneOpen && (
                  <aside className="objective-copilot glass">
                    <header>
                      <span><BrainCircuit size={17} /></span>
                      <div><small>STRUCTURED INTENT · HUMAN CONFIRMATION</small><strong>{t("retuneTitle")}</strong><p>{t("retuneHint")}</p></div>
                      <button onClick={() => setRetuneOpen(false)} aria-label={t("close")}><X size={16} /></button>
                    </header>

                {pendingClarification && <p className="intent-command-replacement-note">{intentCommandCopy[language].replacementHint}</p>}
                    <div className="command-input">
                      <Radio size={17} />
                      <input
                        ref={commandInputRef}
                        value={command}
                        onChange={(event) => setCommand(event.target.value)}
                        onKeyDown={(event) => event.key === "Enter" && parseCommand()}
                        disabled={busy}
                        placeholder={pendingClarification ? intentCommandCopy[language].fullPlaceholder : t("commandPlaceholder")}
                      />
                      <VoiceControl onText={setCommand} onError={setError} language={language} />
                      <button className="parse-button" onClick={parseCommand} disabled={busy || !command.trim()}>
                        {pendingClarification ? intentCommandCopy[language].parseAll : t("parseCommand")}
                      </button>
                    </div>
                    {session.intent?.needs_confirmation && (
                      <IntentPreview
                        key={session.id + ":" + (session.intent.version ?? 0)}
                        intent={session.intent}
                        busy={busy}
                        onClarify={(issueIndex, text) => void clarifyIntentIssue(issueIndex, text)}
                        onReject={() => confirm(false)}
                        onConfirm={() => confirm(true)}
                        language={language}
                      />
                    )}
                    {session.intent && !session.intent.needs_confirmation && !command.trim() && (
                      <div className="coordination-actions">
                        <span><ShieldCheck size={14} /> {t("safetyPipeline")}</span>
                        <button className="deep-button" onClick={() => runPlanning(true, true)} disabled={busy}>
                          <BrainCircuit size={14} /> {t("deepReplan")}
                        </button>
                        <button className="primary" onClick={() => runPlanning(false, true)} disabled={busy}>
                          {busy ? <Activity className="spin" size={15} /> : <RotateCcw size={15} />} {t("fastReplan")}
                        </button>
                      </div>
                    )}
                  </aside>
                )}
              </>
            ) : (
              <>
                {briefView && mapSession && <AudienceBrief view={briefView} session={session} plan={selectedPlan} world={mapSession} events={visibleExecutionEvents} minute={effectiveMinute} language={language} paused={false} onClose={()=>setBriefView(null)} onDetails={()=>briefView==="passengers"?setPassengersOpen(true):setRelayOpen(true)}/>}
                {pendingClarification && <p className="intent-command-replacement-note">{intentCommandCopy[language].replacementHint}</p>}
                <div className="command-input">
                  <Radio size={17} />
                  <input
                    ref={commandInputRef}
                    value={command}
                    onChange={(event) => setCommand(event.target.value)}
                    onKeyDown={(event) => event.key === "Enter" && parseCommand()}
                    disabled={busy}
                    placeholder={pendingClarification ? intentCommandCopy[language].fullPlaceholder : t("commandPlaceholder")}
                  />
                  <VoiceControl onText={setCommand} onError={setError} language={language} />
                  <button className="parse-button" onClick={parseCommand} disabled={busy || !command.trim()}>
                    {pendingClarification ? intentCommandCopy[language].parseAll : t("parseCommand")}
                  </button>
                </div>
                {session.intent?.needs_confirmation && (
                  <IntentPreview
                    key={session.id + ":" + (session.intent.version ?? 0)}
                    intent={session.intent}
                    busy={busy}
                    onClarify={(issueIndex, text) => void clarifyIntentIssue(issueIndex, text)}
                    onReject={() => confirm(false)}
                    onConfirm={() => confirm(true)}
                    language={language}
                  />
                )}
                <div className="coordination-actions">
                  <span>{language==="zh"?(session.intent?.needs_confirmation?"下一步：核对并确认目标。":session.intent&&!command.trim()?"下一步：生成候选方案。":"下一步：输入目标，再点击「理解目标」。"):"Enter a goal, confirm it, then generate plans."}</span>
                  {session.phase === "infeasible" && session.decision_lab.infeasible && (
                    <button className="core-open-button" onClick={() => setLabOpen(true)}>
                      <AlertTriangle size={14} /> {language === "zh" ? "查看不可行证书" : "View infeasibility proof"}
                    </button>
                  )}
                  <button className="deep-button" onClick={() => runPlanning(true)} disabled={busy || Boolean(session.intent?.needs_confirmation)}>
                    <BrainCircuit size={14} /> {t("deepReplan")}
                  </button>
                  <button className="primary" hidden={!session.intent||session.intent.needs_confirmation||Boolean(command.trim())} onClick={() => runPlanning(false)} disabled={busy || Boolean(session.intent?.needs_confirmation)}>
                    {busy ? <Activity className="spin" size={16} /> : <Play size={16} />} {t("startCoordination")}
                  </button>
                </div>
              </>
            )}
          </section>
          <div className="machine-indicator">{String(machineState.value)}</div>
        </>
      )}
      {error && <div className="error-toast"><AlertTriangle size={16} /><span>{runtimeText(error, language)}</span><button onClick={() => setError("")}><X size={15} /></button></div>}
      {toast && <div className="success-toast"><Check size={16} />{runtimeText(toast, language)}</div>}
      {session && labOpen && (
        <DecisionLab
          events={visibleExecutionEvents}
          session={session}
          selectedPlanId={selectedPlan?.id ?? ""}
          onSelect={(planId) => { setSelectedPlanId(planId); setLayer("routes"); }}
          onObserve={(queryId) => void observeAndReplan(queryId)}
          onPreference={(left, right, preferred) => void choosePreference(left, right, preferred)}
          onResetPreference={() => void forgetPreferenceMemory()}
          observing={busy}
          onClose={() => setLabOpen(false)}
          language={language}
        />
      )}
      {session && relayOpen && <AgentRelayViewer session={session} playbackPaused={isPlaying && playbackPaused} planId={selectedPlan?.id} traces={session.traces} onClose={() => setRelayOpen(false)} language={language} />}
      {passengersOpen && mapSession && <PassengerPanel playbackPaused={isPlaying && playbackPaused} world={mapSession} events={visibleExecutionEvents} minute={effectiveMinute} language={language} onClose={() => setPassengersOpen(false)}/> }
      {session && flightLensId && (
        <FlightLens session={session} plan={selectedPlan} flightId={flightLensId} language={language} onClose={closeFlightLens} onResume={resumeAfterFlightLens ? resumeFromFlightLens : undefined} onFlightChange={setFlightLensId} />
      )}
      {replayOpen && (
        <ReplayPanel
          events={replayEvents}
          onClose={() => setReplayOpen(false)}
          onPlay={playReplay}
          onSeek={seekReplay}
          initialFocus={replayFocus}
          language={language}
        />
      )}
    </main>
  );
}
