import type { Plan, SessionState } from "./types";
import type { UiLanguage } from "./localization";
import { planTitleLabel } from "./localization";
import "./evidenceReceipt.css";

type ObjectValue = Record<string, unknown>;
const object = (value: unknown): ObjectValue =>
  value && typeof value === "object" && !Array.isArray(value) ? value as ObjectValue : {};
const text = (value: unknown): string => typeof value === "string" ? value : "";
const number = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined;

export function latestStoryObservation(session: SessionState): ObjectValue | undefined {
  const entries = session.data_snapshot?.active_observations;
  if (!Array.isArray(entries) || !entries.length) return undefined;
  const last = object(entries.at(-1));
  return ["weather_reopen_nowcast", "critical_gate_readiness"].includes(text(last.query_id)) ? last : undefined;
}

/** Preserve strategy continuity, never pretend that a new candidate is the old assignment. */
export function observationReplacement(session: SessionState, previous?: Plan): Plan | undefined {
  const receipt = latestStoryObservation(session);
  const profile = previous?.profile || (receipt?.valid === true ? text(object(receipt.review_context).profile) : "");
  const matches = session.plans.filter(plan => profile && plan.profile === profile);
  return matches.length === 1 ? matches[0] : session.plans[0];
}

const words = {
  zh: {
    title: "证据更新回执", before: "查询前查看", unknown: "未记录查询前候选，不能比较审批变化。",
    query: "实际获取", synthetic: "合成演练观测 · 未连接真实跑道 / AODB 接口",
    unspecified: "来源未明确为合成观测；不据此声称已获真实确认。",
    acquired: "获取于", input: "输入项", old: "查询前", observed: "本次观测",
    reopen: "预计开放时刻", uncertainty: "开放时间标准差（分钟）", available: "机位预计可用时刻",
    current: "重算后的当前候选", same: "同一策略的新候选，不是原来的安排。",
    different: "当前查看的是不同策略；不能把审批差异仅归因于补充证据。",
    pending: "观测已保存，尚无绑定新证据的候选。请重新规划，不可沿用旧方案。",
    invalid: "此观测已被后续事件作废，下面仅作历史记录，不能作为当前审批依据。",
    missing: "此观测尚未被当前证据状态接受，不能用于批准。",
    ready: "当前审批门允许人工审批；仍须重新预演、人工确认，尚未执行。",
    reviewedReady: "已查看当前快照的预演；仍需人工最终批准，尚未执行。",
    blocked: "当前审批门仍未放行，请处理下方缺失证据或冲突。",
    caution: "预计开放时间更新不等于跑道已经开放。重新求解可能改变安排；此回执不证明效果提升。",
    gateCaution: "预计可用时间更新不等于机位已经释放。重新求解可能改变安排；此回执不证明效果提升。",
    risk: "仍需注意", detail: "查看查询前的缺失依据", unavailable: "未提供",
    statuses: {ready:"允许人工审批", human_required:"需人工承担风险", needs_evidence:"缺少证据", blocked:"禁止批准", unknown:"审批状态未记录"},
  },
  en: {
    title: "Evidence update receipt", before: "Candidate reviewed before query", unknown: "Prior candidate was not recorded; no before/after gate comparison is available.",
    query: "Observation acquired", synthetic: "Synthetic exercise observation · no live runway / AODB interface",
    unspecified: "Source is not explicitly synthetic; this receipt does not claim real-world confirmation.",
    acquired: "Acquired at", input: "Input", old: "Before query", observed: "Observed",
    reopen: "Estimated reopening", uncertainty: "Reopening standard deviation (minutes)", available: "Estimated gate availability",
    current: "Current re-solved candidate", same: "A new candidate using the same strategy, not the original assignments.",
    different: "A different strategy is selected; gate differences cannot be attributed solely to the observation.",
    pending: "Observation saved, but no candidate is bound to the new evidence. Replan; do not reuse the old plan.",
    invalid: "A later event invalidated this observation. Shown as history only, not current approval evidence.",
    missing: "The current evidence state has not accepted this observation. It cannot authorize approval.",
    ready: "The current gate permits human review. Preview again and explicitly approve; nothing has executed.",
    reviewedReady: "The current snapshot was previewed. Final human approval is still required; nothing has executed.",
    blocked: "The current gate still blocks approval. Resolve the missing evidence or conflict below.",
    caution: "An updated reopening estimate does not mean the runway is open. Re-solving may change assignments; this receipt does not prove an improvement.",
    gateCaution: "An updated availability estimate does not mean the gate is released. Re-solving may change assignments; this receipt does not prove an improvement.",
    risk: "Remaining risk", detail: "Prior missing evidence", unavailable: "Not provided",
    statuses: {ready:"Human approval allowed", human_required:"Human risk acceptance required", needs_evidence:"Evidence missing", blocked:"Approval blocked", unknown:"Gate not recorded"},
  },
};

export function EvidenceReceipt({session, selectedPlanId, language, previewReviewed = false}: {
  session: SessionState; selectedPlanId: string; language: UiLanguage; previewReviewed?: boolean;
}) {
  const entry = latestStoryObservation(session);
  if (!entry) return null;
  const t = words[language], before = object(entry.before), observed = object(entry.observed);
  const review = object(entry.review_context), queryId = text(entry.query_id);
  const plan = session.plans.find(item => item.id === selectedPlanId);
  const gate = plan ? session.decision_lab.approval_gates?.[plan.id] : undefined;
  const resolved = session.data_snapshot?.resolved_observation_ids;
  const accepted = entry.valid === true && Array.isArray(resolved) && resolved.includes(queryId);
  const evidenceSequence = number(entry.evidence_sequence);
  const bound = accepted && Boolean(plan && gate?.evidence_binding?.valid
    && gate.evidence_binding.trusted_state_digest
    && gate.evidence_binding.trusted_state_digest === plan.evidence_basis?.trusted_state_digest
    && evidenceSequence !== undefined && session.sequence > evidenceSequence
    && session.phase === "awaiting_approval" && !session.intent?.needs_confirmation && !session.intent?.issues?.length);
  const status = (value: unknown) => t.statuses[text(value) as keyof typeof t.statuses] ?? t.statuses.unknown;
  const fields = queryId === "weather_reopen_nowcast"
    ? [["reopen_minute", t.reopen], ["closure_std_min", t.uncertainty]]
    : [["available_minute", t.available]];
  const value = (data: ObjectValue, key: string) => {
    const parsed = number(data[key]);
    return parsed === undefined ? "—" : key === "closure_std_min" ? String(parsed) : `T+${parsed}`;
  };
  return <section className="story-evidence-receipt" aria-label={t.title}>
    <h4>{t.title}</h4>
    {entry.valid === false && <p className="receipt-warning">{t.invalid}</p>}
    {entry.valid !== false && !accepted && <p className="receipt-warning">{t.missing}</p>}
    <p><b>{t.before}：</b>{review.plan_id
      ? <>{planTitleLabel(text(review.title), text(review.profile), language)} · {status(review.gate_status)}</>
      : t.unknown}</p>
    {Array.isArray(review.required_evidence) && review.required_evidence.length > 0 &&
      <details><summary>{t.detail}</summary>{review.required_evidence.map((item, index) =>
        <p key={index}>{text(object(item).evidence)} · {text(object(item).consequence)}</p>)}</details>}
    <p><b>{t.query}：</b>{text(entry.target) || t.unavailable} · {t.acquired} T+{number(entry.acquired_minute) ?? "—"}</p>
    <small>{entry.synthetic === true ? t.synthetic : t.unspecified}</small>
    <table><thead><tr><th>{t.input}</th><th>{t.old}</th><th>{t.observed}</th></tr></thead>
      <tbody>{fields.map(([key, label]) => <tr key={key}><th>{label}</th><td>{value(before,key)}</td><td>{value(observed,key)}</td></tr>)}</tbody></table>
    {bound && plan && gate ? <>
      <p><b>{t.current}：</b>{planTitleLabel(plan.title, plan.profile, language)} · {status(gate.status)}</p>
      <p>{review.profile === plan.profile ? t.same : review.profile ? t.different : t.unknown}</p>
      <p>{gate.can_human_approve ? (previewReviewed ? t.reviewedReady : t.ready) : t.blocked}</p>
      <details><summary>{t.risk}</summary><p>{gate.risk_disclosure}</p>
        {gate.required_queries.map((query,index) => <p key={index}>{query.evidence} · {query.consequence}</p>)}
        {gate.hard_block_reasons.map((reason,index) => <p key={index}>{reason}</p>)}</details>
    </> : accepted && <p className="receipt-warning">{t.pending}</p>}
    <small>{queryId === "weather_reopen_nowcast" ? t.caution : t.gateCaution}</small>
  </section>;
}
