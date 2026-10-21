import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PreviewSelectionReceipt } from "./PreviewSelectionReceipt";
import {
  buildPreviewSelectionReceipt, deliverPreviewSelection, mergePreviewReports,
  type BranchPreviewReport, type EventRehearsal, type PreviewSelectionReceiptData,
} from "./branchPreview";
import type { SessionState } from "./types";

const nominal: EventRehearsal = {
  solver_calls: 1, live_approval: false, scheduled_events: [],
  first_decision_boundary: {minute: 9, event_constraint_failures: ["FAIL F1 gate unavailable"], processed_event_ids: ["tug-1"]},
  continuation: {status: "completed", minute: 55, stopped_reason: "recovered",
    pending_flight_ids: [], unprocessed_event_ids: [],
    outcome: {turnaround_complete: 2, safe_diversions: 0, hard_constraints_passed: true},
    repairs: [{minute: 9, status: "nominally_valid_not_approved", before_failures: ["FAIL F1"],
      after_constraint_proof: ["PASS gate"],
      before_assignments: [{flight_id: "F1", runway: "07L", gate: "A1", landing_minute: 11}],
      after_assignments: [{flight_id: "F1", runway: "07L", gate: "A2", landing_minute: 13}]}],
    events: []},
};
const report: BranchPreviewReport = {
  id: "preview-all", session_id: "session-1", status: "fresh", source_digest: "digest-1",
  source_sequence: 4, source_updated_at: "updated-1", source_minute: 0, end_minute: 55,
  method_version: "preview-test", runs: 50, sample_world_index: 0, sample_world_seed: 1,
  scope_flight_ids: ["F1", "F2"], scope_connection_passengers: 20, excluded_flight_ids: ["F0"],
  committed_operations: [], limitations: [],
  branches: ["p1", "p2"].map((id, index) => ({
    plan_id: id, title: index ? "旅客优先" : "均衡恢复", profile: index ? "passenger" : "balanced",
    status: "evaluated", constraint_proof: ["PASS runway"], failures: [],
    scope_flight_ids: ["F1", "F2"], sample: [], rehearsal: nominal,
    report: {runs: 50, mean_delay_min: 3, p90_delay_min: 5, cvar_delay_min: 6,
      missed_connections: 0, fuel_risk_probability: 0, remaining_conflicts: 0,
      emissions_index: 1, recovery_minute: 40, confidence_low: 2, confidence_high: 4, per_flight: {},
      raw_metrics: {mean_delay_min: 3.1415, missed_connections: .49}},
  })),
};
const session = {
  id: "session-1", sequence: 4, updated_at: "updated-1", phase: "awaiting_approval", intent: null,
  plans: [{id: "p1"}, {id: "p2"}],
} as SessionState;
const collection = mergePreviewReports(null, report)!;
const receipt = buildPreviewSelectionReceipt(collection, report, session, "p1")!;
const render = (data: PreviewSelectionReceiptData | null = receipt, current = session, selectedPlanId = "p1", language: "zh" | "en" = "zh") =>
  renderToStaticMarkup(<PreviewSelectionReceipt receipt={data} session={current} selectedPlanId={selectedPlanId} language={language} />);

const delayRun: EventRehearsal = {...nominal, solver_calls: 2,
  first_decision_boundary: {minute: 0, event_constraint_failures: ["FAIL F1 使用了关闭跑道 07R"],
    processed_event_ids: ["whatif:runway-delay:07R:34"]},
  continuation: {...nominal.continuation, status: "needs_decision", minute: 24, stopped_reason: "solver_infeasible",
    pending_flight_ids: ["F1"], outcome: null},
};
const delayed: BranchPreviewReport = {...report, id: "preview-delay",
  runway_delay_hypothesis: {kind: "runway_reopening_delay", runway_id: "07R", delay_min: 10,
    notice_minute: 0, original_reopen_minute: 24, hypothetical_reopen_minute: 34,
    event_id: "whatif:runway-delay:07R:34", scope: "nominal_event_rehearsal_only"},
  branches: report.branches.map(branch => ({...branch, hypothesis_rehearsal: delayRun,
    hypothesis_comparison: {
      baseline: {status: "completed", completed_minute: 55, changed_pending_flight_ids: ["F1"],
        solver_calls: 1, model_calls: 0, outcome: {turnaround_complete: 2, safe_diversions: 0}},
      hypothesis: {status: "needs_decision", completed_minute: null, changed_pending_flight_ids: ["F1"],
        solver_calls: 2, model_calls: 0, outcome: null}, controls: [], caveats: [],
    }})),
};
const fallbackRun: EventRehearsal = {...nominal, model_calls: 0, proposal_trace: [{
  source: "deterministic_fallback", model: "do-not-label-as-model", minute: 9, model_calls: 0,
  fallback_reason: "fallback_mode",
  proposal: {tool: "replan_remaining", preserve_flight_ids: [], evidence_ids: [],
    reason_code: "restore_feasibility", source_digest: "digest-1"},
  evidence: {}, eligible_preserve_ids: [],
}]};
const feedbackReport: BranchPreviewReport = {...report, id: "preview-feedback-p1",
  branches: [{...report.branches[0], feedback_rehearsal: fallbackRun}]};

describe("returning an original candidate with preview evidence", () => {
  it("invokes the production selection callback with candidate id and a structured receipt", () => {
    const onSelect = vi.fn();
    expect(deliverPreviewSelection(collection, report, session, "p1", onSelect)).toBe(true);
    expect(onSelect).toHaveBeenCalledExactlyOnceWith("p1", expect.objectContaining({
      preview_id: "preview-all", session_id: "session-1", source_sequence: 4,
      source_updated_at: "updated-1", source_digest: "digest-1", original_plan_id: "p1",
      mode: "ordinary", original_candidate_only: true, live_approval: false,
    }));
    expect(onSelect.mock.calls[0][1].baseline).toMatchObject({
      status: "completed", end_minute: 55, solver_calls: 1, accepted_repairs: 1, changed_flight_count: 1,
    });
    expect(onSelect.mock.calls[0][1].scope).toMatchObject({
      kind: "isolated_preview_only", flight_ids: ["F1", "F2"], excluded_flight_ids: ["F0"],
    });
  });

  it("uses each candidate's checked owner after a same-source feedback merge", () => {
    const merged = mergePreviewReports(collection, feedbackReport, "p1")!;
    const onSelect = vi.fn();
    expect(deliverPreviewSelection(merged, feedbackReport, session, "p1", onSelect)).toBe(true);
    expect(onSelect.mock.calls[0][1]).toMatchObject({preview_id: "preview-feedback-p1", mode: "feedback",
      feedback: {sources: ["deterministic_fallback"], model_calls: 0}});
    expect(deliverPreviewSelection(merged, report, session, "p2", onSelect)).toBe(true);
    expect(onSelect.mock.calls[1][1]).toMatchObject({
      original_plan_id: "p2", preview_id: "preview-all", mode: "ordinary", feedback: null,
    });
    expect(deliverPreviewSelection(merged, feedbackReport, session, "p2", onSelect)).toBe(false);
    expect(deliverPreviewSelection(merged, report, session, "p1", onSelect)).toBe(false);
    expect(onSelect).toHaveBeenCalledTimes(2);
  });

  it("does not invoke selection for stale source, different session/candidate or no evaluated result", () => {
    const onSelect = vi.fn();
    for (const changed of [
      {...report, status: "stale" as const}, {...report, source_sequence: 5},
      {...report, source_updated_at: "changed"}, {...report, source_digest: "changed"},
      {...report, session_id: "other"},
      {...report, branches: [{...report.branches[0], report: null}]},
      {...report, branches: [{...report.branches[0], status: "invalid" as const}]},
    ]) expect(deliverPreviewSelection(collection, changed, session, "p1", onSelect)).toBe(false);
    expect(deliverPreviewSelection(collection, report, {...session, sequence: 5}, "p1", onSelect)).toBe(false);
    expect(deliverPreviewSelection(collection, report, session, "unknown", onSelect)).toBe(false);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("hides absent, stale, different-candidate and post-approval records", () => {
    expect(render(null)).toBe("");
    expect(render(receipt, session, "p2")).toBe("");
    for (const changed of [
      {...session, id: "other"}, {...session, sequence: 5}, {...session, updated_at: "later"},
      {...session, phase: "executing" as const}, {...session, phase: "recovered" as const},
      {...session, plans: []},
      {...session, intent: {...session.intent, needs_confirmation: true} as SessionState["intent"]},
      {...session, intent: {...session.intent, issues: [{code: "ambiguous", excerpt: "maybe", message: "clarify"}]} as SessionState["intent"]},
    ]) expect(render(receipt, changed)).toBe("");
    expect(render(receipt, {...session, phase: "candidates"})).toContain("这次选择的预演依据");
  });

  it("keeps delay nominal evidence separate from unchanged original 50-world statistics", () => {
    const delayReceipt = buildPreviewSelectionReceipt(mergePreviewReports(null, delayed)!, delayed, session, "p1")!;
    expect(delayReceipt).toMatchObject({mode: "delay", original_statistics: {
      scope: "original_snapshot_random_worlds", runs: 50, mean_delay_min: 3.1415,
      includes_later_events: false, includes_runway_delay_hypothesis: false,
    }, alternative: {status: "needs_decision", end_minute: 24, solver_calls: 2, outcome: null}});
    const html = render(delayReceipt);
    expect(html).toContain("T+24 → T+34");
    expect(html).toContain("仍待决策");
    expect(html).toContain("晚开放通知后的检查");
    expect(html).toContain("FAIL F1 使用了关闭跑道 07R");
    expect(html).toContain("原快照随机统计");
    expect(html).toContain("50 次");
    expect(html).toContain("不含后续事件和晚开放假设");
    expect(html).toContain('<details class="receipt-original-statistics">');
    expect(html).not.toMatch(/<details[^>]*\bopen(?:=|\s|>)/);
    expect(html).toContain("只选择原候选；未批准任何预演修正");
    expect(html).toContain("不代表现场执行成功");
  });

  it("does not attribute another event's boundary to the runway notice", () => {
    const data = buildPreviewSelectionReceipt(mergePreviewReports(null, delayed)!, delayed, session, "p1")!;
    data.alternative = {...data.alternative!, first_boundary: {...data.alternative!.first_boundary!,
      processed_event_ids: ["whatif:runway-delay:07R:34", "different-event"]}};
    const html = render(data);
    expect(html).toContain("首次事件后的检查");
    expect(html).toContain("未定位晚开放通知检查");
    expect(html).not.toContain("晚开放通知后的检查");
  });

  it("states missing nominal results without fabricating completion or zero counts", () => {
    const noNominal: BranchPreviewReport = {...report,
      branches: [{...report.branches[0], rehearsal: null}]};
    const data = buildPreviewSelectionReceipt(mergePreviewReports(null, noNominal)!, noNominal, session, "p1")!;
    expect(data.baseline).toBeNull();
    const html = render(data);
    expect(html).toContain("未记录名义结果");
    expect(html).toContain("未记录事件检查边界");
    expect(html).not.toContain("名义完成");
    expect(html).not.toContain("<td>0</td>");
  });

  it("reports rule fallback and actual calls without promoting it to an LLM proposal", () => {
    const data = buildPreviewSelectionReceipt(mergePreviewReports(collection, feedbackReport, "p1")!, feedbackReport, session, "p1")!;
    expect(data.feedback?.models).toEqual([]);
    const html = render(data);
    expect(html).toContain("规则反馈降级（不是模型建议）");
    expect(html).toContain("实际模型调用：0");
    expect(html).not.toContain("本地模型建议");
    expect(html).not.toContain("do-not-label-as-model");
    const en = render(data, session, "p1", "en");
    expect(en).toContain("Preview evidence for this selection");
    expect(en).toContain("Rule-based fallback (not a model proposal)");
    expect(en).not.toContain("Local model proposal");
    expect(en).toContain("Human approval is still required");
    expect(en).not.toContain("undefined");
  });

  it("does not invent a human rationale or omit event-check limits", () => {
    const data = {...receipt, baseline: {...receipt.baseline!, first_boundary: {
      minute: 9, event_constraint_failures: [], processed_event_ids: ["event-1"],
    }}};
    expect(render(data)).toContain("后续事件仍可能使其失效");
    expect(render(data)).not.toContain("选择理由");
    expect(render(data)).not.toContain("最优");
  });
});
