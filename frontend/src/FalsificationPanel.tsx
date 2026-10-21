import { AlertTriangle, Check, Radar, RotateCcw, ShieldCheck } from "lucide-react";
import type { FalsificationAnalysis, FalsificationAxis } from "./types";
import "./falsification.css";

type Language = "zh" | "en";

export function deltaLabel(axis: FalsificationAxis, language: Language) {
  if (axis.status === "lower_bound") {
    const limits = Object.values(axis.target_search_limits ?? {});
    if (!limits.length || !limits.every((value) => Number.isFinite(value) && value >= 0)) {
      return language === "zh" ? "范围未核实" : "RANGE UNVERIFIED";
    }
    const commonLimit = Math.min(...limits);
    if (commonLimit === 0) return language === "zh" ? "部分资源未搜索" : "PARTLY UNSEARCHED";
    return "≥" + commonLimit + (language === "zh" ? " 分钟（共同范围）" : " min (common range)");
  }
  return "+" + axis.delta + (language === "zh" ? " 分钟" : " min");
}

export function FalsificationPanel({
  analysis,
  language,
}: {
  analysis: FalsificationAnalysis;
  language: Language;
}) {
  const nearest = analysis.axes.find((axis) => axis.id === analysis.nearest_witness_id);
  const tierLabel: Record<FalsificationAnalysis["margin_tier"], string> = language === "zh"
    ? {
      thin: "余量很薄",
      watch: "需要监控",
      buffered: "已有缓冲",
      invalid: "基准无效",
      not_applicable: "无需检查",
    }
    : {
      thin: "THIN MARGIN",
      watch: "WATCH",
      buffered: "BUFFERED",
      invalid: "INVALID BASELINE",
      not_applicable: "NOT APPLICABLE",
    };

  return (
    <article className={"falsification-section " + analysis.margin_tier}>
      <header>
        <span><Radar size={18} /></span>
        <div>
          <small>MINIMAL COUNTEREXAMPLE SEARCH · CP-SAT REPAIR</small>
          <strong>{language === "zh" ? "反例搜索：计划会在哪里最先失效？" : "Falsification Lab: where does this plan break first?"}</strong>
          <p>{language === "zh" ? analysis.summary : analysis.summary_en}</p>
        </div>
        <em>{tierLabel[analysis.margin_tier]}</em>
      </header>

      <div className="falsification-metrics">
        <span><small>{language === "zh" ? "自动探测" : "PROBES"}</small><b>{analysis.evaluations}</b></span>
        <span><small>{language === "zh" ? "扰动类型" : "AXES"}</small><b>{analysis.categories_tested}</b></span>
        <span><small>{language === "zh" ? "找到击穿点" : "WITNESSES"}</small><b>{analysis.witness_count}</b></span>
        <p><ShieldCheck size={13} />{language === "zh" ? "反例只在隔离副本中运行，不会修改已批准方案" : "Witnesses run only in an isolated twin; the approved plan is unchanged"}</p>
      </div>

      {!!analysis.axes.length && (
        <div className="falsification-axes">
          {analysis.axes.map((axis) => {
            const isNearest = axis.id === analysis.nearest_witness_id;
            const limits = Object.values(axis.target_search_limits ?? {});
            const verifiedRange = limits.length > 0 && limits.every((value) => Number.isFinite(value) && value > 0);
            const showChain = axis.status === "witness" || verifiedRange;
            return (
              <section className={(isNearest ? "nearest " : "") + axis.status} key={axis.id}>
                <header>
                  <span><small>{language === "zh" ? axis.label : axis.label_en}</small><strong>{axis.target_id ?? (language === "zh" ? "搜索下界" : "SEARCH BOUND")}</strong></span>
                  <b>{deltaLabel(axis, language)}</b>
                  {isNearest && <em>{language === "zh" ? "最近击穿点" : "NEAREST"}</em>}
                </header>
                <p>{language === "zh" ? axis.description : axis.description_en}</p>
                {axis.status === "lower_bound" && axis.target_search_limits && <p>{Object.entries(axis.target_search_limits).map(([id, limit]) => `${id}: 0–${limit} min`).join(" · ")}</p>}
                {showChain && <div className="minimality-chain">
                  <span className="pass"><Check size={10} />BASE</span>
                  <i>→</i>
                  <span className="pass"><Check size={10} />Δ{axis.status === "lower_bound" ? Math.min(...limits) : axis.minimality.predecessor_delta}</span>
                  <i>→</i>
                  <span className={axis.status === "witness" ? "fail" : "pass"}>
                    {axis.status === "witness" ? <AlertTriangle size={10} /> : <Check size={10} />}
                    {axis.status === "witness" ? "FAIL" : "PASS"}
                  </span>
                </div>}
                {isNearest && nearest?.repair && (
                  <div className={"falsification-repair " + nearest.repair.status}>
                    <RotateCcw size={13} />
                    <span>
                      <small>{nearest.repair.available ? "CP-SAT SAFE REPAIR" : "HUMAN GATE"}</small>
                      <strong>{nearest.repair.available
                        ? (language === "zh" ? "安全修复 " + nearest.repair.changed_count + " 架航班" : "Safe repair changes " + nearest.repair.changed_count + " flights")
                        : (language === "zh" ? "未找到自动安全修复" : "No safe automatic repair")}</strong>
                      {!!nearest.repair.changed_flights.length && <code>{nearest.repair.changed_flights.join(" · ")}</code>}
                    </span>
                  </div>
                )}
              </section>
            );
          })}
        </div>
      )}

      <footer>
        <code>{analysis.method}</code>
        <p>{analysis.caveat}</p>
      </footer>
    </article>
  );
}
