import { describe, expect, it } from "vitest";
import {
  canAdvanceExecution,
  executionMadeProgress,
  executionNeedsApproval,
  executionRequestId,
  mergeExecutionEvents,
  nextExecutionMinute,
  shouldRequestAdvance,
} from "./executionDriver";
import type { ExecutionEvent, SessionState } from "./types";

const event = (sequence: number, minute: number): ExecutionEvent => ({
  sequence,
  session_id: "session_test",
  sim_minute: minute,
  type: "clock_advanced",
  payload: {},
  created_at: "2026-09-05T00:00:00Z",
});

function runningSession(): SessionState {
  return {
    id: "session_test",
    phase: "monitoring",
    scenario: {
      current_minute: 7,
      runways: [{ id: "07R", status: "closed", reopen_minute: 24 }],
      gates: [{ id: "A2", status: "available", available_minute: 0 }],
    },
    execution: {
      version: 1,
      revision: 3,
      active_plan_id: "plan_2",
      minute: 7,
      status: "running",
      scope_flight_ids: ["SK218", "CX551"],
      pending_assignments: [{ flight_id: "CX551", landing_minute: 10 }],
      committed_operations: [{
        flight_id: "SK218",
        plan_id: "plan_1",
        revision: 1,
        landing_minute: 4,
        runway: "07L",
        runway_release_minute: 8,
        gate: "A3",
        gate_start_minute: 9,
        gate_release_minute: 31,
        stage: "taxiing",
        runway_released: false,
      }],
      scheduled_events: [{
        id: "evt_tug",
        type: "ground_vehicle_failure",
        minute: 9,
      }],
      processed_event_ids: [],
      request_receipts: {},
      last_stopped_reason: null,
    },
  } as unknown as SessionState;
}

describe("online execution driver", () => {
  it("selects the nearest uncommitted operational milestone", () => {
    const session = runningSession();
    expect(nextExecutionMinute(session)).toBe(8);
    session.execution!.committed_operations[0].runway_released = true;
    expect(nextExecutionMinute(session)).toBe(9);
  });

  it("blocks advance requests while paused or while one is in flight", () => {
    const session = runningSession();
    expect(shouldRequestAdvance(session, { paused: true, inFlight: false })).toBe(false);
    expect(shouldRequestAdvance(session, { paused: false, inFlight: true })).toBe(false);
    expect(shouldRequestAdvance(session, { paused: false, inFlight: false })).toBe(true);
  });

  it("does not advance while a safety gate waits for approval", () => {
    const session = runningSession();
    session.phase = "awaiting_approval";
    session.execution!.status = "blocked";
    expect(canAdvanceExecution(session)).toBe(false);
    expect(executionNeedsApproval(session)).toBe(true);
    expect(nextExecutionMinute(session)).toBeNull();
  });

  it("deduplicates websocket, replay and response copies by sequence", () => {
    expect(mergeExecutionEvents(
      [event(2, 7), event(1, 4)],
      [event(2, 7), event(3, 9)],
    ).map((item) => item.sequence)).toEqual([1, 2, 3]);
  });

  it("generates a stable idempotency key for the same cursor step", () => {
    expect(executionRequestId("s1", 4, 12)).toBe("ui-s1-r4-t12");
    expect(executionRequestId("s1", 4, 12)).toBe(
      executionRequestId("s1", 4, 12),
    );
  });
});

it("uses the committed gate release instead of a stale same-minute availability hint", () => {
  const session = runningSession();
  session.scenario.current_minute = 28;
  const cursor = session.execution!;
  cursor.pending_assignments = [];
  cursor.scheduled_events = [];
  cursor.committed_operations[0] = {...cursor.committed_operations[0],
    stage: "at_gate", gate: "A2", runway_released: true, gate_release_minute: 30};
  session.scenario.gates[0] = {...session.scenario.gates[0], status: "occupied",
    occupied_by: "SK218", available_minute: 28};
  expect(nextExecutionMinute(session)).toBe(30);
});

it("does not release a stand retained for the connecting departure", () => {
  const session = runningSession(), cursor = session.execution!;
  session.scenario.current_minute = 31;
  cursor.pending_assignments = []; cursor.scheduled_events = [];
  cursor.committed_operations[0] = {...cursor.committed_operations[0],
    stage: "released", runway_released: true};
  session.scenario.gates[0] = {...session.scenario.gates[0], status: "occupied", available_minute: 31};
  cursor.connection = {stage: "boarding", gate: "A2", next_minute: 37} as NonNullable<typeof cursor.connection>;
  expect(nextExecutionMinute(session)).toBe(37);
});

it("stops an empty same-minute reply even when the revision keeps incrementing", () => {
  const before = runningSession(), after = structuredClone(before);
  after.execution!.revision += 1;
  const reply = {session: after, execution_events: [], next_event_minute: 7,
    stopped_reason: null, revision: after.execution!.revision};
  expect(executionMadeProgress(before, reply)).toBe(false);
  expect(executionMadeProgress(before, {...reply, execution_events: [event(8,7)]})).toBe(true);
  after.scenario.current_minute = 8;
  expect(executionMadeProgress(before, reply)).toBe(true);
});
