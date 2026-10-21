import type { ExecutionEvent } from "./types";
import { hasHistoricalWorlds, historicalWorld } from "./historicalWorld";

export type ReplayFocus = "latest" | "disruption";
export type ReplayLandmarkKind = "approved" | "beforeFault" | "fault" | "decision" | "recovered";
export interface ReplayLandmark { kind: ReplayLandmarkKind; sequence: number }

/** Marks identify recorded events, not imagined milestones or successful repairs. */
export function replayLandmarks(events: ExecutionEvent[]): ReplayLandmark[] {
  const ordered = [...events].sort((a, b) => a.sequence - b.sequence);
  const marks: ReplayLandmark[] = [];
  const approved = ordered.find(e => e.type === "plan_approved");
  if (approved) marks.push({kind: "approved", sequence: approved.sequence});
  const faultIndex = ordered.findIndex(e => e.type === "ground_vehicle_failure");
  if (faultIndex >= 0) {
    if (faultIndex > 0) marks.push({kind: "beforeFault", sequence: ordered[faultIndex - 1].sequence});
    marks.push({kind: "fault", sequence: ordered[faultIndex].sequence});
    const decision = ordered.slice(faultIndex + 1).find(e => e.type.includes("replan"));
    if (decision) marks.push({kind: "decision", sequence: decision.sequence});
  }
  const recovered = [...ordered].reverse().find(e => e.type === "recovered");
  if (recovered) marks.push({kind: "recovered", sequence: recovered.sequence});
  return marks;
}

export function replayInitialCursor(events: ExecutionEvent[], focus: ReplayFocus): number {
  const fault = focus === "disruption" ? replayLandmarks(events).find(mark => mark.kind === "fault") : undefined;
  return fault ? events.findIndex(e => e.sequence === fault.sequence) : Math.max(0, events.length - 1);
}

/** Select an exact sequence prefix: a later event at the same minute is still future.
 * Invalid/legacy snapshots never fall back to current live state. No input mutation.
 */
export function historicalSelection(events: ExecutionEvent[], sessionId: string, sequence: number) {
  if (!sessionId || events.some(e => e.session_id !== sessionId)) return;
  const ordered = [...events].sort((a, b) => a.sequence - b.sequence);
  if (ordered.some((e, i) => !Number.isSafeInteger(e.sequence) || e.sequence < 1
    || i > 0 && e.sequence === ordered[i - 1].sequence)) return;
  const index = ordered.findIndex(e => e.sequence === sequence);
  if (index < 0) return;
  const prefix = ordered.slice(0, index + 1);
  if (prefix.some((e, i) => !Number.isFinite(e.sim_minute) || e.sim_minute < 0
    || i > 0 && e.sim_minute < prefix[i - 1].sim_minute) || !hasHistoricalWorlds(prefix)) return;
  const event = prefix[prefix.length - 1], world = historicalWorld(event)!;
  return {prefix, event, world};
}
