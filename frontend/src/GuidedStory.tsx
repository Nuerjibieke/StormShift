import { runtimeText } from "./localization";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { BookOpen, X } from "lucide-react";
import { planTitleLabel, type UiLanguage } from "./localization";
import type { AgentTrace, CandidateApprovalGate, Plan } from "./types";
import { guidedStoryCopy } from "./guidedStoryCopy";
import { forecastMetric } from "./auditPresentation";
import { storyActions, type StoryAction, type StoryState } from "./guidedStory";
import "./guidedStory.css";

export interface GuideProps {
  state: StoryState; language: UiLanguage; busy: boolean; playing: boolean; paused: boolean;
  canResume: boolean; needsApproval: boolean; fallback: boolean; onAction: (action: StoryAction) => void;
  plan?: Plan; plans?: Plan[]; gate?: CandidateApprovalGate; traces?: AgentTrace[];
  decisionReceipts?: ReactNode;
  onSelectPlan?: (id: string) => void; onObserve?: (queryId: string) => void;
}
export function StoryGuideContent(props: GuideProps) {
  const {state, language, busy, onAction} = props;
  const t = guidedStoryCopy[language];
  const [view, setView] = useState(state.stage);
  const [evidenceOpen, setEvidenceOpen] = useState(props.gate?.status === "needs_evidence");
  const evidenceDetails = useRef<HTMLDetailsElement>(null);
  useEffect(() => { if (props.gate?.status === "needs_evidence") setEvidenceOpen(true); }, [props.gate?.status]);
  useEffect(() => setView(state.stage), [state.stage]);
  const current = view === state.stage, chapter = t.stages[view - 1];
  const note = state.stage === 2 ? (state.needsConfirmation ? t.pending : state.intentConfirmed ? t.confirmed : "")
    : state.stage === 3 || state.stage === 4 ? (state.gateStatus === "needs_evidence" ? t.missing
      : state.gateStatus === "blocked" ? t.blocked : state.previewSelected && state.canApprove ? t.ready : state.canApprove ? t.candidateReady : t.unknown)
    : state.stage === 5 ? state.faultVisible ? t.fault : t.executing : state.stage === 6 ? t.auditNote : "";
  return <>
    <nav aria-label={t.overview}>{t.stages.map((item, index) =>
      <button key={index} onClick={() => setView((index + 1) as StoryState["stage"])}
        aria-current={index + 1 === state.stage ? "step" : undefined} aria-pressed={view === index + 1}>
        <small>{index + 1 === state.stage ? t.current : String(index + 1).padStart(2, "0")}</small>{item.title}
      </button>)}</nav>
    <p className="story-guide-caption">{t.choose}</p>
    <article>
      <h3>{view}/6 · {chapter.title}</h3>
      {!current && <p className="story-guide-note">{t.reference}</p>}
      <dl><dt>{t.what}</dt><dd>{chapter.what}</dd><dt>{t.why}</dt><dd>{chapter.why}</dd><dt>{t.next}</dt><dd>{chapter.next}</dd></dl>
      {current && props.plan && ([3, 4].includes(state.stage) || state.stage === 5 && props.needsApproval) && <section className="story-guide-plan">
        <label>{t.selectedPlan}<select aria-label={t.selectedPlan} value={props.plan.id} disabled={busy || props.playing}
          onChange={event => props.onSelectPlan?.(event.target.value)}>
          {(props.plans ?? [props.plan]).map(plan => <option key={plan.id} value={plan.id}>
            {planTitleLabel(plan.title, plan.profile, language)}</option>)}
        </select></label>
        <small>{t.forecast}</small>
        <div className="story-guide-metrics">{[
          [t.delay, forecastMetric(props.plan.report, "mean_delay_min")], [t.p90, forecastMetric(props.plan.report, "p90_delay_min")], [t.missed, forecastMetric(props.plan.report, "missed_connections")],
        ].map(([label, value]) => <span key={label}><small>{label}</small><b>{typeof value === "number" ? value.toFixed(2) : "—"}</b></span>)}</div>
        <details ref={evidenceDetails} open={evidenceOpen} onToggle={event => setEvidenceOpen(event.currentTarget.open)}>
          <summary>{t.evidence}</summary>
          <p>{runtimeText(props.gate?.summary ?? t.unknown, language)}</p><p>{runtimeText(props.gate?.risk_disclosure ?? "", language)}</p>
          {props.gate?.required_queries.map(query => <section key={query.query_id}>
            <b>{query.producer} → {query.consumer}</b><p>{query.evidence}</p><p>{query.consequence}</p>
            {props.onObserve && <button disabled={busy || props.playing} onClick={() => props.onObserve?.(query.query_id)}>{t.observe}</button>}
          </section>)}
          {props.gate?.hard_block_reasons.map((reason, index) => <p key={index}>{reason}</p>)}
          <details><summary>{t.agentTrace} ({props.traces?.length ?? 0})</summary>
            <ol>{props.traces?.map((trace, index) => <li key={index}><b>{trace.agent}</b><p>{runtimeText(trace.summary, language)}</p></li>)}</ol>
          </details>
        </details>
      </section>}
      {current && [2, 3, 4].includes(state.stage) && props.decisionReceipts}
      {current && note && <p className="story-guide-note" role="status">{note}</p>}
      {current && state.focusFlight && <p className="story-guide-focus">{t.focus}: <b>{state.focusFlight}</b></p>}
    </article>
    <div className="story-guide-actions">
      {current ? storyActions(state, props).map(action => <button key={action}
        disabled={busy || (action === "approve" && !state.canApprove) || (action === "preview" && state.needsConfirmation)}
        onClick={() => {
          if (action === "evidence" && props.plan) {
            setEvidenceOpen(true);
            evidenceDetails.current?.scrollIntoView({block: "nearest"});
          } else onAction(action);
        }}>{t.actions[action]}</button>)
        : <button onClick={() => setView(state.stage)}>{t.returnCurrent}</button>}
    </div>
    <p className="story-guide-caption">{props.fallback ? t.fallback : t.model}</p>
    {props.playing && <p className="story-guide-caption">{t.pauseNote}</p>}
  </>;
}
function GuideDialog(props: GuideProps & { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null), t = guidedStoryCopy[props.language];
  useEffect(() => { dialog.current?.showModal(); }, []);
  return <dialog ref={dialog} className="story-guide-dialog" aria-labelledby="story-guide-title" onCancel={props.onClose}>
    <header><div><small>STORMSHIFT · {t.overview}</small><h2 id="story-guide-title">{t.title}</h2></div>
      <button aria-label={t.close} onClick={props.onClose}><X size={20} /></button></header>
    <StoryGuideContent {...props} />
    <footer><button onClick={props.onClose}>{t.close}</button></footer>
  </dialog>;
}
export function GuidedStory(props: GuideProps & { open: boolean; onOpen: () => void; onClose: () => void }) {
  const t = guidedStoryCopy[props.language];
  return <>
    <button className="story-guide-trigger" onClick={props.onOpen} aria-label={t.guide} aria-haspopup="dialog">
      <BookOpen size={13} /><b>{props.state.stage}/6 · {t.stages[props.state.stage - 1].title}</b>
    </button>
    {props.open && createPortal(<GuideDialog {...props} />, document.body)}
  </>;
}
