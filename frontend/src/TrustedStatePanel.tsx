import { AlertTriangle, Check, Database, GitMerge, ShieldCheck } from "lucide-react";
import "./trusted-state.css";
import type { TrustedOperationalState } from "./types";

export function TrustedStatePanel({
  state,
  language,
}: {
  state: TrustedOperationalState;
  language: "zh" | "en";
}) {
  const highlights = state.claims.filter((fact) =>
    fact.status !== "confirmed" || fact.key === "gate.A2.available_minute"
  ).slice(0, 5);
  const argumentation = state.argumentation;
  const argumentNodeById = new Map(
    (argumentation?.nodes ?? []).map((node) => [node.id, node]),
  );
  const statusLabel = (status: string) => {
    if (language === "en") return status.replaceAll("_", " ").toUpperCase();
    if (status === "confirmed") return "已确认·可进入求解器";
    if (status === "conflicted_excluded") return "冲突·已排除";
    if (status === "stale_excluded") return "过期·已排除";
    return status;
  };
  const factLabel = (key: string) => {
    if (key === "gate.A2.available_minute") {
      return language === "zh" ? "A2 最早可用时刻" : "A2 earliest availability";
    }
    if (key.includes("fuel_margin_min")) {
      return language === "zh" ? "SK218 剩余燃油窗口" : "SK218 remaining fuel window";
    }
    return key;
  };

  return (
    <article className={"trusted-state-section " + (state.quality.unresolved_critical ? "blocked" : "ready")}>
      <header>
        <span><Database size={18} /></span>
        <div>
          <small>TYPED EVIDENCE → VERIFIABLE CONSTRAINTS</small>
          <strong>{language === "zh" ? "可信运行状态" : "Trusted operational state"}</strong>
          <p>{language === "zh"
            ? "Agent 先核对来源、时效与冲突；只有确认事实才能编译为 CP-SAT 输入。"
            : "Agents reconcile provenance, freshness and conflicts before any fact can enter CP-SAT."}</p>
        </div>
        <em>{state.quality.unresolved_critical ? "BLOCKED" : "SOLVER READY"}</em>
      </header>

      <div className="trusted-pipeline" aria-label={language === "zh" ? "可信约束编译流水线" : "Trusted constraint pipeline"}>
        {state.pipeline.map((step, index) => (
          <span className={step.status} key={step.stage}>
            <i>{String(index + 1).padStart(2, "0")}</i>
            <strong>{step.stage.replaceAll("_", " ")}</strong>
            <small>{step.output}</small>
          </span>
        ))}
      </div>

      <div className="trusted-metrics">
        <span><small>{language === "zh" ? "确认事实" : "CONFIRMED"}</small><b>{state.quality.solver_facts}</b></span>
        <span><small>{language === "zh" ? "冲突已解决" : "RESOLVED"}</small><b>{state.quality.resolved_conflicts}</b></span>
        <span><small>{language === "zh" ? "过期已排除" : "STALE EXCLUDED"}</small><b>{state.quality.stale_excluded}</b></span>
        <span className={state.quality.unresolved_critical ? "warn" : "clear"}>
          <small>{language === "zh" ? "关键阻塞" : "BLOCKERS"}</small>
          <b>{state.quality.unresolved_critical}</b>
        </span>
      </div>

      {argumentation && (
        <section className={"argumentation-strip " + argumentation.status}>
          <header>
            <GitMerge size={16} />
            <div>
              <small>FORMAL MULTI-AGENT ARGUMENTATION</small>
              <strong>{language === "zh" ? "证据声明如何相互攻击并被裁决" : "How evidence claims attack and defeat one another"}</strong>
              <p>{argumentation.summary}</p>
            </div>
            <em>{argumentation.status === "resolved" ? "GROUNDED · ACCEPT" : "UNDECIDED · ABSTAIN"}</em>
          </header>
          <div className="argumentation-metrics">
            <span><small>ACCEPTED</small><b>{argumentation.accepted_ids.length}</b></span>
            <span><small>REJECTED</small><b>{argumentation.rejected_ids.length}</b></span>
            <span><small>UNDECIDED</small><b>{argumentation.undecided_ids.length}</b></span>
            <span><small>ATTACKS</small><b>{argumentation.attack_count}</b></span>
          </div>
          {!!argumentation.edges.length && (
            <div className="argumentation-edges">
              {argumentation.edges.slice(0, 4).map((edge) => {
                const source = argumentNodeById.get(edge.from);
                const target = argumentNodeById.get(edge.to);
                return (
                  <span key={edge.from + ":" + edge.to}>
                    <i className={source?.verdict}>{source?.source ?? edge.from}</i>
                    <b>DEFEATS</b>
                    <i className={target?.verdict}>{target?.source ?? edge.to}</i>
                    <small>{factLabel(edge.fact_key)} · Δ {edge.score_gap.toFixed(3)}</small>
                  </span>
                );
              })}
            </div>
          )}
          <footer>
            <ShieldCheck size={12} />
            <span>{language === "zh"
              ? "只有 grounded extension 接受的声明能进入约束编译器；平局或仅有过期关键证据时系统拒答。"
              : "Only claims accepted by the grounded extension enter the compiler; ties or stale-only critical evidence force abstention."}</span>
          </footer>
        </section>
      )}

      {!!highlights.length && (
        <div className="evidence-facts">
          {highlights.map((fact) => (
            <span className={fact.status} key={fact.id}>
              {fact.solver_eligible ? <Check size={13} /> : <AlertTriangle size={13} />}
              <div>
                <strong>{factLabel(fact.key)}</strong>
                <small>{fact.source} · {fact.observed_at} · {Math.round(fact.confidence * 100)}%</small>
              </div>
              <code>{String(fact.value ?? "—")}</code>
              <em>{statusLabel(fact.status)}</em>
            </span>
          ))}
        </div>
      )}

      {(state.changed_facts.length > 0 || state.invalidated_constraints.length > 0) && (
        <div className="trusted-change-set">
          <ShieldCheck size={16} />
          <div>
            <strong>{language === "zh" ? "故障后先重建事实，再重规划" : "Reconcile facts before replanning"}</strong>
            <p>{language === "zh"
              ? `${state.changed_facts.length} 项事实变化 · ${state.invalidated_constraints.length} 条约束失效并重新编译`
              : `${state.changed_facts.length} facts changed · ${state.invalidated_constraints.length} constraints invalidated and recompiled`}</p>
          </div>
          <span>{state.changed_facts.slice(0, 3).map((item) => item.key).join(" · ")}</span>
        </div>
      )}

      <footer>
        <code title={state.digest}>INPUT · {state.digest.slice(0, 16)}…</code>
        <p>{state.caveat}</p>
      </footer>
    </article>
  );
}
