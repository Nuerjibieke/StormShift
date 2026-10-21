import type { Plan, Runway, SessionState } from "./types";

export interface PreviewFlight {
  flight_id: string; runway: string; gate: string;
  landing_minute: number; runway_release_minute: number;
  gate_in_minute: number; gate_out_minute: number;
  missed_connections: number; fuel_risk: boolean;
}
export interface RunwayDelayRequest {
  runway_id: string;
  delay_min: 10;
}
export interface RunwayDelayHypothesis {
  kind: "runway_reopening_delay";
  runway_id: string;
  delay_min: number;
  notice_minute: number;
  original_reopen_minute: number;
  hypothetical_reopen_minute: number;
  event_id: string;
  scope: "nominal_event_rehearsal_only";
}
export interface HypothesisComparison {
  baseline: RepairComparisonRow;
  hypothesis: RepairComparisonRow;
  controls: string[];
  caveats: string[];
}
export interface RepairComparison {
  baseline: RepairComparisonRow; feedback: RepairComparisonRow;
  controls: string[]; caveats: string[];
}
export interface RepairComparisonRow {
  status: string; completed_minute: number | null;
  changed_pending_flight_ids: string[]; solver_calls: number; model_calls: number;
  outcome: {turnaround_complete: number; safe_diversions: number} | null;
}
export interface RepairProposalTrace {
  source: "fast_model" | "deterministic_fallback"; model: string | null;
  minute: number; model_calls: number; fallback_reason: string | null;
  proposal: {tool: string; preserve_flight_ids: string[]; evidence_ids: string[]; reason_code: string; source_digest: string};
  evidence: Record<string, unknown>; eligible_preserve_ids: string[];
}
export interface EventRehearsal {
  controller_version?: string; proposal_trace?: RepairProposalTrace[]; model_calls?: number;
  solver_calls: number; live_approval: false;
  scheduled_events: Array<{id: string; minute: number; type: string; resource_id?: string | null}>;
  first_decision_boundary: null | {minute: number; event_constraint_failures: string[]; processed_event_ids?: string[]};
  continuation: {
    status: "completed" | "needs_decision"; minute: number; stopped_reason: string | null;
    pending_flight_ids: string[]; unprocessed_event_ids: string[];
    outcome: null | {turnaround_complete: number; safe_diversions: number; hard_constraints_passed: boolean};
    repairs: Array<{
      minute: number; status: string; before_failures: string[]; after_constraint_proof: string[];
      before_assignments: Array<{flight_id: string; runway: string; gate: string; landing_minute: number}>;
      after_assignments: Array<{flight_id: string; runway: string; gate: string; landing_minute: number}>;
    }>;
    events: Array<{sequence: number; sim_minute: number; type: string; payload: Record<string, unknown>}>;
  };
}
export interface PreviewBranch {
  plan_id: string; title: string; profile: string;
  status: "evaluated" | "invalid";
  constraint_proof: string[]; failures: string[];
  rehearsal?: EventRehearsal | null;
  hypothesis_rehearsal?: EventRehearsal | null;
  hypothesis_comparison?: HypothesisComparison | null;
  feedback_rehearsal?: EventRehearsal; repair_comparison?: RepairComparison;
  report: Plan["report"] | null; sample: PreviewFlight[]; scope_flight_ids: string[];
}
export interface BranchPreviewReport {
  id: string; session_id: string; status: "fresh" | "stale";
  source_digest: string; source_sequence: number; source_updated_at: string;
  source_minute: number; end_minute: number; method_version: string;
  runs: number; sample_world_index: number; sample_world_seed: number;
  scope_flight_ids: string[]; scope_connection_passengers: number;
  excluded_flight_ids: string[];
  committed_operations: Array<{
    flight_id: string; runway: string; gate: string;
    landing_minute: number; gate_start_minute: number; gate_release_minute: number;
  }>;
  branches: PreviewBranch[]; limitations: string[];
  runway_delay_hypothesis?: RunwayDelayHypothesis | null;
}
export function previewIsCurrent(report: BranchPreviewReport, session: SessionState): boolean {
  return report.status === "fresh" && report.session_id === session.id
    && report.source_sequence === session.sequence && report.source_updated_at === session.updated_at;
}
export function previewFlightStage(row: PreviewFlight, minute: number):
  "waiting" | "landing" | "taxiing" | "turnaround" | "released" {
  if (minute < row.landing_minute) return "waiting";
  if (minute < row.runway_release_minute) return "landing";
  if (minute < row.gate_in_minute) return "taxiing";
  if (minute < row.gate_out_minute) return "turnaround";
  return "released";
}

export interface PreviewCollection {
  report: BranchPreviewReport;
  reportIdsByPlan: Record<string, string>;
}

export function eligibleRunwayDelays(session: SessionState): Runway[] {
  return session.scenario.runways.filter(runway => runway.status === "closed"
    && runway.reopen_minute != null && Number.isFinite(runway.reopen_minute)
    && runway.reopen_minute > session.scenario.current_minute);
}

function samePreviewSource(left: BranchPreviewReport, right: BranchPreviewReport): boolean {
  return left.session_id === right.session_id && left.source_digest === right.source_digest
    && left.source_sequence === right.source_sequence && left.source_updated_at === right.source_updated_at;
}

/** A feedback run replaces one candidate, never the other candidates or their report ownership. */
export function mergePreviewReports(
  current: PreviewCollection | null,
  incoming: BranchPreviewReport,
  feedbackPlanId?: string,
): PreviewCollection | null {
  if (!feedbackPlanId) return {
    report: incoming,
    reportIdsByPlan: Object.fromEntries(incoming.branches.map(branch => [branch.plan_id, incoming.id])),
  };
  if (!current || current.report.status !== "fresh" || incoming.status !== "fresh"
    || !samePreviewSource(current.report, incoming)
    || current.report.runway_delay_hypothesis || incoming.runway_delay_hypothesis
    || !current.report.branches.some(branch => branch.plan_id === feedbackPlanId)
    || incoming.branches.length !== 1 || incoming.branches[0].plan_id !== feedbackPlanId) return null;
  return {
    report: {...current.report,
      end_minute: Math.max(current.report.end_minute, incoming.end_minute),
      branches: current.report.branches.map(branch =>
        branch.plan_id === feedbackPlanId ? incoming.branches[0] : branch),
    },
    reportIdsByPlan: {...current.reportIdsByPlan, [feedbackPlanId]: incoming.id},
  };
}

export function previewSelectionIsValid(
  collection: PreviewCollection,
  checked: BranchPreviewReport,
  session: SessionState,
  planId: string,
): boolean {
  return collection.reportIdsByPlan[planId] === checked.id
    && samePreviewSource(collection.report, checked)
    && previewIsCurrent(collection.report, session) && previewIsCurrent(checked, session)
    && collection.report.branches.some(branch => branch.plan_id === planId && branch.status === "evaluated")
    && checked.branches.some(branch => branch.plan_id === planId && branch.status === "evaluated");
}


export interface PreviewNominalSummary {
  status: EventRehearsal["continuation"]["status"];
  end_minute: number;
  solver_calls: number;
  accepted_repairs: number;
  changed_flight_count: number;
  pending_flight_count: number;
  outcome: EventRehearsal["continuation"]["outcome"];
  first_boundary: EventRehearsal["first_decision_boundary"];
  stopped_reason: string | null;
}

/** Evidence attached to returning an original candidate, never a live execution or approval. */
export interface PreviewSelectionReceiptData {
  preview_id: string;
  session_id: string;
  source_status: "fresh";
  source_sequence: number;
  source_updated_at: string;
  source_digest: string;
  source_minute: number;
  original_plan_id: string;
  title: string;
  profile: string;
  mode: "ordinary" | "delay" | "feedback";
  scope: {
    kind: "isolated_preview_only";
    flight_ids: string[];
    excluded_flight_ids: string[];
    connection_passengers: number;
  };
  original_statistics: {
    scope: "original_snapshot_random_worlds";
    includes_later_events: false;
    includes_runway_delay_hypothesis: false;
    runs: number;
    mean_delay_min: number | null;
    p90_delay_min: number | null;
    missed_connections: number | null;
    fuel_risk_probability: number | null;
  };
  baseline: PreviewNominalSummary | null;
  alternative: PreviewNominalSummary | null;
  runway_delay_hypothesis: RunwayDelayHypothesis | null;
  feedback: null | {
    sources: RepairProposalTrace["source"][];
    models: string[];
    model_calls: number | null;
  };
  original_candidate_only: true;
  live_approval: false;
}

function nominalSummary(run?: EventRehearsal | null, row?: RepairComparisonRow): PreviewNominalSummary | null {
  if (!run) return null;
  const accepted = run.continuation.repairs.filter(repair => repair.status === "nominally_valid_not_approved");
  const changed = new Set<string>();
  for (const repair of accepted) {
    for (const after of repair.after_assignments) {
      const before = repair.before_assignments.find(item => item.flight_id === after.flight_id);
      if (!before || before.runway !== after.runway || before.gate !== after.gate
        || before.landing_minute !== after.landing_minute) changed.add(after.flight_id);
    }
  }
  return {
    status: run.continuation.status, end_minute: run.continuation.minute,
    solver_calls: run.solver_calls, accepted_repairs: accepted.length,
    changed_flight_count: row ? new Set(row.changed_pending_flight_ids).size : changed.size,
    pending_flight_count: run.continuation.pending_flight_ids.length,
    outcome: run.continuation.outcome,
    first_boundary: run.first_decision_boundary,
    stopped_reason: run.continuation.stopped_reason,
  };
}

/** The server-checked owner supplies mode and evidence; a merged collection is only the candidate index. */
export function buildPreviewSelectionReceipt(
  collection: PreviewCollection,
  checked: BranchPreviewReport,
  session: SessionState,
  planId: string,
): PreviewSelectionReceiptData | null {
  if (!previewSelectionIsValid(collection, checked, session, planId)) return null;
  const branch = checked.branches.find(item => item.plan_id === planId);
  if (!branch?.report || !Number.isFinite(branch.report.runs) || branch.report.runs <= 0) return null;
  const stats = branch.report;
  const metric = (field: "mean_delay_min" | "p90_delay_min" | "missed_connections" | "fuel_risk_probability") => {
    const value = stats.raw_metrics?.[field] ?? stats[field];
    return Number.isFinite(value) ? value : null;
  };
  const hypothesis = checked.runway_delay_hypothesis ?? null;
  const mode = hypothesis ? "delay" : branch.feedback_rehearsal ? "feedback" : "ordinary";
  const comparison = mode === "delay" ? branch.hypothesis_comparison : branch.repair_comparison;
  const alternative = mode === "delay" ? branch.hypothesis_rehearsal
    : mode === "feedback" ? branch.feedback_rehearsal : null;
  const alternativeRow = mode === "delay" ? branch.hypothesis_comparison?.hypothesis
    : mode === "feedback" ? branch.repair_comparison?.feedback : undefined;
  const receipt: PreviewSelectionReceiptData = {
    preview_id: checked.id, session_id: checked.session_id, source_status: "fresh",
    source_sequence: checked.source_sequence, source_updated_at: checked.source_updated_at,
    source_digest: checked.source_digest, source_minute: checked.source_minute,
    original_plan_id: branch.plan_id, title: branch.title, profile: branch.profile, mode,
    scope: {kind: "isolated_preview_only", flight_ids: [...branch.scope_flight_ids],
      excluded_flight_ids: [...checked.excluded_flight_ids],
      connection_passengers: checked.scope_connection_passengers},
    original_statistics: {scope: "original_snapshot_random_worlds", includes_later_events: false,
      includes_runway_delay_hypothesis: false, runs: stats.runs,
      mean_delay_min: metric("mean_delay_min"), p90_delay_min: metric("p90_delay_min"),
      missed_connections: metric("missed_connections"), fuel_risk_probability: metric("fuel_risk_probability")},
    baseline: nominalSummary(branch.rehearsal, comparison?.baseline),
    alternative: nominalSummary(alternative, alternativeRow),
    runway_delay_hypothesis: hypothesis,
    feedback: mode === "feedback" && branch.feedback_rehearsal ? {
      sources: [...new Set((branch.feedback_rehearsal.proposal_trace ?? []).map(trace => trace.source))],
      models: [...new Set((branch.feedback_rehearsal.proposal_trace ?? [])
        .filter(trace => trace.source === "fast_model" && trace.model).map(trace => trace.model!))],
      model_calls: Number.isFinite(branch.feedback_rehearsal.model_calls)
        ? branch.feedback_rehearsal.model_calls! : null,
    } : null,
    original_candidate_only: true, live_approval: false,
  };
  return previewReceiptIsCurrent(receipt, session, planId) ? receipt : null;
}

export function previewReceiptIsCurrent(
  receipt: PreviewSelectionReceiptData | null | undefined,
  session: SessionState,
  selectedPlanId: string,
): receipt is PreviewSelectionReceiptData {
  return Boolean(receipt && receipt.source_status === "fresh"
    && receipt.session_id === session.id && receipt.source_sequence === session.sequence
    && receipt.source_updated_at === session.updated_at
    && receipt.original_plan_id === selectedPlanId
    && session.plans.some(plan => plan.id === selectedPlanId)
    && (session.phase === "candidates" || session.phase === "awaiting_approval")
    && !session.intent?.needs_confirmation && !session.intent?.issues?.length);
}

/** Shared by the actual selection handler and tests, after GET validates the candidate's report. */
export function deliverPreviewSelection(
  collection: PreviewCollection,
  checked: BranchPreviewReport,
  session: SessionState,
  planId: string,
  onSelect: (id: string, receipt: PreviewSelectionReceiptData) => void,
): boolean {
  const receipt = buildPreviewSelectionReceipt(collection, checked, session, planId);
  if (!receipt) return false;
  onSelect(planId, receipt);
  return true;
}
