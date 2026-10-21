import { useId, useState } from "react";
import { BrainCircuit, Check } from "lucide-react";
import { copy, type Locale } from "./copy";
import type { Intent } from "./types";

const labels = {
  zh: {
    evidence: "原文依据与保留限制", clarify: "需要澄清，尚未执行",
    revise: "请回答下面的问题。分钟和人数可直接填写数字；其他要求用一句话说明。",
    legacyRevise: "请在输入框里写清完整要求，再点击“理解目标”。",
    inherited: "沿用已确认要求", clock: "原草稿时间基准", unknownClock: "未记录",
    fixedClock: "相对时间以此时刻为起点。",
    original: "原始指令", history: "补充记录", answer: "你的补充", replacement: "本轮明确为",
    current: "当前解析目标与限制 · 待人工确认", currentText: "当前有效指令",
    submit: "提交补充", submitting: "处理中…",
    safety: "规划会同时检查：油量够用、飞机保持安全间距、停机位置有空位。",
    weights: "目标权重", average_delay_cap: "平均延误上限",
    minimum_connection_rate: "按时转机率下限", maximum_missed_connections: "平均错失转机人数上限",
    maximum_tail_exposure_rate: "尾部观察比例上限", scheduled_constraints: "定时资源安排",
    delay: "延误", connections: "转机", fuel_risk: "燃油", conflicts: "冲突", emissions: "低碳",
    close: "关闭", open: "开放", priority: "优先", delayCap: "延误上限",
    onTime: "按时转机", missed: "人平均错失转机", tail: "尾部观察",
    questions: {
      average_delay_cap: "平均延误上限是多少分钟？",
      minimum_connection_rate: "按时转机率下限是多少？",
      maximum_missed_connections: "平均错失转机人数上限是多少？",
      maximum_tail_exposure_rate: "尾部观察比例上限是多少？",
      clause: "请把当前有歧义的子句改写为明确要求。",
    },
    placeholders: {
      average_delay_cap: "例如：22", minimum_connection_rate: "例如：95%",
      maximum_missed_connections: "例如：10", maximum_tail_exposure_rate: "例如：0.5%",
      clause: "只改写这条子句，其他目标会保留",
    },
  },
  en: {
    evidence: "Source evidence and retained requirements", clarify: "Clarification required — not executed",
    revise: "Answer each question below. A number is enough for a numeric limit; otherwise rewrite only the ambiguous clause. A reply of “yes” does not resolve ambiguity.",
    legacyRevise: "Rewrite the full command in the main input and parse again; do not reply only “yes”.",
    inherited: "Retained confirmed requirements", clock: "Original draft time reference", unknownClock: "Not recorded",
    fixedClock: "Relative times use this reference; follow-up answers do not reset it.",
    original: "Original command", history: "Clarification history", answer: "Your answer", replacement: "Clarified clause",
    current: "Current goals and constraints · awaiting your confirmation", currentText: "Current effective command",
    submit: "Submit clarification", submitting: "Processing…",
    safety: "Planning checks adequate fuel, safe aircraft spacing and available parking stands.",
    weights: "Objective weights", average_delay_cap: "Mean delay cap",
    minimum_connection_rate: "On-time connection floor", maximum_missed_connections: "Mean missed connections cap",
    maximum_tail_exposure_rate: "Tail exposure cap", scheduled_constraints: "Scheduled resource constraints",
    delay: "Delay", connections: "Connections", fuel_risk: "Fuel", conflicts: "Conflicts", emissions: "Carbon",
    close: "Close", open: "Open", priority: "Prioritize", delayCap: "Delay cap",
    onTime: "on-time connections", missed: "mean missed pax", tail: "tail exposure",
    questions: {
      average_delay_cap: "What is the maximum mean delay in minutes?",
      minimum_connection_rate: "What is the minimum on-time connection rate?",
      maximum_missed_connections: "What is the maximum mean number of missed connections?",
      maximum_tail_exposure_rate: "What is the maximum tail exposure rate?",
      clause: "Rewrite this ambiguous clause as an explicit requirement.",
    },
    placeholders: {
      average_delay_cap: "For example: 22", minimum_connection_rate: "For example: 95%",
      maximum_missed_connections: "For example: 10", maximum_tail_exposure_rate: "For example: 0.5%",
      clause: "Rewrite only this clause; other goals are retained",
    },
  },
} as const;

type ClarificationQuestion = NonNullable<Intent["clarification_questions"]>[number];

function ClarificationInput({ question, language, busy, onClarify }: {
  question: ClarificationQuestion;
  language: Locale;
  busy: boolean;
  onClarify: (issueIndex: number, text: string) => void;
}) {
  const [answer, setAnswer] = useState("");
  const inputId = useId();
  const t = labels[language];
  const prompt = language === "zh" && question.question
    ? question.question : t.questions[question.target];
  const placeholder = language === "zh" && question.placeholder
    ? question.placeholder : t.placeholders[question.target];
  return <form className="intent-clarification-form" onSubmit={(event) => {
    event.preventDefault();
    if (!busy && answer.trim()) onClarify(question.issue_index, answer.trim());
  }}>
    <label htmlFor={inputId}>{prompt}</label>
    <div className="intent-clarification-row">
      <input id={inputId} value={answer} disabled={busy}
        onChange={(event) => setAnswer(event.target.value)} placeholder={placeholder} />
      <button type="submit" disabled={busy || !answer.trim()}>
        {busy ? t.submitting : t.submit}
      </button>
    </div>
  </form>;
}

export function IntentPreview({ intent, onReject, onConfirm, onClarify, busy = false, language }: {
  intent: Intent;
  onReject: () => void;
  onConfirm: () => void;
  onClarify?: (issueIndex: number, text: string) => void;
  busy?: boolean;
  language: Locale;
}) {
  const t = labels[language];
  const issues = intent.issues ?? [];
  const questions = intent.clarification_questions ?? [];
  const history = intent.clarification_history ?? [];
  const blocked = issues.length > 0;
  const canClarify = Boolean(onClarify && questions.length && intent.version != null);
  const fieldName = (name: string): string => {
    const label = t[name as keyof typeof t];
    return typeof label === "string" ? label : name;
  };
  const pct = (value: number) => Number((value * 100).toFixed(3));
  const weights = Object.entries(intent.weights).sort((a, b) => b[1] - a[1]);
  const originalText = intent.original_text ?? intent.raw_text;
  const constraintLabel = (item: Intent["scheduled_constraints"][number]) => {
    if (item.kind === "priority") return t.priority + " " + (item.resource_id ?? item.value ?? "");
    if (item.kind === "delay_cap") return t.delayCap + " " + (item.value ?? "") + " min";
    const action = item.kind === "close_resource" ? t.close : t.open;
    return (item.minute == null ? "" : "T+" + item.minute + " ") + action + " " + (item.resource_id ?? "");
  };
  return (
    <div className="intent-confirmation">
      <span><BrainCircuit size={14} />{intent.summary}</span>
      <details className="intent-draft-context"><summary>{language==="zh"?"查看原指令和时间基准":"Source command and time reference"}</summary>
        <p><strong>{t.original}:</strong> <q>{originalText}</q></p>
        <p><strong>{t.clock}:</strong> {intent.base_minute == null ? t.unknownClock : "T+" + intent.base_minute}
          {intent.base_minute != null && <> · {t.fixedClock}</>}</p>
        {intent.raw_text !== originalText && <p><strong>{t.currentText}:</strong> <q>{intent.raw_text}</q></p>}
        {!!history.length && <details className="intent-clarification-history" aria-label={t.history}>
          <summary>{t.history}{language === "zh" ? "（" + history.length + "轮）" : " (" + history.length + " turns)"}</summary>
          <ol>{history.map((item, index) => <li key={index}>
            <p>{item.question}</p>
            <p><q>{item.excerpt}</q></p>
            <p>{t.answer}: <q>{item.answer}</q></p>
            <p>{t.replacement}: <q>{item.replacement}</q></p>
          </li>)}</ol>
        </details>}
      </details>
      <section className="intent-current-goals" aria-label={t.current}>
        <strong>{language==="zh"?"请确认这就是你的目标":"Review and confirm your goal"}</strong><p className="intent-plain-goal">{intent.raw_text}</p>
        <div className="intent-goal-tags">
          <details className="intent-weights"><summary>{language==="zh"?"优先考虑什么":"Preference weights"}</summary><p>{language==="zh"?"数字越大，规划时越优先考虑这一项。":"Optimization priorities, not passenger success rates."}</p><div className="intent-goal-tags">{weights.map(([name, value]) => <i key={name}>{fieldName(name)} {pct(value)}%</i>)}</div></details>
          {intent.average_delay_cap != null && <i>{t.delayCap} ≤ {intent.average_delay_cap} min</i>}
          {intent.minimum_connection_rate != null && <i>≥ {pct(intent.minimum_connection_rate)}% {t.onTime}</i>}
          {intent.maximum_missed_connections != null && <i>≤ {intent.maximum_missed_connections} {t.missed}</i>}
          {intent.maximum_tail_exposure_rate != null && <i>≤ {pct(intent.maximum_tail_exposure_rate)}% {t.tail}</i>}
          {intent.scheduled_constraints.map((item, i) => <i key={i}>{constraintLabel(item)}</i>)}
        </div>
        {!!intent.inherited_fields?.length && <p>{t.inherited}: {intent.inherited_fields.map(fieldName).join(" · ")}</p>}
        <p className="intent-hard-constraints">{t.safety}</p>
      </section>
      {blocked && <section className="intent-issues" role="alert">
        <strong>{t.clarify}</strong>
        <p>{canClarify ? t.revise : t.legacyRevise}</p>
        <ul>{issues.map((item, index) => {
          const question = questions.find((entry) => entry.issue_index === index);
          return <li key={index}>
            <q>{item.excerpt}</q> — {item.message}
            {!question && canClarify && <p>{t.legacyRevise}</p>}
            {question && onClarify && intent.version != null && <ClarificationInput
              key={String(intent.version) + ":" + index + ":" + item.excerpt}
              question={question} language={language} busy={busy} onClarify={onClarify} />}
          </li>;
        })}</ul>
      </section>}
      <div className="intent-review-actions">
        <button disabled={busy} onClick={onReject}>{copy[language].reject}</button>
        <button className="confirm-button" disabled={blocked || busy}
          title={blocked ? t.clarify : undefined} onClick={() => { if (!blocked && !busy) onConfirm(); }}>
          <Check size={13} />{copy[language].confirm}
        </button>
      </div>
      {((intent.evidence?.length ?? 0) > 0 || (intent.notes?.length ?? 0) > 0) &&
        <details className="intent-evidence">
          <summary>{t.evidence}</summary>
          {intent.evidence?.map((item, i) => <p key={i}>{fieldName(item.field)} ← <q>{item.excerpt}</q></p>)}
          {intent.notes?.map((note, i) => <p key={i}>{note}</p>)}
        </details>}
    </div>
  );
}
