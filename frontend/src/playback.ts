import type { ExecutionEvent, Runway } from "./types";
import { STAND_EXIT_MS } from "./standHandover";

/** An empty transport batch is not proof that the whole execution has ended. */
export function emptyPlaybackBatchLabel(event: ExecutionEvent, language: "zh" | "en"): string {
  if (event.type === "recovered") return language === "zh" ? "本轮执行已完成，可查看验收" : "Execution complete; inspect the audit";
  if (event.type.includes("requires_approval")) return language === "zh" ? "请审核重规划候选，再批准继续" : "Review and approve the repair before continuing";
  return language === "zh" ? "本批事件已播完，等待下个真实里程碑" : "Batch complete; awaiting the next live milestone";
}

export interface ScheduledExecutionEvent {
  event: ExecutionEvent;
  delayMs: number;
  tickIndex?: number;
}

const FLIGHT_MOTION_MS = 5420;
const EVENT_PULSE_MS = 2300;
const SIMULATED_MINUTE_MS = 700;
const MIN_EVENT_GAP_MS = 450;
const NARRATIVE_EVENT_GAP_MS = 1400;
const FLIGHT_LAUNCH_GAP_MS = 2850;
const RECOVERY_SETTLE_MS = 800;

export type MissionChapter = 0 | 1 | 2 | 3 | 4;

export type RunwayTimelineState = "open" | "closed" | "reopened";

export type RunwayPlanDisclosure = {
  firstLandingMinute: number | null;
  state: "none" | "available-now" | "after-reopen" | "safety-violation";
};

export function runwayStateAt(runway: Runway, simMinute: number, requireConfirmation = false): RunwayTimelineState {
  if (runway.status !== "closed") return "open";
  if (requireConfirmation) return "closed";
  if (runway.reopen_minute !== null && simMinute >= runway.reopen_minute) return "reopened";
  return "closed";
}

export function runwayAllowsUseAt(runway: Runway, simMinute: number): boolean {
  return runwayStateAt(runway, simMinute) !== "closed";
}

export function runwayTimelineLabel(runway: Runway, simMinute: number, requireConfirmation = false): string {
  const state = runwayStateAt(runway, simMinute, requireConfirmation);
  if (state === "open") return `${runway.id} · OPEN`;
  if (state === "reopened") return `${runway.id} · OPEN · REOPENED T+${runway.reopen_minute}`;
  if (runway.reopen_minute === null) return `${runway.id} · CLOSED · NO ETA`;
  if (requireConfirmation && simMinute >= runway.reopen_minute) return `${runway.id} · CLOSED · AWAITING OPEN CONFIRMATION`;
  const remaining = Math.max(0, Math.ceil(runway.reopen_minute - simMinute));
  return `${runway.id} · CLOSED NOW · REOPEN T+${runway.reopen_minute} · ${remaining}m`;
}

export function runwayPlanDisclosureAt(
  runway: Runway,
  assignmentMinutes: number[],
  simMinute: number,
  requireConfirmation = false,
): RunwayPlanDisclosure {
  const firstLandingMinute = [...assignmentMinutes]
    .filter((minute) => minute >= simMinute)
    .sort((left, right) => left - right)[0] ?? null;
  if (firstLandingMinute === null) return { firstLandingMinute, state: "none" };
  if (!runwayAllowsUseAt(runway, firstLandingMinute)) {
    return { firstLandingMinute, state: "safety-violation" };
  }
  return {
    firstLandingMinute,
    state: runwayStateAt(runway, simMinute, requireConfirmation) === "closed" ? "after-reopen" : "available-now",
  };
}

/** Derive a chapter from the revealed prefix, never from a transport batch. */
export function missionChapterForEvent(
  revealed: ExecutionEvent[],
  current: ExecutionEvent,
): MissionChapter {
  const prefix = new Map<number, ExecutionEvent>();
  for (const event of [...revealed, current]) {
    if (event.session_id === current.session_id
      && event.sequence <= current.sequence
      && event.sim_minute <= current.sim_minute) {
      prefix.set(event.sequence, event);
    }
  }
  let chapter: MissionChapter = 0;
  for (const event of [...prefix.values()].sort(compareExecutionEvents)) {
    if (event.type === "recovered") chapter = 4;
    else if (event.type.includes("failure")) chapter = 2;
    else if (event.type.includes("replan")) chapter = 3;
    else if (event.type !== "plan_approved" && chapter === 0) chapter = 1;
  }
  return chapter;
}

export function missionChapterAt(events: ExecutionEvent[], index: number): MissionChapter {
  if (!events.length) return 0;
  const safeIndex = Math.min(Math.max(0, index), events.length - 1);
  return missionChapterForEvent(events.slice(0, safeIndex), events[safeIndex]);
}

function isNarrativeExecutionEvent(event: ExecutionEvent): boolean {
  return event.type.includes("failure")
    || event.type.includes("replan")
    || event.type === "trusted_state_reconciled";
}

export function isMissionBriefingEvent(event: ExecutionEvent): boolean {
  return event.type === "ground_vehicle_failure" || event.type.includes("replan");
}

export function compareExecutionEvents(left: ExecutionEvent, right: ExecutionEvent): number {
  if (left.sim_minute !== right.sim_minute) return left.sim_minute - right.sim_minute;
  return left.sequence - right.sequence;
}
export interface ExecutionFlightSnapshot {
  flight_id: string;
  runway: string;
  gate: string;
  landing_minute: number;
  plan_id: string | null;
  schedule_revision: string | null;
}

export function executionFlightLedger(events: ExecutionEvent[]): ExecutionFlightSnapshot[] {
  const byFlight = new Map<string, ExecutionFlightSnapshot>();
  [...events]
    .sort((left, right) => left.sequence - right.sequence || compareExecutionEvents(left, right))
    .filter((event) => event.type === "execution_tick")
    .forEach((event) => {
      const flightId = String(event.payload.flight_id ?? "");
      const runway = String(event.payload.runway ?? "");
      const gate = String(event.payload.gate ?? "");
      if (!flightId || !runway || !gate) return;
      byFlight.set(flightId, {
        flight_id: flightId,
        runway,
        gate,
        landing_minute: event.sim_minute,
        plan_id: event.payload.plan_id ? String(event.payload.plan_id) : null,
        schedule_revision: event.payload.schedule_revision
          ? String(event.payload.schedule_revision)
          : null,
      });
    });
  return [...byFlight.values()].sort((left, right) =>
    left.landing_minute - right.landing_minute
      || left.flight_id.localeCompare(right.flight_id)
  );
}

/**
 * Return only the aircraft actions that are already part of the visible world.
 * Future revised assignments remain in the event log for replay, but must not
 * leak into the map before their simulated execution time.
 */
export function executionFlightLedgerAt(
  events: ExecutionEvent[],
  simMinute: number,
): ExecutionFlightSnapshot[] {
  return executionFlightLedger(events.filter((event) => event.sim_minute <= simMinute));
}

export function buildPlaybackSchedule(events: ExecutionEvent[], startMinute?: number): ScheduledExecutionEvent[] {
  const ordered = [...events].sort(compareExecutionEvents);
  if (!ordered.length) return [];

  const firstMinute = Math.min(startMinute ?? ordered[0].sim_minute, ordered[0].sim_minute);
  // Give every connecting-journey leg a readable duration, on the same pausable clock.
  const minuteMs = ordered.some(({type}) => type.startsWith("connection_") || type.startsWith("departure_")) ? 2200 : SIMULATED_MINUTE_MS;
  let tickIndex = 0;
  let lastDelay = -MIN_EVENT_GAP_MS;
  let lastFlightDelay = -FLIGHT_LAUNCH_GAP_MS;
  return ordered.map((event, index) => {
    const relativeMinute = Math.max(0, event.sim_minute - firstMinute);
    const timelineDelay = relativeMinute * minuteMs;
    const runwayDelay = event.type === "execution_tick"
      ? lastFlightDelay + FLIGHT_LAUNCH_GAP_MS
      : 0;
    const settleDelay = event.type === "recovered"
      ? lastFlightDelay + FLIGHT_MOTION_MS + RECOVERY_SETTLE_MS
      : isNarrativeExecutionEvent(event) && lastFlightDelay >= 0
        ? lastFlightDelay + FLIGHT_MOTION_MS
      : 0;
    const isNarrative = isNarrativeExecutionEvent(event);
    const previousIsNarrative = index > 0 && isNarrativeExecutionEvent(ordered[index - 1]);
    const previousReleasedStand = index > 0
      && ["gate_released", "gate_recovered"].includes(ordered[index - 1].type);
    const eventGap = Math.max(
      isNarrative || previousIsNarrative ? NARRATIVE_EVENT_GAP_MS : MIN_EVENT_GAP_MS,
      previousReleasedStand ? STAND_EXIT_MS + 200 : 0,
    );
    const delayMs = Math.max(timelineDelay, runwayDelay, settleDelay, lastDelay + (index ? eventGap : 0));
    const scheduled: ScheduledExecutionEvent = {
      event,
      delayMs,
      tickIndex: event.type === "execution_tick" ? tickIndex : undefined,
    };
    if (event.type === "execution_tick") {
      tickIndex += 1;
      lastFlightDelay = delayMs;
    }
    lastDelay = delayMs;
    return scheduled;
  });
}

export function playbackDuration(events: ExecutionEvent[], startMinute?: number): number {
  const schedule = buildPlaybackSchedule(events, startMinute);
  if (!schedule.length) return 0;
  return Math.max(...schedule.map(({ event, delayMs }) =>
    delayMs + (event.type === "execution_tick"
      ? FLIGHT_MOTION_MS
      : event.type === "recovered"
        ? 800
        : EVENT_PULSE_MS)
  ));
}

/** Samples share the same timer queue as event disclosure, pause and speed changes. */
export function playbackClockSamples(events: ExecutionEvent[], startMinute: number) {
  const schedule = buildPlaybackSchedule(events, startMinute);
  const samples: Array<{ delayMs: number; minute: number }> = [];
  let previous = { delayMs: 0, minute: startMinute };
  for (const item of schedule) {
    const end = { delayMs: item.delayMs, minute: item.event.sim_minute };
    for (let delayMs = previous.delayMs + 80; delayMs < end.delayMs; delayMs += 80) {
      const fraction = (delayMs - previous.delayMs) / (end.delayMs - previous.delayMs);
      samples.push({ delayMs, minute: previous.minute + (end.minute - previous.minute) * fraction });
    }
    previous = end;
  }
  return samples;
}

export function formatSimulationMinute(minute: number): string {
  const seconds = Math.max(0, Math.floor(minute * 60 + 1e-7));
  return `T+${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}
