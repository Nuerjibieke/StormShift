import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { BranchPreviewContent, RehearsalEvidence, FeedbackComparison, RunwayDelayControl } from "./BranchPreview";
import {
  eligibleRunwayDelays, mergePreviewReports, previewFlightStage, previewIsCurrent, previewSelectionIsValid,
  type BranchPreviewReport, type EventRehearsal,
} from "./branchPreview";
import type { SessionState } from "./types";

const flight = {
  flight_id: "F1", runway: "07L", gate: "A1", landing_minute: 10,
  runway_release_minute: 12, gate_in_minute: 18, gate_out_minute: 40,
  missed_connections: 3, fuel_risk: false,
};
const report: BranchPreviewReport = {
  id: "preview-1", session_id: "session-1", status: "fresh", source_digest: "hash",
  source_sequence: 4, source_updated_at: "2026-10-06", source_minute: 2, end_minute: 40,
  method_version: "entity-world-v1+fixed-reservations-v1", runs: 50,
  sample_world_index: 0, sample_world_seed: 2032, scope_flight_ids: ["F1"],
  scope_connection_passengers: 20, excluded_flight_ids: ["F0"],
  committed_operations: [{ flight_id: "F0", runway: "07R", gate: "A2",
    landing_minute: 0, gate_start_minute: 5, gate_release_minute: 27 }],
  limitations: ["future_events_not_injected"],
  branches: [{
    plan_id: "p1", title: "均衡恢复", profile: "balanced", status: "evaluated",
    constraint_proof: ["PASS runway"], failures: [], scope_flight_ids: ["F1"],
    report: { runs: 50, mean_delay_min: 3, p90_delay_min: 5, cvar_delay_min: 6,
      missed_connections: 0, fuel_risk_probability: 0, remaining_conflicts: 0,
      emissions_index: 1, recovery_minute: 40, confidence_low: 2, confidence_high: 4,
      per_flight: {}, raw_metrics: {mean_delay_min: 3.1415, missed_connections: .49,
        p90_delay_min: 5.06, fuel_risk_probability: .02} },
    sample: [flight],
  }],
};
const render = (r = report, stale = false, language: "zh" | "en" = "zh") =>
  renderToStaticMarkup(<BranchPreviewContent report={r} stale={stale}
    language={language} minute={13} choosing={false} onSelect={() => {}} />);

describe("isolated branch preview", () => {
  it("has explicit stage boundaries instead of disappearing aircraft", () => {
    expect([9, 10, 11, 12, 17, 18, 39, 40].map(t => previewFlightStage(flight, t))).toEqual([
      "waiting", "landing", "landing", "taxiing", "taxiing", "turnaround", "turnaround", "released",
    ]);
  });
  it("invalidates any source change and every stale server response", () => {
    const state = {id: "session-1", sequence: 4, updated_at: "2026-10-06"} as SessionState;
    expect(previewIsCurrent(report, state)).toBe(true);
    expect(previewIsCurrent(report, {...state, sequence: 5})).toBe(false);
    expect(previewIsCurrent(report, {...state, updated_at: "later"})).toBe(false);
    expect(previewIsCurrent(report, {...state, id: "different"})).toBe(false);
    expect(previewIsCurrent({...report, status: "stale"}, state)).toBe(false);
  });
  it("separates exact aggregate metrics, a sampled future and committed facts", () => {
    const html = render();
    expect(html).toContain("0.49");
    expect(html).toContain("3.14");
    expect(html).toContain("2.0");
    expect(html).toContain("不是平均结果");
    expect(html).toContain("F0");
    expect(html).toContain("不重新模拟");
    expect(html).toContain("不自动注入未来故障");
    expect(html).toContain("选择此方案，返回审批");
    expect(html).not.toContain('disabled=""');
  });
  it("disables stale and infeasible selections without inventing zero metrics", () => {
    expect(render(report, true)).toContain('disabled=""');
    const invalid: BranchPreviewReport = {...report, branches: [{
      ...report.branches[0], status: "invalid", report: null, sample: [], failures: ["FAIL missing F2"],
    }]};
    const html = render(invalid);
    expect(html).toContain('disabled=""');
    expect(html).toContain("FAIL missing F2");
    expect(html).not.toContain("0.00");
  });
  it("keeps scope and approval boundaries in the English interface", () => {
    const html = render(report, false, "en");
    expect(html).toContain("Main-screen approval is still required");
    expect(html).toContain("not an average");
    expect(html).toContain("Committed prefix");
    expect(html).not.toContain("undefined");
  });
});

it("separates event-based repairs from statistics and exposes the full trace", () => {
  const data: EventRehearsal = {
    solver_calls: 1, live_approval: false,
    scheduled_events: [{id: "e1", minute: 9, type: "ground_vehicle_failure", resource_id: "TUG-3"}],
    first_decision_boundary: {minute: 9, event_constraint_failures: ["FAIL F2 gate unavailable"]},
    continuation: {
      status: "completed", minute: 70, stopped_reason: "recovered", pending_flight_ids: [],
      unprocessed_event_ids: [], outcome: {turnaround_complete: 2, safe_diversions: 1, hard_constraints_passed: true},
      repairs: [{minute: 9, status: "nominally_valid_not_approved", before_failures: ["FAIL F2"],
        after_constraint_proof: ["PASS gate"], before_assignments: [{flight_id: "F2", runway: "07L", gate: "A3", landing_minute: 12}],
        after_assignments: [{flight_id: "F2", runway: "07L", gate: "A5", landing_minute: 14}]}],
      events: Array.from({length: 80}, (_, i) => ({
        sequence: i + 1, sim_minute: i, type: "execution_tick", payload: {flight_id: "F" + i},
      })),
    },
  };
  const html = renderToStaticMarkup(<RehearsalEvidence data={data} language="zh" />);
  expect(html).toContain("TUG-3"); expect(html).toContain("FAIL F2 gate unavailable");
  expect(html).toContain("A3"); expect(html).toContain("A5");
  expect(html).toContain("F79"); expect(html).toContain("查看完整执行接力");
  expect(html).toContain("尚不是 Agent 自主修正");
  expect(html).toContain("安全备降"); expect(html).toContain("未获批准");
  expect(html).not.toContain("<button");
  const en = renderToStaticMarkup(<RehearsalEvidence data={data} language="en" />);
  expect(en).toContain("not autonomous Agent repair");
  expect(en).not.toContain("undefined");
});

it("does not label a blocked or infeasible rehearsal as recovered", () => {
  const data: EventRehearsal = {
    solver_calls: 1, live_approval: false, scheduled_events: [], first_decision_boundary: null,
    continuation: {status: "needs_decision", minute: 9, stopped_reason: "repair_infeasible",
      pending_flight_ids: ["F2"], unprocessed_event_ids: ["later"], outcome: null,
      repairs: [], events: []},
  };
  const html = renderToStaticMarkup(<RehearsalEvidence data={data} language="zh" />);
  expect(html).toContain("仍需人工决策"); expect(html).toContain("F2");
  expect(html).not.toContain("本轮航班已处理完");
});

it("shows paired actual budgets and fallback without claiming model execution or improvement", () => {
  const branch = {...report.branches[0],
    feedback_rehearsal: {
      controller_version: "grounded-repair-controller-v1", solver_calls: 2, model_calls: 0,
      live_approval: false as const, scheduled_events: [], first_decision_boundary: null,
      proposal_trace: [{source: "deterministic_fallback" as const, model: null, minute: 9,
        model_calls: 0, fallback_reason: "model_disabled",
        proposal: {tool: "preserve_assignments", preserve_flight_ids: ["F2"], evidence_ids: ["failure:0"],
          reason_code: "minimize_disruption", source_digest: "abc"},
        evidence: {"failure:0": "FAIL A3"}, eligible_preserve_ids: ["F2"]}],
      continuation: {status: "needs_decision" as const, minute: 9, stopped_reason: "repair_infeasible",
        pending_flight_ids: ["F1"], unprocessed_event_ids: [], outcome: null, repairs: [], events: []},
    },
    repair_comparison: {
      baseline: {status: "completed", completed_minute: 55, changed_pending_flight_ids: ["F1","F2"],
        solver_calls: 1, model_calls: 0, outcome: {turnaround_complete: 2, safe_diversions: 0}},
      feedback: {status: "needs_decision", completed_minute: null, changed_pending_flight_ids: [],
        solver_calls: 2, model_calls: 0, outcome: null},
      controls: [], caveats: [],
    },
  };
  const html = renderToStaticMarkup(<FeedbackComparison branch={branch} language="zh" />);
  expect(html).toContain("规则反馈降级");
  expect(html).toContain("仍需人工决策");
  expect(html).toContain("不保证反馈方案更好");
  expect(html).toContain("FAIL A3"); expect(html).toContain("F2");
  expect(html).toContain("—"); expect(html).toContain("2/2");
  expect(html).not.toContain("尚不是 Agent 自主修正");
  expect(html).not.toContain("<button");
});

it("disables selection while calculating without falsely claiming the live snapshot changed", () => {
  const html = renderToStaticMarkup(<BranchPreviewContent report={report} stale={false} busy
    language="zh" minute={13} choosing={false} onSelect={() => {}} onFeedback={() => {}} />);
  expect(html).toContain('disabled=""');
  expect(html).not.toContain("现场已变化");
});

const livePreviewState = {id: "session-1", sequence: 4, updated_at: "2026-10-06"} as SessionState;
const allCandidates: BranchPreviewReport = {...report, branches: [
  report.branches[0],
  {...report.branches[0], plan_id: "p2", title: "保转机", profile: "passenger"},
  {...report.branches[0], plan_id: "p3", title: "韧性优先", profile: "resilient"},
]};
const delayedRehearsal: EventRehearsal = {
  solver_calls: 2, live_approval: false,
  scheduled_events: [
    {id: "whatif:runway-delay:07R:34", minute: 2, type: "runway_closed", resource_id: "07R"},
    {id: "tug", minute: 9, type: "ground_vehicle_failure", resource_id: "TUG-3"},
  ],
  first_decision_boundary: {minute: 2, event_constraint_failures: ["FAIL runway 07R remains closed"],
    processed_event_ids: ["whatif:runway-delay:07R:34"]},
  continuation: {
    status: "needs_decision", minute: 18, stopped_reason: "repair_budget_exhausted",
    pending_flight_ids: ["F1"], unprocessed_event_ids: [], outcome: null, repairs: [],
    events: [
      {sequence: 1, sim_minute: 2, type: "runway_closed", payload: {resource_id: "07R", preview_only: true}},
      {sequence: 2, sim_minute: 9, type: "ground_vehicle_failure", payload: {resource_id: "TUG-3"}},
    ],
  },
};
const baselineRehearsal: EventRehearsal = {
  ...delayedRehearsal, solver_calls: 1, scheduled_events: delayedRehearsal.scheduled_events.slice(1),
  first_decision_boundary: {minute: 9, event_constraint_failures: ["FAIL gate A3"]},
  continuation: {...delayedRehearsal.continuation, status: "completed", minute: 70, stopped_reason: "recovered",
    pending_flight_ids: [], outcome: {turnaround_complete: 1, safe_diversions: 0, hard_constraints_passed: true}},
};
const hypothesisReport: BranchPreviewReport = {...report,
  runway_delay_hypothesis: {
    kind: "runway_reopening_delay", runway_id: "07R", delay_min: 10, notice_minute: 2,
    original_reopen_minute: 24, hypothetical_reopen_minute: 34, event_id: "whatif:runway-delay:07R:34",
    scope: "nominal_event_rehearsal_only",
  },
  branches: [{...report.branches[0], rehearsal: baselineRehearsal, hypothesis_rehearsal: delayedRehearsal,
    hypothesis_comparison: {
      baseline: {status: "completed", completed_minute: 70, changed_pending_flight_ids: ["F1"],
        solver_calls: 1, model_calls: 0, outcome: {turnaround_complete: 1, safe_diversions: 0}},
      hypothesis: {status: "needs_decision", completed_minute: null, changed_pending_flight_ids: ["F1", "F2"],
        solver_calls: 2, model_calls: 0, outcome: null},
      controls: ["Same TUG incident"], caveats: ["Nominal rehearsal only"],
    },
  }],
};

describe("runway reopening delay hypothesis", () => {
  it("only offers a closed runway with a future reopening estimate", () => {
    const runway = {id: "07R", status: "closed", reopen_minute: 24, separation_min: 3, capacity_per_hour: 20};
    const session = {...livePreviewState, scenario: {current_minute: 2, runways: [
      runway, {...runway, id: "open", status: "open"}, {...runway, id: "now", reopen_minute: 2},
      {...runway, id: "past", reopen_minute: 1}, {...runway, id: "unknown", reopen_minute: null},
    ]}} as SessionState;
    const available = eligibleRunwayDelays(session);
    expect(available.map(item => item.id)).toEqual(["07R"]);
    const html = renderToStaticMarkup(<RunwayDelayControl runways={available} runwayId="07R"
      language="zh" busy={false} onRunwayChange={() => {}} onRun={() => {}} />);
    expect(html).toContain("假设07R晚10分钟开放");
    expect(html).not.toContain('disabled=""');
    const empty = renderToStaticMarkup(<RunwayDelayControl runways={[]} runwayId=""
      language="en" busy={false} onRunwayChange={() => {}} onRun={() => {}} />);
    expect(empty).toContain("No closed runway");
    expect(empty).toContain('disabled=""');
    expect(session.scenario.current_minute).toBe(2);
    expect(runway.reopen_minute).toBe(24);
  });

  it("shows nominal before/after outcomes above unchanged random-world statistics", () => {
    const html = render(hypothesisReport);
    expect(html).toContain("T+24 → T+34");
    expect(html).toContain("T+2 收到通知");
    expect(html).toContain("T+70");
    expect(html).toContain("T+18");
    expect(html).toContain("本轮航班已处理完");
    expect(html).toContain("仍需人工决策");
    expect(html).toContain("1/2");
    expect(html).toContain("2/2");
    expect(html).toContain("不含晚开放假设与后续事件");
    expect(html).toContain("3.14");
    expect(html.indexOf("跑道晚开放 · 名义对照")).toBeLessThan(html.indexOf("3.14"));
    expect(html).toContain("选择原候选，返回审批");
    expect(html).toContain("不能批准假设修正");
    expect(html).not.toContain("假设方案已批准");
  });

  it("shows the first two notice failures immediately and preserves the complete evidence in the trace", () => {
    const failures = ["FAIL first flight", "FAIL second flight", "FAIL third flight"];
    const withFailures = {...hypothesisReport, branches: [{...hypothesisReport.branches[0],
      hypothesis_rehearsal: {...delayedRehearsal,
        first_decision_boundary: {...delayedRehearsal.first_decision_boundary!, event_constraint_failures: failures}},
    }]};
    const html = render(withFailures);
    const reason = html.match(/<div class="preview-hypothesis-reason">([\s\S]*?)<\/div>/)?.[1];
    expect(reason).toContain("晚开放通知后的检查 · T+2");
    expect(reason).toContain(failures[0]);
    expect(reason).toContain(failures[1]);
    expect(reason).not.toContain(failures[2]);
    expect(html).toContain(failures[2]);
  });

  it("bounds a passing notice check to that instant, without promising future feasibility", () => {
    const noFailures = {...hypothesisReport, branches: [{...hypothesisReport.branches[0],
      hypothesis_rehearsal: {...delayedRehearsal,
        first_decision_boundary: {...delayedRehearsal.first_decision_boundary!, event_constraint_failures: []}},
    }]};
    expect(render(noFailures)).toContain("收到晚开放通知时，原计划仍满足名义约束。");
    expect(render(noFailures)).toContain("后续事件仍可能使计划失效");
    const english = render(noFailures, false, "en");
    expect(english).toContain("At receipt of the delay notice, the original plan still meets the nominal constraints.");
    expect(english).toContain("later events may still invalidate the plan");
    expect(english).toContain("Needs a decision");
  });

  it("does not attribute a same-minute different event or a missing boundary to the notice", () => {
    const otherEvent = {...hypothesisReport, branches: [{...hypothesisReport.branches[0],
      hypothesis_rehearsal: {...delayedRehearsal, first_decision_boundary: {
        minute: 2, event_constraint_failures: [], processed_event_ids: ["whatif:runway-delay:07R:34", "other"],
      }},
    }]};
    const html = render(otherEvent);
    expect(html).toContain("首次事件后的检查 · T+2");
    expect(html).toContain("尚未定位晚开放通知的检查");
    expect(html).not.toContain("收到晚开放通知时，原计划仍满足名义约束。");
    const missing = {...otherEvent, branches: [{...otherEvent.branches[0],
      hypothesis_rehearsal: {...delayedRehearsal, first_decision_boundary: null},
    }]};
    expect(render(missing)).toContain("尚无通知时的决策边界记录");
    expect(render(missing)).not.toContain("收到晚开放通知时，原计划仍满足名义约束。");
    expect(render(otherEvent, false, "en")).toContain("Check after the first event");
  });

  it("collapses only the hypothesis view's original statistics and all sample flights", () => {
    const withEightFlights = {...hypothesisReport, branches: [{...hypothesisReport.branches[0],
      sample: Array.from({length: 8}, (_, index) => ({...flight, flight_id: "F" + (index + 1)})),
    }]};
    const html = render(withEightFlights);
    const snapshot = html.match(/<details class="preview-original-snapshot"([^>]*)>([\s\S]*?)<\/details>/);
    expect(snapshot).not.toBeNull();
    expect(snapshot![1]).not.toMatch(/\bopen(?:=|\s|$)/);
    expect(snapshot![2]).toContain("不含晚开放假设与后续事件");
    expect(snapshot![2]).toContain("3.14");
    expect(snapshot![2]).toContain("F1");
    expect(snapshot![2]).toContain("F8");
    expect(render()).not.toContain('class="preview-original-snapshot"');
    expect(render()).toContain('class="preview-metrics"');
  });

  it("keeps both complete traces collapsed and disables feedback in a hypothesis report", () => {
    const html = renderToStaticMarkup(<BranchPreviewContent report={hypothesisReport} stale={false}
      language="en" minute={13} choosing={false} onSelect={() => {}} onFeedback={() => {}} />);
    const tags = html.match(/<details class="preview-hypothesis-trace"[^>]*>/g);
    expect(tags).toHaveLength(2);
    for (const tag of tags!) expect(tag).not.toMatch(/\bopen(?:=|\s|>)/);
    expect(html).toContain("Runway closed");
    expect(html).toContain("preview_only");
    expect(html).toContain("TUG-3");
    expect(html).toContain("FAIL runway 07R remains closed");
    expect(html).toContain("Same TUG incident");
    expect(html).toContain('disabled="" title="The delay hypothesis cannot run with model feedback.');
    expect(html).toContain("Select original candidate for approval");
    expect(html).toContain("Original snapshot · 50-world statistics");
    expect(html).not.toContain("undefined");
  });
});

describe("candidate-owned preview reports", () => {
  it("merges feedback for one candidate and retains every other candidate and report owner", () => {
    const initial = mergePreviewReports(null, allCandidates)!;
    const feedback = {...allCandidates, id: "feedback-p2", branches: [{
      ...allCandidates.branches[1], feedback_rehearsal: delayedRehearsal,
    }]};
    const merged = mergePreviewReports(initial, feedback, "p2")!;
    expect(merged.report.branches.map(branch => branch.plan_id)).toEqual(["p1", "p2", "p3"]);
    expect(merged.report.branches[0]).toBe(allCandidates.branches[0]);
    expect(merged.report.branches[1]).toBe(feedback.branches[0]);
    expect(merged.report.branches[2]).toBe(allCandidates.branches[2]);
    expect(merged.reportIdsByPlan).toEqual({p1: "preview-1", p2: "feedback-p2", p3: "preview-1"});
    expect(initial.reportIdsByPlan.p2).toBe("preview-1");
    expect(previewSelectionIsValid(merged, feedback, livePreviewState, "p2")).toBe(true);
    expect(previewSelectionIsValid(merged, allCandidates, livePreviewState, "p1")).toBe(true);
    expect(previewSelectionIsValid(merged, feedback, livePreviewState, "p1")).toBe(false);
    expect(previewSelectionIsValid(merged, allCandidates, livePreviewState, "p2")).toBe(false);
    expect(previewSelectionIsValid(merged, {...feedback, status: "stale"}, livePreviewState, "p2")).toBe(false);
    expect(previewSelectionIsValid(merged, {...feedback, branches: []}, livePreviewState, "p2")).toBe(false);
  });

  it("rejects feedback from another snapshot, another candidate, or a hypothesis run", () => {
    const initial = mergePreviewReports(null, allCandidates)!;
    const feedback = {...allCandidates, id: "feedback-p2", branches: [allCandidates.branches[1]]};
    expect(mergePreviewReports(initial, {...feedback, source_digest: "other"}, "p2")).toBeNull();
    expect(mergePreviewReports(initial, {...feedback, source_sequence: 5}, "p2")).toBeNull();
    expect(mergePreviewReports(initial, {...feedback, status: "stale"}, "p2")).toBeNull();
    expect(mergePreviewReports(initial, feedback, "p3")).toBeNull();
    expect(mergePreviewReports(initial, hypothesisReport, "p1")).toBeNull();
    expect(mergePreviewReports(mergePreviewReports(null, hypothesisReport), feedback, "p2")).toBeNull();
  });

  it("resets report ownership for a fresh full comparison", () => {
    const initial = mergePreviewReports(null, allCandidates)!;
    const next = mergePreviewReports(initial, {...allCandidates, id: "full-new"})!;
    expect(Object.values(next.reportIdsByPlan)).toEqual(["full-new", "full-new", "full-new"]);
  });
});

it("explains fractional passenger averages and lists every sampled flight in the simple view", () => {
  const expanded = {...report, scope_flight_ids: ["F1", "F2", "F3"], branches: [{
    ...report.branches[0], sample: [flight, {...flight, flight_id: "F2"}, {...flight, flight_id: "F3"}],
  }]};
  const html = renderToStaticMarkup(<BranchPreviewContent report={expanded} stale={false}
    language="zh" minute={13} choosing={false} compact onSelect={() => {}}/>);
  expect(html).toContain("50 次模拟的平均人数");
  expect(html).toContain("原计划时间表");
  expect(html).toContain("F3");
  expect(html).toContain("第 18.0 分钟 · 到 A1 停靠");
  expect(html).toContain("第 40.0 分钟 · A1 可交给下一架");
});
