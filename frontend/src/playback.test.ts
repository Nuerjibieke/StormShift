import { describe, expect, it } from "vitest";
import { buildPlaybackSchedule, playbackClockSamples, formatSimulationMinute, compareExecutionEvents, executionFlightLedger, executionFlightLedgerAt, isMissionBriefingEvent, missionChapterAt, missionChapterForEvent, runwayAllowsUseAt, runwayPlanDisclosureAt, runwayStateAt, runwayTimelineLabel } from "./playback";
import type { ExecutionEvent, Runway } from "./types";

const event = (
  sequence: number,
  simMinute: number,
  type: string,
  payload: Record<string, unknown> = {},
): ExecutionEvent => ({
  sequence,
  session_id: "session_test",
  sim_minute: simMinute,
  type,
  payload,
  created_at: "2026-09-02T00:00:00Z",
});

describe("buildPlaybackSchedule", () => {
  it("holds critical narrative events long enough to read", () => {
    const schedule = buildPlaybackSchedule([
      event(1, 0, "plan_approved"),
      event(8, 9, "ground_vehicle_failure"),
      event(9, 10, "automatic_replan"),
      event(4, 10, "execution_tick"),
    ]);
    expect(schedule[2].delayMs - schedule[1].delayMs).toBeGreaterThanOrEqual(1400);
    expect(schedule[3].delayMs - schedule[2].delayMs).toBeGreaterThanOrEqual(1400);
  });

  it("does not announce recovery before the final aircraft settles", () => {
    const schedule = buildPlaybackSchedule([
      event(1, 4, "execution_tick"),
      event(2, 5, "recovered"),
    ]);
    expect(schedule[1].delayMs - schedule[0].delayMs).toBeGreaterThanOrEqual(6220);
  });
  it("shows trusted-state reconciliation between a fault and CP-SAT replanning", () => {
    const schedule = buildPlaybackSchedule([
      event(1, 9, "ground_vehicle_failure"),
      event(2, 9, "trusted_state_reconciled"),
      event(3, 10, "automatic_replan"),
    ]);
    expect(schedule.map((item) => item.event.type)).toEqual([
      "ground_vehicle_failure", "trusted_state_reconciled", "automatic_replan",
    ]);
    expect(schedule[1].delayMs - schedule[0].delayMs).toBeGreaterThanOrEqual(1400);
    expect(schedule[2].delayMs - schedule[1].delayMs).toBeGreaterThanOrEqual(1400);
  });
});

describe("mission guidance", () => {
  const story = [
    event(1, 0, "plan_approved"),
    event(2, 4, "execution_tick"),
    event(3, 9, "ground_vehicle_failure"),
    event(4, 10, "automatic_replan"),
    event(5, 13, "execution_tick"),
    event(6, 18, "recovered"),
  ];

  it("maps the execution log to five stable story chapters", () => {
    expect(story.map((_, index) => missionChapterAt(story, index))).toEqual([0, 1, 2, 3, 3, 4]);
  });

  it("pauses only at explanation-critical decision boundaries", () => {
    expect(story.filter(isMissionBriefingEvent).map((item) => item.type)).toEqual([
      "ground_vehicle_failure",
      "automatic_replan",
    ]);
  });
});

describe("mission chapters across streamed batches", () => {
  const story = [
    event(1, 0, "plan_approved"),
    event(2, 4, "execution_tick"),
    event(3, 9, "ground_vehicle_failure"),
    event(4, 9, "automatic_replan"),
    event(5, 9, "plan_approved"),
    event(6, 13, "execution_tick"),
    event(7, 18, "recovered"),
  ];

  it("matches the full story for every batch split and duplicate delivery", () => {
    for (let split = 0; split <= story.length; split += 1) {
      const revealed: ExecutionEvent[] = [];
      const chapters: number[] = [];
      for (const batch of [story.slice(0, split), story.slice(split)]) {
        for (const current of batch) {
          chapters.push(missionChapterForEvent(revealed, current));
          revealed.push(current, current);
        }
      }
      expect(chapters).toEqual([0, 1, 2, 3, 3, 3, 4]);
    }
  });

  it("does not reveal a later same-minute repair before the fault", () => {
    expect(missionChapterForEvent([...story].reverse(), story[2])).toBe(2);
    expect(missionChapterForEvent(story, story[3])).toBe(3);
  });

  it("keeps recovery on subsequent housekeeping events", () => {
    expect(missionChapterForEvent(story, event(8, 18, "clock_advanced"))).toBe(4);
  });

  it("shows a new fault after an earlier repair instead of stale recovery", () => {
    const fault = event(8, 20, "ground_vehicle_failure");
    expect(missionChapterForEvent(story, fault)).toBe(2);
    expect(missionChapterForEvent([...story, fault], event(9, 21, "execution_tick"))).toBe(2);
  });

  it("starts replay from its revealed prefix and ignores another session", () => {
    expect(missionChapterForEvent([], story[0])).toBe(0);
    const next = { ...event(1, 0, "plan_approved"), session_id: "cloned_session" };
    expect(missionChapterForEvent(story, next)).toBe(0);
  });
});

describe("runway timeline state", () => {
  const closedRunway: Runway = {
    id: "07R",
    status: "closed",
    separation_min: 3,
    capacity_per_hour: 19,
    reopen_minute: 24,
  };

  it("keeps a runway closed before its recovery minute and reopens it on time", () => {
    expect(runwayStateAt(closedRunway, 23)).toBe("closed");
    expect(runwayStateAt(closedRunway, 24)).toBe("reopened");
    expect(runwayAllowsUseAt(closedRunway, 23)).toBe(false);
    expect(runwayAllowsUseAt(closedRunway, 24)).toBe(true);
    expect(runwayTimelineLabel(closedRunway, 0)).toBe("07R · CLOSED NOW · REOPEN T+24 · 24m");
  });

  it("distinguishes a safe future landing from an impossible use while the runway is red", () => {
    expect(runwayPlanDisclosureAt(closedRunway, [24, 31], 0)).toEqual({
      firstLandingMinute: 24,
      state: "after-reopen",
    });
    expect(runwayPlanDisclosureAt(closedRunway, [23, 31], 0)).toEqual({
      firstLandingMinute: 23,
      state: "safety-violation",
    });
    expect(runwayPlanDisclosureAt(closedRunway, [24, 31], 24)).toEqual({
      firstLandingMinute: 24,
      state: "available-now",
    });
  });

  it("keeps an indefinite closure closed", () => {
    expect(runwayStateAt({ ...closedRunway, reopen_minute: null }, 120)).toBe("closed");
  });

  it("orders a reopening before a same-minute landing", () => {
    const reopened = event(4, 24, "runway_reopened");
    const landing = event(8, 24, "execution_tick");
    expect([landing, reopened].sort(compareExecutionEvents).map((item) => item.type)).toEqual([
      "runway_reopened",
      "execution_tick",
    ]);
  });
});

describe("execution continuity", () => {
  it("does not interrupt an aircraft before it reaches its gate", () => {
    const schedule = buildPlaybackSchedule([
      event(1, 4, "execution_tick"),
      event(2, 9, "ground_vehicle_failure"),
    ]);
    expect(schedule[1].delayMs - schedule[0].delayMs).toBeGreaterThanOrEqual(5420);
  });

  it("keeps the executed prefix when a revised plan replaces future actions", () => {
    const prefix = event(2, 4, "execution_tick", {
      flight_id: "SK218", runway: "07L", gate: "A3",
      plan_id: "plan_old", schedule_revision: "approved",
    });
    const superseded = event(3, 20, "execution_tick", {
      flight_id: "CX551", runway: "07L", gate: "A4",
      plan_id: "plan_old", schedule_revision: "approved",
    });
    const revised = event(8, 14, "execution_tick", {
      flight_id: "CX551", runway: "07R", gate: "A6",
      plan_id: "plan_new", schedule_revision: "automatic_replan",
    });

    expect(executionFlightLedger([revised, prefix, superseded])).toEqual([
      {
        flight_id: "SK218",
        runway: "07L",
        gate: "A3",
        landing_minute: 4,
        plan_id: "plan_old",
        schedule_revision: "approved",
      },
      {
        flight_id: "CX551",
        runway: "07R",
        gate: "A6",
        landing_minute: 14,
        plan_id: "plan_new",
        schedule_revision: "automatic_replan",
      },
    ]);
  });

  it("does not expose a future revised assignment before its simulated time", () => {
    const prefix = event(2, 4, "execution_tick", {
      flight_id: "SK218", runway: "07L", gate: "A3",
      plan_id: "plan_old", schedule_revision: "approved",
    });
    const revised = event(8, 14, "execution_tick", {
      flight_id: "CX551", runway: "07R", gate: "A6",
      plan_id: "plan_new", schedule_revision: "automatic_replan",
    });

    expect(executionFlightLedgerAt([prefix, revised], 9).map((item) => item.flight_id))
      .toEqual(["SK218"]);
    expect(executionFlightLedgerAt([prefix, revised], 14).map((item) => item.flight_id))
      .toEqual(["SK218", "CX551"]);
  });
});

describe("shared visible simulation clock", () => {
  it("retains the gap between online batches instead of teleporting to the next milestone", () => {
    const batch=[event(2,6,"runway_released")];
    const schedule=buildPlaybackSchedule(batch,3);
    expect(schedule[0].delayMs).toBe(2100);
    const samples=playbackClockSamples(batch,3);
    expect(samples[0].minute).toBeGreaterThan(3);
    expect(samples.at(-1)!.minute).toBeLessThan(6);
    expect(samples.every((x,i)=>i===0 || x.minute >= samples[i-1].minute)).toBe(true);
  });
  it("holds the same minute through same-time narrative events without revealing future time", () => {
    const samples=playbackClockSamples([event(1,9,"ground_vehicle_failure"),event(2,9,"automatic_replan")],9);
    expect(samples.every(x=>x.minute===9)).toBe(true);
  });
  it("formats fractional time as readable simulated seconds", () => {
    expect(formatSimulationMinute(3.5)).toBe("T+03:30");
    expect(formatSimulationMinute(0)).toBe("T+00:00");
  });
});

it("keeps the observed runway closed until an explicit opening confirmation", () => {
  const closed = {id:"07R",status:"closed",reopen_minute:24} as Runway;
  expect(runwayStateAt(closed,24,true)).toBe("closed");
  expect(runwayStateAt(closed,30,true)).toBe("closed");
  expect(runwayTimelineLabel(closed,24,true)).toContain("AWAITING OPEN CONFIRMATION");
  expect(runwayStateAt({...closed,status:"open"},24,true)).toBe("open");
  // Future scheduling remains an explicitly projected view, not observed clearance.
  expect(runwayAllowsUseAt(closed,25)).toBe(true);
  expect(runwayPlanDisclosureAt(closed,[25],24,true).state).toBe("after-reopen");
});

it("refreshes the confirmed-runway label at the exact ETA without opening it speculatively",()=>{
  const runway={id:"07R",status:"closed",reopen_minute:23} as Runway;
  expect(runwayTimelineLabel(runway,22.999,true)).toContain("1m");
  expect(runwayTimelineLabel(runway,23,true)).toContain("AWAITING OPEN CONFIRMATION");
  expect(runwayStateAt(runway,23,true)).toBe("closed");
  expect(runwayTimelineLabel({...runway,status:"open"},23,true)).toBe("07R · OPEN");
});
