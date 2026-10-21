import { previewReceiptIsCurrent, type PreviewNominalSummary, type PreviewSelectionReceiptData } from "./branchPreview";
import { planTitleLabel, type UiLanguage } from "./localization";
import type { SessionState } from "./types";
import "./previewSelectionReceipt.css";

const copy = {
  zh: {
    title: "这次选择的预演依据", original: "原候选", source: "源快照",
    ordinary: "普通预演", delay: "晚开放假设", feedback: "反馈修正对照",
    scope: "隔离预演范围", flights: "架待执行航班", passengers: "位转机旅客", excluded: "架未计入",
    nominal: "名义事件预演（不是随机统计或真实执行）",
    baseline: "普通 CP-SAT", originalOpen: "原预计开放", alternativeOpen: "假设晚开放",
    feedbackRun: "反馈修正", result: "名义结果", completed: "名义完成", pending: "仍待决策",
    end: "结束 / 停止", solver: "求解次数", repairs: "已接受的假设修正", changed: "改动航班",
    missing: "未记录名义结果", unknown: "未记录", notice: "晚开放通知",
    reopening: "预计开放", firstCheck: "首次事件后的检查", noticeCheck: "晚开放通知后的检查",
    notLocated: "未定位晚开放通知检查；以下只说明首次事件。",
    satisfied: "该次检查中原计划仍满足名义约束；后续事件仍可能使其失效。",
    failures: "该次约束失败", more: "仅列前2条，完整依据保留在预演轨迹。",
    missingBoundary: "未记录事件检查边界。",
    modelProposal: "本地模型建议", ruleProposal: "规则反馈降级（不是模型建议）",
    noProposal: "无建议来源记录", provenance: "实际建议来源", modelCalls: "实际模型调用",
    stats: "原快照随机统计", statsLimit: "不含后续事件和晚开放假设，不是上述名义结果。",
    worlds: "次", mean: "平均延误", minutes: "分钟", missed: "预计错过转机", people: "人",
    approval: "只选择原候选；未批准任何预演修正，也不代表现场执行成功。仍须人工审批。",
  },
  en: {
    title: "Preview evidence for this selection", original: "Original candidate", source: "Source snapshot",
    ordinary: "Ordinary preview", delay: "Delayed-reopening hypothesis", feedback: "Feedback repair comparison",
    scope: "Isolated scope", flights: "pending flights", passengers: "connecting passengers", excluded: "excluded flights",
    nominal: "Nominal event rehearsal (not random-world statistics or live execution)",
    baseline: "Ordinary CP-SAT", originalOpen: "Original reopening", alternativeOpen: "Delayed reopening",
    feedbackRun: "Feedback repair", result: "Nominal result", completed: "Nominally completed", pending: "Needs a decision",
    end: "End / stop", solver: "Solver calls", repairs: "Accepted hypothetical repairs", changed: "Changed flights",
    missing: "No nominal result recorded", unknown: "Not recorded", notice: "Delay notice",
    reopening: "Expected reopening", firstCheck: "Check after the first event", noticeCheck: "Check after the delay notice",
    notLocated: "Delay-notice check not located; the evidence below is only about the first event.",
    satisfied: "The original plan met nominal constraints at this check; later events may still invalidate it.",
    failures: "Constraint failures at this check", more: "First two only; full evidence remains in the preview trace.",
    missingBoundary: "No event-check boundary recorded.",
    modelProposal: "Local model proposal", ruleProposal: "Rule-based fallback (not a model proposal)",
    noProposal: "No proposal source recorded", provenance: "Actual proposal source", modelCalls: "Actual model calls",
    stats: "Original snapshot random-world statistics", statsLimit: "Excludes later events and the delay hypothesis; these are not the nominal results above.",
    worlds: "worlds", mean: "Mean delay", minutes: "min", missed: "Expected missed connections", people: "passengers",
    approval: "Only the original candidate is selected. No rehearsal repairs are approved, and no live success is implied. Human approval is still required.",
  },
};

export interface PreviewSelectionReceiptProps {
  receipt?: PreviewSelectionReceiptData | null;
  session: SessionState;
  selectedPlanId: string;
  language: UiLanguage;
}

export function PreviewSelectionReceipt({receipt, session, selectedPlanId, language}: PreviewSelectionReceiptProps) {
  if (!previewReceiptIsCurrent(receipt, session, selectedPlanId)) return null;
  const t = copy[language];
  const hypothesis = receipt.runway_delay_hypothesis;
  const inspected = receipt.alternative ?? receipt.baseline;
  const boundary = inspected?.first_boundary;
  const noticeLocated = Boolean(hypothesis && boundary?.processed_event_ids?.at(-1) === hypothesis.event_id);
  const row = (label: string, run: PreviewNominalSummary | null) => <p>
    <strong>{label}：</strong>{run ? <>
      {run.status === "completed" ? t.completed : t.pending} · T+{run.end_minute}
      {" · "}{t.solver} {run.solver_calls} / {t.repairs} {run.accepted_repairs}
      {" · "}{t.changed} {run.changed_flight_count}
    </> : t.missing}
  </p>;
  const statistics = receipt.original_statistics;
  const metric = (value: number | null) => value === null ? "—" : value.toFixed(2);
  return <section className="preview-selection-receipt" aria-label={t.title}>
    <header><h4>{t.title}</h4><span>{t[receipt.mode]}</span></header>
    <p><strong>{t.original} · {planTitleLabel(receipt.title, receipt.profile, language)}</strong>
      <small>{t.source} T+{receipt.source_minute} · #{receipt.source_sequence}</small></p>
    <p>{t.scope}：{receipt.scope.flight_ids.length} {t.flights} · {receipt.scope.connection_passengers} {t.passengers}
      {" · "}{receipt.scope.excluded_flight_ids.length} {t.excluded}</p>
    {hypothesis && <p>{t.notice} T+{hypothesis.notice_minute} · {hypothesis.runway_id} {t.reopening}
      {" "}T+{hypothesis.original_reopen_minute} → T+{hypothesis.hypothetical_reopen_minute}</p>}
    <div className="receipt-nominal-summary"><small>{t.nominal}</small>
      {row(hypothesis ? t.originalOpen : t.baseline, receipt.baseline)}
      {receipt.mode !== "ordinary" && row(hypothesis ? t.alternativeOpen : t.feedbackRun, receipt.alternative)}
    </div>
    {boundary ? <div className="receipt-event-evidence">
      <p>{noticeLocated ? t.noticeCheck : t.firstCheck} · T+{boundary.minute}</p>
      {hypothesis && !noticeLocated && <p>{t.notLocated}</p>}
      {boundary.event_constraint_failures.length ? <>
        <p>{t.failures}：</p><ul>{boundary.event_constraint_failures.slice(0, 2).map((failure, index) => <li key={index}>{failure}</li>)}</ul>
        {boundary.event_constraint_failures.length > 2 && <small>{t.more}</small>}
      </> : <p>{t.satisfied}</p>}
    </div> : <p>{t.missingBoundary}</p>}
    {receipt.feedback && <p className="receipt-feedback-source">{t.provenance}：
      {receipt.feedback.sources.length ? receipt.feedback.sources.map(source =>
        source === "fast_model" ? t.modelProposal : t.ruleProposal).join(" / ") : t.noProposal}
      {" · "}{t.modelCalls}：{receipt.feedback.model_calls ?? t.unknown}
      {receipt.feedback.models.length > 0 && <> · {receipt.feedback.models.join(", ")}</>}
    </p>}
    <details className="receipt-original-statistics"><summary>{t.stats} · {statistics.runs} {t.worlds}</summary>
      <p>{t.statsLimit}</p><p>{t.mean} {metric(statistics.mean_delay_min)} {t.minutes}
        {" · "}{t.missed} {metric(statistics.missed_connections)} {t.people}</p>
    </details>
    <p className="receipt-approval-boundary">{t.approval}</p>
  </section>;
}
