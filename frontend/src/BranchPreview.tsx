import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FlaskConical, Pause, Play, X } from "lucide-react";
import { createBranchPreview, getBranchPreview } from "./api";
import {
  deliverPreviewSelection, eligibleRunwayDelays, mergePreviewReports, previewFlightStage, previewIsCurrent,
  type BranchPreviewReport, type EventRehearsal, type PreviewBranch, type PreviewCollection,
  type PreviewSelectionReceiptData, type RunwayDelayHypothesis,
} from "./branchPreview";
import type { Runway, SessionState } from "./types";
import {planTitleLabel, type UiLanguage} from "./localization";
import "./branchPreview.css";

const words = {
  zh: {
    hypothesisTitle: "跑道晚开放 · 名义对照", runway: "假设涉及的跑道",
    hypothesisAction: "假设", laterOpen: "晚10分钟开放", noRunway: "当前没有仍关闭且预计将在未来开放的跑道。",
    hypothesisLoading: "正在对全部原候选进行晚开放名义对照…",
    originalReopen: "原预计开放", delayedReopen: "假设晚开放", endOrStop: "结束 / 停止时刻",
    result: "名义预演结果", notice: "收到通知", reopen: "预计开放时刻",
    noticeBoundary: "晚开放通知后的检查", eventBoundary: "首次事件后的检查",
    eventStillValid: "首次事件后，原计划仍满足名义约束。",
    eventScope: "这只是该次事件的检查；后续事件仍可能使计划失效。",
    noticeNotLocated: "尚未定位晚开放通知的检查，以下依据属于首次事件。",
    noticeStillValid: "收到晚开放通知时，原计划仍满足名义约束。",
    noticeScope: "这只是通知时的检查；后续事件仍可能使计划失效。",
    noticeMore: "这里只列前2条，完整依据见下方假设晚开放轨迹。",
    noticeUnknown: "尚无通知时的决策边界记录，请查看完整轨迹。",
    nominalControls: "相同现场、目标和后续事件（含 TUG 故障）；仅新增当前收到的晚开放通知。两侧最多各进行2次 CP-SAT 修正。",
    nominalCaveat: "改动航班数只计已接受的修正；未完成的分支不能据此判优。这不是50次随机统计，也没有改变正式现场。",
    missingHypothesis: "该候选缺少完整的晚开放对照结果，请重新预演。",
    originalTrace: "原预计开放 · 完整轨迹", hypothesisTrace: "假设晚开放 · 完整轨迹",
    rawAggregate: "原快照 · 50次随机仿真统计（不含晚开放假设与后续事件）",
    rawSample: "下面是原现场的第 1 次模拟：不含晚开放假设，不是平均结果",
    rawClock: "原快照示例时钟", rawPlay: "播放原快照示例", rawPause: "暂停原快照示例",
    rawLimit: "下方随机统计与示例播放仍基于原快照；晚开放结果只在各卡上方的名义对照中呈现。",
    selectOriginal: "选择原候选，返回审批", noFeedback: "晚开放假设与模型反馈不能同时运行；请先重新运行普通预演。",
    mergeError: "反馈结果与当前候选对照不匹配，请重新预演。",
    feedbackRun: "反馈修正对照（可调用本地模型）", feedbackTitle: "普通求解器 vs 反馈修正",
    baseline: "普通 CP-SAT", controller: "反馈修正", changed: "改动安排的航班", finish: "完成时刻",
    modelCalls: "模型调用", rule: "规则反馈降级", fast: "本地模型建议", proposalTitle: "建议与工具反馈",
    preserve: "保留指定安排后重算", replan: "重算剩余航班", escalate: "请人工决策",
    compareLimit: "固定同一现场、事件和目标；双方最多求解两次。实际调用次数可能不同，模型还有额外时延。这是名义执行对照，不保证反馈方案更好。改动数量只计已经接受的修正；未完成的分支不能据此判优。",
    feedbackLimit: "模型只选择工具和待保留航班；求解器生成动作、独立校验后才在预演中继续。这里没有批准任何动作，也未改变现场。",
    notCalled: "本次无需建议，未调用模型", selectedOriginal: "这里只能选择原候选返回审批，不能批准假设修正。",
    rehearsal: "后续事件预演", nominal: "按计划时间推进 · 与上方随机统计分开",
    scheduled: "同一组事件", boundary: "第一次需要决策", violations: "原计划失效依据",
    repaired: "假设允许修正后的结果", completed: "本轮航班已处理完", blocked: "仍需人工决策",
    solver: "求解次数", repair: "假设修正", approvedNo: "仅预演，未获批准",
    distinction: "这是普通 CP-SAT 修正基线，尚不是 Agent 自主修正；选择按钮只选择原候选，不采用这里的假设修正。",
    trace: "查看完整执行接力", before: "修正前", after: "修正后", pending: "尚未执行",
    diverted: "安全备降", releasedCount: "完成周转", future: "尚未处理的排定事件",
    unchanged: "该事件未使剩余计划违反名义约束", noIncident: "无已排定的后续事件",
    stop: "停止原因",
    validRepair: "名义约束通过，未批准", infeasibleRepair: "未找到可行修正", rejectedRepair: "独立校验未通过",
    open: "预演不同未来", title: "选哪个方案？比较飞机安排和旅客影响", close: "返回现场",
    subtitle: "把同一批飞机按不同方案模拟一遍，看看等待和误机人数有什么不同。选中方案后，还需要回到主界面批准。",
    loading: "正在同一组随机世界中试运行…", retry: "从最新现场重新预演",
    stale: "现场已变化：这是旧快照，只可回看，不能据此选择方案。",
    scope: "待执行航班", passengers: "本轮转机旅客", excluded: "未计入本轮",
    evidence: "计算范围与边界", invalid: "当前现场不可行", select: "选择此方案，返回审批",
    selecting: "正在核验现场…", delay: "平均延误", missed: "预计错过转机", p90: "P90 延误",
    risk: "出现燃油风险的样本比例", proof: "名义计划约束检查",
    sample: "下面播放第 1 次模拟：这些是这一次的结果，不是平均结果，也不是原计划时间表", aggregate: "当前快照 · 50 次随机仿真统计（不含后续事件）",
    play: "播放预演", pause: "暂停预演", clock: "预演时钟",
    waiting: "还没落地", landing: "已落地，正在跑道减速", taxiing: "滑行或等机位",
    turnaround: "停靠，下客和地面服务", released: "服务结束，机位可交给下一架",
    fixed: "已执行部分 · 保留原现场记录，不重新模拟",
    limitation: "这是基于当前快照与天气扰动的条件预演，不自动注入未来故障；下方事件预演另用计划时间推进，不与这些随机统计混算，也不是真实机场预测。选择后仍须在主界面通过审批。",
    counts: "指标只覆盖上述待执行航班；已执行航班不被算成新的成功结果。",
    notApproval: "检查通过不等于审批通过；随机扰动仍可能产生风险。",
    empty: "尚未生成可预演的方案", failure: "预演失败，请重试。",
    returnError: "现场已变化，请重新预演。", minutes: "分钟", people: "人",
  },
  en: {
    hypothesisTitle: "Delayed runway reopening · nominal comparison", runway: "Runway in this hypothesis",
    hypothesisAction: "Assume", laterOpen: "reopens 10 minutes later", noRunway: "No closed runway currently has a future reopening estimate.",
    hypothesisLoading: "Comparing delayed reopening for all original candidates…",
    originalReopen: "Original reopening", delayedReopen: "Delayed reopening", endOrStop: "End / stop minute",
    result: "Nominal rehearsal result", notice: "Notice received", reopen: "Expected reopening",
    noticeBoundary: "Check after the delayed-reopening notice", eventBoundary: "Check after the first event",
    eventStillValid: "After the first event, the original plan still meets the nominal constraints.",
    eventScope: "This is only the check for that event; later events may still invalidate the plan.",
    noticeNotLocated: "The delayed-reopening notice check was not located; the evidence below belongs to the first event.",
    noticeStillValid: "At receipt of the delay notice, the original plan still meets the nominal constraints.",
    noticeScope: "This is only the check at the notice; later events may still invalidate the plan.",
    noticeMore: "Only the first two failures are shown here; see the delayed-reopening trace for all evidence.",
    noticeUnknown: "No decision boundary was recorded for the notice. Inspect the full trace.",
    nominalControls: "Same snapshot, objectives and later events, including the TUG failure. Only the new delay notice is added now. Each side allows at most two CP-SAT repairs.",
    nominalCaveat: "Changed-flight counts include accepted repairs only and cannot rank incomplete runs. This is not the 50-world simulation and does not change the live airport.",
    missingHypothesis: "This candidate has no complete delayed-reopening comparison. Run the preview again.",
    originalTrace: "Original reopening · full trace", hypothesisTrace: "Delayed reopening · full trace",
    rawAggregate: "Original snapshot · 50-world statistics (excluding the delay hypothesis and later events)",
    rawSample: "Original snapshot sample world #1 · no delayed-reopening hypothesis, not an average",
    rawClock: "Original sample clock", rawPlay: "Play original sample", rawPause: "Pause original sample",
    rawLimit: "Random-world statistics and sample playback below still use the original snapshot. Delayed-reopening results appear only in the nominal comparison at the top of each card.",
    selectOriginal: "Select original candidate for approval", noFeedback: "The delay hypothesis cannot run with model feedback. Run a regular preview first.",
    mergeError: "The feedback result does not match the current candidate comparison. Preview again.",
    feedbackRun: "Compare feedback repair (may call local model)", feedbackTitle: "Solver vs feedback repair",
    baseline: "Ordinary CP-SAT", controller: "Feedback repair", changed: "Flights with changed assignments", finish: "Completion minute",
    modelCalls: "Model calls", rule: "Rule-based fallback", fast: "Local model proposal", proposalTitle: "Proposals and tool feedback",
    preserve: "Preserve selected assignments, then solve", replan: "Replan pending flights", escalate: "Request operator",
    compareLimit: "Same snapshot, incidents and objectives; at most two solver calls each. Actual calls may differ and models add latency. Nominal execution only, with no promised improvement. Change counts cover accepted repairs only and cannot rank incomplete runs.",
    feedbackLimit: "The model selects tools and pending flights to preserve. The solver generates actions, independently checked before continuation in preview. No live approval or execution.",
    notCalled: "No proposal needed; model not called", selectedOriginal: "Selection returns the original candidate for approval, never the hypothetical repair.",
    rehearsal: "Scheduled-event rehearsal", nominal: "Nominal timing · separate from random-world statistics",
    scheduled: "Shared incidents", boundary: "First decision boundary", violations: "Why the original plan fails",
    repaired: "Result assuming hypothetical repairs", completed: "Current flight scope processed", blocked: "Needs a decision",
    solver: "Solver calls", repair: "Hypothetical repair", approvedNo: "Preview only, not approved",
    distinction: "Ordinary CP-SAT repair baseline, not autonomous Agent repair. Selection returns the original candidate, not these hypothetical repairs.",
    trace: "Full execution trace", before: "Before", after: "After", pending: "Not yet executed",
    diverted: "Safe diversions", releasedCount: "Turnarounds completed", future: "Scheduled events not yet processed",
    unchanged: "This event did not violate the remaining plan's nominal constraints", noIncident: "No scheduled future incidents",
    stop: "Stop reason",
    validRepair: "Nominal checks passed, not approved", infeasibleRepair: "No feasible repair found", rejectedRepair: "Independent checks rejected the repair",
    open: "Preview futures", title: "One snapshot · different futures", close: "Back to live",
    subtitle: "Preview only: the live clock and approval state remain unchanged.",
    loading: "Simulating the same random worlds…", retry: "Preview the latest snapshot",
    stale: "Live state changed. This snapshot is view-only; selection is disabled.",
    scope: "Pending flights", passengers: "Connecting passengers in scope", excluded: "Outside this scope",
    evidence: "Scope and limitations", invalid: "Invalid in this snapshot", select: "Select and return for approval",
    selecting: "Checking live state…", delay: "Mean delay", missed: "Expected missed connections", p90: "P90 delay",
    risk: "Samples with fuel risk", proof: "Nominal plan constraint checks",
    sample: "Shared sample world #1 · not an average", aggregate: "Current snapshot · 50-world statistics (excluding later incidents)",
    play: "Play preview", pause: "Pause preview", clock: "Preview clock",
    waiting: "Awaiting approach", landing: "Runway occupancy", taxiing: "Taxi / gate queue",
    turnaround: "Gate turnaround", released: "Gate released",
    fixed: "Committed prefix · preserved from live records, not re-simulated",
    limitation: "Conditional on the current snapshot and weather disturbances; future incidents are excluded from these statistics. The separate event rehearsal uses nominal times, not Monte Carlo or a real-airport forecast. Main-screen approval is still required.",
    counts: "Metrics cover pending flights only; committed flights are not counted as new successes.",
    notApproval: "Nominal checks are not approval; stochastic disturbances can still create risks.",
    empty: "No candidates to preview", failure: "Preview failed. Please retry.",
    returnError: "Live state changed. Please preview again.", minutes: "min", people: "pax",
  },
};
const value = (v: number | undefined) => v === undefined ? "—" : v.toFixed(2);


const eventNames: Record<string, [string, string]> = {
  agent_requests_operator: ["Agent 请求人工决策", "Agent requests operator"],
  repair_infeasible: ["未找到可行修正", "No feasible repair found"],
  repair_invalid: ["修正未通过独立校验", "Repair rejected by independent checks"],
  repair_budget_exhausted: ["已用完两次修正预算", "Two-repair budget exhausted"],
  event_budget_exhausted: ["已达到事件处理预算", "Event-processing budget exhausted"],
  runway_closed: ["跑道关闭", "Runway closed"], runway_reopened: ["跑道恢复", "Runway reopened"],
  gate_outage: ["机位故障", "Gate outage"], ground_vehicle_failure: ["地面车辆故障", "Ground vehicle failure"],
  medical_diversion: ["医疗备降插入", "Medical diversion"], storm: ["天气变化", "Weather changed"],
  clock_advanced: ["推进时钟", "Clock advanced"], execution_tick: ["提交落地", "Landing committed"],
  runway_released: ["释放跑道", "Runway released"], gate_arrival: ["到达机位", "Gate arrival"],
  gate_released: ["完成周转并释放机位", "Turnaround complete; gate released"],
  recovered: ["本轮执行结束", "Scope completed"], execution_blocked: ["执行被安全检查阻断", "Execution blocked"],
  flight_diverted: ["燃油截止前备降", "Safe diversion"],
  trusted_state_reconciled: ["更新现场证据", "State evidence updated"],
  preview_hypothetical_repair: ["尝试假设修正（未批准）", "Hypothetical repair (not approved)"],
  preview_plan_still_valid: ["剩余计划仍满足名义约束", "Remaining plan nominally valid"],
  active_observation_invalidated: ["旧观测失效", "Prior observation invalidated"],
};
export function RehearsalEvidence({data, language}: {data: EventRehearsal; language: UiLanguage}) {
  const t = words[language], end = data.continuation, first = data.first_decision_boundary;
  const label = (type: string) => eventNames[type]?.[language === "zh" ? 0 : 1] ?? type;
  const assignment = (a: {flight_id: string; runway: string; gate: string; landing_minute: number}) =>
    a.flight_id + " · " + a.runway + " → " + a.gate + " · T+" + a.landing_minute;
  return <section className="preview-rehearsal">
    <h4>{t.rehearsal}</h4><p>{t.nominal}</p>
    <details><summary>{t.scheduled} ({data.scheduled_events.length})</summary>
      {data.scheduled_events.length ? data.scheduled_events.map(e =>
        <p key={e.id}>T+{e.minute} · {label(e.type)} · {e.resource_id}</p>) : <p>{t.noIncident}</p>}
    </details>
    {first && <div><b>{t.boundary}: T+{first.minute}</b>
      {first.event_constraint_failures.length > 0 ? <details open>
        <summary>{t.violations}</summary><ul>{first.event_constraint_failures.map((line, i) => <li key={i}>{line}</li>)}</ul>
      </details> : <p>{t.unchanged}</p>}
    </div>}
    <p><b>{t.repaired}: {end.status === "completed" ? t.completed : t.blocked}</b> · T+{end.minute}</p>
    {end.status !== "completed" && end.stopped_reason && <p>{t.stop}: {label(end.stopped_reason)}</p>}
    {end.outcome && <p>{t.releasedCount}: {end.outcome.turnaround_complete} · {t.diverted}: {end.outcome.safe_diversions}</p>}
    <p>{t.solver}: {data.solver_calls} / 2 · {t.approvedNo}</p>
    {end.repairs.map((r, i) => <details key={i}>
      <summary>{t.repair} {i + 1} · T+{r.minute} · {r.status === "nominally_valid_not_approved" ? t.validRepair :
        r.status === "infeasible" ? t.infeasibleRepair : t.rejectedRepair}</summary>
      <ul>{r.before_failures.map((line, j) => <li key={j}>{line}</li>)}</ul>
      <b>{t.before}</b>{r.before_assignments.map(a => <p key={a.flight_id}>{assignment(a)}</p>)}
      <b>{t.after}</b>{r.after_assignments.map(a => <p key={a.flight_id}>{assignment(a)}</p>)}
      <ul>{r.after_constraint_proof.map((line, j) => <li key={j}>{line}</li>)}</ul>
    </details>)}
    {end.pending_flight_ids.length > 0 && <p>{t.pending}: {end.pending_flight_ids.join(", ")}</p>}
    {end.unprocessed_event_ids.length > 0 && <p>{t.future}: {end.unprocessed_event_ids.length}</p>}
    <details><summary>{t.trace} ({end.events.length})</summary>
      <ol className="preview-event-list">{end.events.map(e => <li key={e.sequence}>
        T+{e.sim_minute} · {label(e.type)} {String(e.payload.flight_id ?? e.payload.resource_id ?? "")}
        <details><summary>{t.evidence}</summary><pre>{JSON.stringify(e.payload, null, 2)}</pre></details>
      </li>)}</ol>
    </details>
    <p className="preview-caption">{data.controller_version && data.controller_version !== "ordinary_cp_sat" ? t.feedbackLimit : t.distinction}</p>
  </section>;
}


export function FeedbackComparison({branch, language}: {branch: PreviewBranch; language: UiLanguage}) {
  const t = words[language], run = branch.feedback_rehearsal, comparison = branch.repair_comparison;
  if (!run || !comparison) return null;
  const traces = run.proposal_trace ?? [];
  return <section className="preview-rehearsal">
    <h4>{t.feedbackTitle}</h4><p>{t.compareLimit}</p>
    <table className="feedback-comparison"><thead><tr><th></th><th>{t.baseline}</th><th>{t.controller}</th></tr></thead>
      <tbody>
        <tr><th>{t.repaired}</th>{[comparison.baseline, comparison.feedback].map((r, i) =>
          <td key={i}>{r.status === "completed" ? t.completed : t.blocked}</td>)}</tr>
        <tr><th>{t.changed}</th><td>{comparison.baseline.changed_pending_flight_ids.length}</td><td>{comparison.feedback.changed_pending_flight_ids.length}</td></tr>
        <tr><th>{t.finish}</th><td>{comparison.baseline.completed_minute ?? "—"}</td><td>{comparison.feedback.completed_minute ?? "—"}</td></tr>
        <tr><th>{t.solver}</th><td>{comparison.baseline.solver_calls}/2</td><td>{comparison.feedback.solver_calls}/2</td></tr>
        <tr><th>{t.modelCalls}</th><td>{comparison.baseline.model_calls}</td><td>{comparison.feedback.model_calls}</td></tr>
        <tr><th>{t.diverted}</th><td>{comparison.baseline.outcome?.safe_diversions ?? "—"}</td><td>{comparison.feedback.outcome?.safe_diversions ?? "—"}</td></tr>
      </tbody>
    </table>
    {!traces.length && <p>{t.notCalled}</p>}
    {traces.map((trace, i) => <details key={i} open>
      <summary>{t.proposalTitle} {i + 1} · T+{trace.minute} · {trace.source === "fast_model" ? t.fast : t.rule}</summary>
      {trace.model && <p>{trace.model}</p>}
      <p>{trace.proposal.tool === "preserve_assignments" ? t.preserve :
        trace.proposal.tool === "request_operator" ? t.escalate : t.replan}</p>
      {trace.proposal.preserve_flight_ids.length > 0 && <p>{trace.proposal.preserve_flight_ids.join(", ")}</p>}
      <details><summary>{t.evidence}</summary>
        {trace.proposal.evidence_ids.map(id => <pre key={id}>{id}: {JSON.stringify(trace.evidence[id], null, 2)}</pre>)}
        <code>{trace.proposal.source_digest}</code>
        {trace.fallback_reason && <p>{trace.fallback_reason}</p>}
      </details>
    </details>)}
    <RehearsalEvidence data={run} language={language} />
    <p>{t.selectedOriginal}</p>
  </section>;
}

export function RunwayDelayControl({ runways, runwayId, language, busy, onRunwayChange, onRun }: {
  runways: Runway[]; runwayId: string; language: UiLanguage; busy: boolean;
  onRunwayChange: (id: string) => void; onRun: (id: string) => void;
}) {
  const t = words[language], selected = runways.find(runway => runway.id === runwayId);
  return <section className="preview-hypothesis-control">
    <label>{t.runway}<select value={selected?.id ?? ""} disabled={busy || !runways.length}
      onChange={event => onRunwayChange(event.target.value)}>
      {!runways.length && <option value="">—</option>}
      {runways.map(runway => <option key={runway.id} value={runway.id}>
        {runway.id} · T+{runway.reopen_minute}
      </option>)}
    </select></label>
    <button disabled={busy || !selected} onClick={() => { if (selected && !busy) onRun(selected.id); }}>
      {t.hypothesisAction}{language === "zh" ? "" : " "}{selected?.id ?? "—"}
      {language === "zh" ? "" : " "}{t.laterOpen}
    </button>
    {!runways.length && <p>{t.noRunway}</p>}
  </section>;
}

export function RunwayDelayComparison({ branch, hypothesis, language }: {
  branch: PreviewBranch; hypothesis: RunwayDelayHypothesis; language: UiLanguage;
}) {
  const t = words[language], comparison = branch.hypothesis_comparison;
  const baselineRun = branch.rehearsal, hypothesisRun = branch.hypothesis_rehearsal;
  if (!comparison || !baselineRun || !hypothesisRun) return <p role="alert" className="preview-warning">{t.missingHypothesis}</p>;
  const rows = [comparison.baseline, comparison.hypothesis];
  const runs = [baselineRun, hypothesisRun];
  const firstBoundary = hypothesisRun.first_decision_boundary;
  const firstFailures = firstBoundary?.event_constraint_failures ?? [];
  const isNoticeBoundary = firstBoundary?.processed_event_ids?.at(-1) === hypothesis.event_id;
  return <section className="preview-hypothesis-comparison">
    <h4>{t.hypothesisTitle}</h4>
    <p className="preview-hypothesis-notice"><b>T+{hypothesis.notice_minute} {t.notice} · {hypothesis.runway_id}</b><br />
      {t.reopen}: T+{hypothesis.original_reopen_minute} → T+{hypothesis.hypothetical_reopen_minute}
      {" (+" + hypothesis.delay_min + " " + t.minutes + ")"}</p>
    <table className="feedback-comparison"><thead><tr><th></th><th>{t.originalReopen}</th><th>{t.delayedReopen}</th></tr></thead>
      <tbody>
        <tr><th>{t.result}</th>{rows.map((row, index) =>
          <td key={index}>{row.status === "completed" ? t.completed : t.blocked}</td>)}</tr>
        <tr><th>{t.endOrStop}</th>{runs.map((run, index) => <td key={index}>T+{run.continuation.minute}</td>)}</tr>
        <tr><th>{t.changed}</th>{rows.map((row, index) => <td key={index}>{row.changed_pending_flight_ids.length}</td>)}</tr>
        <tr><th>{t.solver}</th>{rows.map((row, index) => <td key={index}>{row.solver_calls}/2</td>)}</tr>
      </tbody>
    </table>
    <div className="preview-hypothesis-reason">
      <b>{isNoticeBoundary ? t.noticeBoundary : t.eventBoundary}{firstBoundary ? " · T+" + firstBoundary.minute : ""}</b>
      {firstBoundary && !isNoticeBoundary && <p className="preview-caption">{t.noticeNotLocated}</p>}
      {!firstBoundary ? <p>{t.noticeUnknown}</p> : firstFailures.length
        ? <ul>{firstFailures.slice(0, 2).map((failure, index) => <li key={index}>{failure}</li>)}</ul>
        : <p>{isNoticeBoundary ? t.noticeStillValid : t.eventStillValid}</p>}
      {firstFailures.length > 2 && <p className="preview-caption">{t.noticeMore}</p>}
      <p className="preview-caption">{isNoticeBoundary ? t.noticeScope : t.eventScope}</p>
    </div>
    <p className="preview-caption">{t.nominalControls}</p>
    <p className="preview-caption">{t.nominalCaveat}</p>
    <p className="preview-original-only">{t.selectedOriginal}</p>
    <details className="preview-hypothesis-trace"><summary>{t.originalTrace}</summary>
      <RehearsalEvidence data={baselineRun} language={language} />
    </details>
    <details className="preview-hypothesis-trace"><summary>{t.hypothesisTrace}</summary>
      <RehearsalEvidence data={hypothesisRun} language={language} />
    </details>
    {!!(comparison.controls.length || comparison.caveats.length) && <details>
      <summary>{t.evidence}</summary>
      <ul>{[...comparison.controls, ...comparison.caveats].map((line, index) => <li key={index}>{line}</li>)}</ul>
    </details>}
  </section>;
}

export function BranchPreviewContent({ report, language, minute, stale, busy = false, choosing, onSelect, onFeedback, compact=false }: {
  report: BranchPreviewReport; language: UiLanguage; minute: number; stale: boolean; busy?: boolean;
  choosing: boolean; onSelect: (id: string) => void; onFeedback?: (id: string) => void; compact?:boolean;
}) {
  const t = words[language];
  const duration = Math.max(1, report.end_minute - report.source_minute);
  const zh = language === "zh";
  const base = report.branches[0]?.report?.raw_metrics;
  const pos = (time: number) => Math.max(0, Math.min(100, (time - report.source_minute) / duration * 100));
  return <>
    {stale && <p className="preview-warning" role="alert">{t.stale}</p>}
    <div className="preview-scope">
      <span>{t.scope}: <b>{report.scope_flight_ids.length}</b></span>
      <span>{t.passengers}: <b>{report.scope_connection_passengers}</b></span>
      <span>{t.excluded}: <b>{report.excluded_flight_ids.length}</b></span>
      <span>T+{report.source_minute} → T+{report.end_minute.toFixed(1)}</span>
    </div>
    {report.runway_delay_hypothesis && <p className="preview-hypothesis-scope">{t.rawLimit}</p>}
    {!report.runway_delay_hypothesis && <p className="preview-explainer">{zh
      ? `同样的 ${report.scope_flight_ids.length} 架飞机、同一组天气变化，每个方案模拟 ${report.runs} 次。下面先比较平均结果，再看其中一次的飞机行动。这里不加入后续故障。`
      : `The same ${report.scope_flight_ids.length} flights and weather samples are simulated ${report.runs} times per plan. Compare averages first, then inspect one simulated run. Later incidents are excluded.`}</p>}
    <div className="preview-branches">
      {report.branches.map((branch, branchIndex) => {
        const metrics = branch.report?.raw_metrics;
        const originalSnapshot = <>
            <dl className="preview-metrics">
              <div><dt>{t.delay}</dt><dd>{value(metrics?.mean_delay_min)} <small>{t.minutes}</small></dd></div>
              <div><dt>{t.missed}</dt><dd>{value(metrics?.missed_connections)} <small>{t.people}</small></dd></div>
              <div><dt>{t.p90}</dt><dd>{value(metrics?.p90_delay_min)} <small>{t.minutes}</small></dd></div>
              <div><dt>{t.risk}</dt><dd>{metrics ? (metrics.fuel_risk_probability * 100).toFixed(1) : "—"}%</dd></div>
            </dl>
            <p className="preview-average-note">{zh
              ? `“预计错过转机”是 ${branch.report?.runs ?? report.runs} 次模拟的平均人数，所以可以有小数；一次模拟中的实际人数是整数。`
              : `Missed connections are averaged across ${branch.report?.runs ?? report.runs} runs, so the estimate can be fractional; each run has a whole-number passenger count.`}</p>
            {branchIndex > 0 && metrics && base && <p className="preview-difference">{zh
              ? `与方案 1 比：每架飞机平均${metrics.mean_delay_min >= base.mean_delay_min ? "多等" : "少等"} ${Math.abs(metrics.mean_delay_min - base.mean_delay_min).toFixed(2)} 分钟；每次模拟平均${metrics.missed_connections >= base.missed_connections ? "多" : "少"} ${Math.abs(metrics.missed_connections - base.missed_connections).toFixed(2)} 人错过转机。`
              : `Versus plan 1: ${(metrics.mean_delay_min - base.mean_delay_min).toFixed(2)} min mean delay and ${(metrics.missed_connections - base.missed_connections).toFixed(2)} mean missed connections.`}</p>}
            <h4 className="preview-sample-heading">{zh ? "看一次具体模拟" : "Inspect one simulated run"}</h4>
            <p className="preview-caption">{report.runway_delay_hypothesis ? t.rawSample : t.sample}</p>
            <div className="preview-time-axis" aria-label={zh ? "分钟刻度，从演示开始计算" : "Minutes since demo start"}>{[0, .25, .5, .75, 1].map(r => <span key={r}>{(report.source_minute + r * duration).toFixed(0)}{zh ? "分" : "m"}</span>)}</div>
            <div className="preview-flights">
              {branch.sample.map(row => <div className="preview-flight" key={row.flight_id}>
                <div><b>{row.flight_id}</b><span>{t[previewFlightStage(row, minute)]}</span></div>
                <svg viewBox="0 0 100 6" preserveAspectRatio="none" role="img"
                  aria-label={zh ? `${row.flight_id} 第 ${row.landing_minute.toFixed(1)} 分钟在 ${row.runway} 落地，第 ${row.gate_in_minute.toFixed(1)} 分钟到 ${row.gate}` : row.flight_id + ": " + row.runway + " → " + row.gate}>
                  <rect x="0" y="1" width="100" height="4" rx="1" fill="#e1eaee" />
                  <rect x={pos(row.landing_minute)} y="1" width={Math.max(.3, pos(row.runway_release_minute) - pos(row.landing_minute))} height="4" fill="#f4bd69" />
                  <rect x={pos(row.runway_release_minute)} y="1" width={Math.max(0, pos(row.gate_in_minute) - pos(row.runway_release_minute))} height="4" fill="#65b6ec" />
                  <rect x={pos(row.gate_in_minute)} y="1" width={Math.max(0, pos(row.gate_out_minute) - pos(row.gate_in_minute))} height="4" fill="#65ddbd" />
                  <line x1={pos(minute)} x2={pos(minute)} y1="0" y2="6" stroke="#264a59" strokeWidth=".6" />
                </svg>
                <div className="preview-flight-times"><small>{zh ? `第 ${row.landing_minute.toFixed(1)} 分钟 · 在 ${row.runway} 落地` : `Minute ${row.landing_minute.toFixed(1)} · land on ${row.runway}`}</small>
                  <small>{zh ? `第 ${row.gate_in_minute.toFixed(1)} 分钟 · 到 ${row.gate} 停靠` : `Minute ${row.gate_in_minute.toFixed(1)} · arrive at ${row.gate}`}</small>
                  <small>{zh ? `第 ${row.gate_out_minute.toFixed(1)} 分钟 · ${row.gate} 可交给下一架` : `Minute ${row.gate_out_minute.toFixed(1)} · ${row.gate} becomes free`}</small></div>
              </div>)}
            </div>
        </>;
        return <article className="preview-branch" key={branch.plan_id}>
          <h3>{zh ? "方案 " : "Plan "}{branchIndex + 1} · {planTitleLabel(branch.title, branch.profile, language)}</h3>
          <p className="preview-plan-purpose">{zh ? ({
            balanced: "同时考虑飞机延误、转机旅客和机场资源。",
            resilient: "给天气和地面延误留出更多时间，再检查燃油和转机是否来得及。",
            passenger: "更重视减少旅客错过下一班飞机。",
            capacity: "更重视尽快安排飞机落地。",
            green: "更重视减少滑行和等待带来的排放。",
            operator: "按你确认的要求安排。",
          }[branch.profile] ?? "具体差别看下面的等待时间、旅客影响和各架飞机安排。") : "Compare delays, passenger impact and the flight-by-flight simulation below."}</p>
          {report.runway_delay_hypothesis && branch.status === "evaluated" && <RunwayDelayComparison
            branch={branch} hypothesis={report.runway_delay_hypothesis} language={language} />}
          {branch.status === "invalid" ? <p role="alert">{t.invalid}</p>
            : report.runway_delay_hypothesis
              ? <details className="preview-original-snapshot"><summary>{t.rawAggregate}</summary>{originalSnapshot}</details>
              : originalSnapshot}
          {!compact && !report.runway_delay_hypothesis && branch.rehearsal && <RehearsalEvidence data={branch.rehearsal} language={language} />}
          {!compact && !report.runway_delay_hypothesis && branch.feedback_rehearsal && <FeedbackComparison branch={branch} language={language} />}
          {!compact && onFeedback && <button disabled={stale || busy || choosing || branch.status !== "evaluated" || Boolean(report.runway_delay_hypothesis)}
            title={report.runway_delay_hypothesis ? t.noFeedback : undefined}
            onClick={() => { if (!report.runway_delay_hypothesis) onFeedback(branch.plan_id); }}>{t.feedbackRun}</button>}
          {!compact && <details><summary>{t.proof}</summary>
            <ul>{(branch.failures.length ? branch.failures : branch.constraint_proof).map((line, i) => <li key={i}>{line}</li>)}</ul>
            <p>{t.notApproval}</p>
          </details>}
          <button disabled={stale || busy || choosing || branch.status !== "evaluated"}
            onClick={() => onSelect(branch.plan_id)}>{choosing ? t.selecting : report.runway_delay_hypothesis ? t.selectOriginal : t.select}</button>
        </article>;
      })}
    </div>
    {report.committed_operations.length > 0 && <details className="preview-fixed">
      <summary>{t.fixed} ({report.committed_operations.length})</summary>
      {report.committed_operations.map(row => <p key={row.flight_id}>
        {row.flight_id} · {row.runway} T+{row.landing_minute} → {row.gate} T+{row.gate_start_minute}–{row.gate_release_minute}
      </p>)}
    </details>}
    <details className="preview-limits"><summary>{t.evidence}</summary>
      <p>{t.counts}</p><p>{t.limitation}</p>
      <code>{report.method_version} · seed {report.sample_world_seed}</code>
    </details>
  </>;
}

function PreviewDialog({ session, language, onClose, onSelect }: {
  session: SessionState; language: UiLanguage; onClose: () => void;
  onSelect: (id: string, receipt: PreviewSelectionReceiptData) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const alive = useRef(true);
  const currentSession = useRef(session);
  currentSession.current = session;
  const [preview, setPreview] = useState<PreviewCollection | null>(null);
  const report = preview?.report ?? null;
  const [runwayId, setRunwayId] = useState("");
  const [hypothesisBusy, setHypothesisBusy] = useState(false);
  const eligibleRunways = eligibleRunwayDelays(session);
  const selectedRunway = eligibleRunways.find(runway => runway.id === runwayId)
    ?? eligibleRunways.find(runway => runway.id === "07R") ?? eligibleRunways[0];
  const [busy, setBusy] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [error, setError] = useState("");
  const [minute, setMinute] = useState(session.scenario.current_minute);
  const [playing, setPlaying] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const started = useRef(false);
  const t = words[language];
  const stale = report ? !previewIsCurrent(report, session) : false;
  useEffect(() => {
    alive.current = true;
    dialog.current?.showModal();
    return () => { alive.current = false; };
  }, []);
  async function run(feedbackPlanId?: string, delayedRunwayId?: string) {
    if (busy || choosing || session.intent?.needs_confirmation) return;
    if (feedbackPlanId && (delayedRunwayId || !report || stale || report.runway_delay_hypothesis)) return;
    if (delayedRunwayId && !eligibleRunways.some(runway => runway.id === delayedRunwayId)) return;
    setBusy(true); setHypothesisBusy(Boolean(delayedRunwayId)); setPlaying(false); setError("");
    try {
      const result = await createBranchPreview(session,
        feedbackPlanId ? [feedbackPlanId] : session.plans.map(plan => plan.id), Boolean(feedbackPlanId),
        delayedRunwayId ? {runway_id: delayedRunwayId, delay_min: 10} : undefined);
      if (alive.current) {
        const merged = mergePreviewReports(preview, result, feedbackPlanId);
        if (!merged) { setError(t.mergeError); return; }
        setPreview(merged); setMinute(merged.report.source_minute);
      }
    } catch (reason) {
      if (alive.current) setError(reason instanceof Error ? reason.message : t.failure);
    } finally { if (alive.current) { setBusy(false); setHypothesisBusy(false); } }
  }
  // The user's preview action starts computation; it never approves live operations.
  useEffect(() => { if(!started.current){started.current=true;void run();} }, []);

  useEffect(() => {
    if (!playing || !report || stale) return;
    const timer = globalThis.setInterval(() => {
      setMinute(old => Math.min(report.end_minute, old + .5));
    }, 500);
    return () => globalThis.clearInterval(timer);
  }, [playing, report, stale]);
  useEffect(() => { if (report && minute >= report.end_minute) setPlaying(false); }, [minute, report]);
  async function choose(id: string) {
    if (!preview || !report || stale || busy || choosing) return;
    const reportId = preview.reportIdsByPlan[id];
    if (!reportId || !report.branches.some(branch => branch.plan_id === id && branch.status === "evaluated")) return;
    setChoosing(true); setPlaying(false);
    try {
      const checked = await getBranchPreview(session.id, reportId);
      if (!alive.current) return;
      if (!deliverPreviewSelection(preview, checked, currentSession.current, id, onSelect)) {
        if (checked.status === "stale") setPreview({...preview, report: {...report, status: "stale"}});
        setError(t.returnError); return;
      }
      onClose();
    } catch (reason) {
      if (alive.current) setError(reason instanceof Error ? reason.message : t.failure);
    } finally { if (alive.current) setChoosing(false); }
  }
  return <dialog ref={dialog} className={`branch-preview-dialog ${expanded?"":"compact-preview"}`} onCancel={onClose} aria-labelledby="preview-title">
    <header><div><h2 id="preview-title">{t.title}</h2><p>{t.subtitle}</p></div>
      <button onClick={onClose} aria-label={t.close}><X size={20} /></button></header>
    <button className="preview-detail-toggle" onClick={()=>setExpanded(v=>!v)}>{language==="zh"?(expanded?"收起更多预演工具":"更多预演工具"):(expanded?"Hide advanced tools":"Advanced tools")}</button>
    <div className="preview-toolbar">
      <button disabled={busy || choosing || !session.plans.length || Boolean(session.intent?.needs_confirmation)}
        onClick={() => void run()}>{busy ? (hypothesisBusy ? t.hypothesisLoading : t.loading) : report ? t.retry : t.open}</button>
      {report && <>
        <button disabled={busy || stale} onClick={() => {
          if (minute >= report.end_minute) setMinute(report.source_minute);
          setPlaying(v => !v);
        }}>{playing && !stale ? <Pause size={16} /> : <Play size={16} />}
          {report.runway_delay_hypothesis ? (playing && !stale ? t.rawPause : t.rawPlay) : (playing && !stale ? t.pause : t.play)}</button>
        <label>{report.runway_delay_hypothesis ? t.rawClock : t.clock} T+{minute.toFixed(1)}
          <input aria-label={t.clock} type="range" min={report.source_minute} max={report.end_minute}
            step="any" value={minute} onChange={event => { setPlaying(false); setMinute(Number(event.target.value)); }} />
        </label>
      </>}
    </div>
    <RunwayDelayControl runways={eligibleRunways} runwayId={selectedRunway?.id ?? ""} language={language}
      busy={busy || choosing || !session.plans.length || Boolean(session.intent?.needs_confirmation)}
      onRunwayChange={setRunwayId} onRun={id => void run(undefined, id)} />
    {error && <p role="alert" className="preview-warning">{error}</p>}
    {!report && <p>{t.limitation}</p>}
    {report && <>
      <div className="preview-legend"><span>{t.landing}</span><span>{t.taxiing}</span><span>{t.turnaround}</span></div>
      <BranchPreviewContent report={report} language={language} minute={minute}
        stale={stale} busy={busy} choosing={choosing} compact={!expanded} onSelect={id => void choose(id)}
        onFeedback={id => void run(id)} />
    </>}
  </dialog>;
}

export function BranchPreviewButton({ session, language, disabled, onSelect, openRequest = 0 }: {
  session: SessionState; language: UiLanguage; disabled: boolean;
  onSelect: (id: string, receipt: PreviewSelectionReceiptData) => void; openRequest?: number;
}) {
  const [open, setOpen] = useState(false);
  const handledRequest = useRef(0);
  useEffect(() => { setOpen(false); handledRequest.current = 0; }, [session.id]);
  useEffect(() => {
    if (openRequest <= handledRequest.current) return;
    handledRequest.current = openRequest;
    if (!disabled && session.plans.length && !session.intent?.needs_confirmation) setOpen(true);
  }, [openRequest, disabled, session.plans.length, session.intent?.needs_confirmation]);
  return <>
    <button className="lab-button" disabled={disabled || !session.plans.length || Boolean(session.intent?.needs_confirmation)}
      onClick={() => setOpen(true)}><FlaskConical size={15} />{words[language].open}</button>
    {open && createPortal(<PreviewDialog session={session} language={language}
      onClose={() => setOpen(false)} onSelect={onSelect} />, document.body)}
  </>;
}
