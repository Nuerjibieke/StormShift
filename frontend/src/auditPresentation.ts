import type { UiLanguage as Language } from "./localization";
import type { OutcomeAudit, Plan } from "./types";

/** Prefer the unrounded sample mean; 0.5 expected passengers must not become zero. */
export function forecastMetric(report: Plan["report"], key: "mean_delay_min" | "p90_delay_min" | "missed_connections"): number {
  const raw = report.raw_metrics?.[key];
  return typeof raw === "number" && Number.isFinite(raw) ? raw : report[key];
}

export function auditNumber(value: number | null | undefined, digits = 3): string {
  return value == null ? "—" : Number(value.toFixed(digits)).toString();
}

export function auditRate(value: number | null | undefined): string {
  return value == null ? "—" : `${auditNumber(value * 100)}%`;
}

export function baselineLabel(id: string, language: Language): string {
  const labels: Record<string, [string, string]> = {
    rules: ["FCFS · 静态，不更新", "FCFS · static, no update"],
    solo_cp_sat: ["CP-SAT · 静态，不重规划", "CP-SAT · static, no replan"],
    updated_cp_sat: ["CP-SAT · 更新后重规划", "CP-SAT · updated replanning"],
    stormshift: ["证据协调 + CP-SAT", "Evidence + CP-SAT"],
  };
  return labels[id]?.[language === "zh" ? 0 : 1] ?? id;
}

export function auditCheckLabel(status: string | undefined, passed: boolean | null, language: Language): string {
  const labels: Record<string, [string, string]> = {
    pending: ["待执行", "PENDING"], unverified: ["未验证", "UNVERIFIED"],
    superseded: ["已替代/撤销", "SUPERSEDED"], rejected: ["对象无效", "REJECTED"],
  };
  return labels[status ?? ""]?.[language === "zh" ? 0 : 1]
    ?? (passed === true ? "PASS" : passed === false ? "FAIL" : "UNKNOWN");
}

export function auditPassengerMetric(passengers: OutcomeAudit["passengers"], key: "on_time" | "missed"): number {
  const raw = key === "on_time" ? passengers.expected_on_time_unrounded : passengers.expected_missed_unrounded;
  return typeof raw === "number" && Number.isFinite(raw) ? raw
    : key === "on_time" ? passengers.predicted_on_time : passengers.predicted_missed;
}
