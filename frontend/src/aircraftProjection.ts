import type { CommittedOperation, ExecutionEvent } from "./types";

export type AircraftStage = "waiting" | "runway" | "taxiing" | "at_gate" | "released";
export type AircraftMilestones = Pick<CommittedOperation,
  "flight_id" | "runway" | "gate" | "landing_minute" | "runway_release_minute"
  | "gate_start_minute" | "gate_release_minute"> & {
  observed_minute?: number;
  observed_stage?: CommittedOperation["stage"];
  observed_runway_released?: boolean;
};

export function milestonesFromEvent(event: ExecutionEvent): AircraftMilestones | undefined {
  if (event.type !== "execution_tick") return;
  const p = event.payload;
  const times = [event.sim_minute, p.runway_release_minute, p.gate_start_minute, p.gate_release_minute];
  if (!times.every((value) => typeof value === "number" && Number.isFinite(value))) return;
  if (![p.flight_id, p.runway, p.gate].every((value) => typeof value === "string" && value.length)) return;
  const [landing, runwayEnd, gateStart, gateEnd] = times as number[];
  if (runwayEnd < landing || gateStart < landing || gateEnd < gateStart) return;
  return { flight_id: String(p.flight_id), runway: String(p.runway), gate: String(p.gate),
    landing_minute: landing, runway_release_minute: runwayEnd,
    gate_start_minute: gateStart, gate_release_minute: gateEnd };
}

/** A saved terminal stage is deliberately ignored when reconstructing earlier time. */
export function aircraftStageAt(operation: AircraftMilestones, minute: number): AircraftStage {
  if (minute < operation.landing_minute) return "waiting";
  const observed = operation.observed_minute !== undefined && minute <= operation.observed_minute;
  if (minute >= operation.gate_release_minute) {
    if (observed && operation.observed_stage && operation.observed_stage !== "released") return operation.observed_stage;
    return "released";
  }
  if (minute >= operation.gate_start_minute && !(observed && operation.observed_stage === "taxiing")) return "at_gate";
  if (minute < operation.runway_release_minute || (observed && operation.observed_runway_released === false)) return "runway";
  return "taxiing";
}

export function executionMilestones(
  operations: CommittedOperation[], events: ExecutionEvent[], minute: number, observedMinute?: number,
): Map<string, AircraftMilestones> {
  const result = new Map<string, AircraftMilestones>();
  for (const operation of operations) {
    if (operation.landing_minute <= minute) result.set(operation.flight_id, { ...operation,
      observed_minute: observedMinute, observed_stage: operation.stage,
      observed_runway_released: operation.runway_released });
  }
  for (const event of [...events].sort((a, b) => a.sequence - b.sequence)) {
    const operation = milestonesFromEvent(event);
    if (operation && operation.landing_minute <= minute && !result.has(operation.flight_id)) result.set(operation.flight_id, operation);
  }
  return result;
}

export type Point = { x: number; y: number };

/** Schematic taxi clearance in CSS pixels, independent of viewport scale.
 * Keep the shared lane outside every stand, including the remote stand.
 */
export function apronSpineX(preferredX: number, stands: readonly Point[]): number {
  return Math.max(preferredX, ...stands.map(stand => stand.x + 48));
}

export function pointOnPath(points: Point[], fraction: number): Point {
  const lengths = points.slice(1).map((p, i) => Math.hypot(p.x - points[i].x, p.y - points[i].y));
  let distance = Math.max(0, Math.min(1, fraction)) * lengths.reduce((a, b) => a + b, 0);
  for (let i = 0; i < lengths.length; i++) {
    if (distance <= lengths[i] && lengths[i] > 0) {
      const ratio = distance / lengths[i];
      return { x: points[i].x + (points[i + 1].x - points[i].x) * ratio,
        y: points[i].y + (points[i + 1].y - points[i].y) * ratio };
    }
    distance -= lengths[i];
  }
  return points[points.length - 1];
}

/** Route geometry is illustrative, but stage boundaries are the committed ledger times. */
export function aircraftPositionAt(
  operation: AircraftMilestones, minute: number,
  runway: { x: number; y: number; width: number; height: number },
  gate: Point, spineX: number, apronLaneY = gate.y - 52, holdBeforeEntry = false,
): Point & {waitingForStand?: boolean} {
  const stage = aircraftStageAt(operation, minute);
  const centerY = runway.y + runway.height / 2;
  const entry = { x: runway.x + runway.width * .96, y: centerY };
  const exit = { x: runway.x, y: centerY };
  const runwayEnd = Math.min(operation.runway_release_minute, operation.gate_start_minute);
  if (stage === "waiting") return entry;
  if (stage === "runway") return pointOnPath([entry, exit],
    (minute - operation.landing_minute) / Math.max(.001, runwayEnd - operation.landing_minute));
  // Parallel apron lane, then a dedicated perpendicular stand lead-in.
  // Never run horizontally through the row of already parked aircraft.
  if (stage === "taxiing") {
    const point = pointOnPath([exit, { x: spineX, y: centerY },
      { x: spineX, y: apronLaneY }, { x: gate.x, y: apronLaneY }, gate],
      (minute - runwayEnd) / Math.max(.001, operation.gate_start_minute - runwayEnd));
    // Reserve the final lead-in for the confirmed entry animation. This also
    // keeps back-to-back users clear while the old airframe moves out.
    if (holdBeforeEntry && Math.hypot(point.x - gate.x, point.y - gate.y) < 38) {
      return {x: gate.x, y: gate.y + (apronLaneY >= gate.y ? 38 : -38), waitingForStand: true};
    }
    return point;
  }
  return gate;
}

export function aircraftStageLabel(stage: AircraftStage, gate: string, runway: string, language: "zh" | "en"): string {
  const labels = language === "zh"
    ? { waiting: "等待降落", runway: "已落地，正在减速", taxiing: "沿地面通道前往", at_gate: "停机下客、准备下一班", released: "下客与地面服务完成" }
    : { waiting: "HOLD", runway: "LANDING / RUNWAY", taxiing: "TAXI TO", at_gate: "TURNAROUND", released: "SCOPE COMPLETE · NOT TAKEOFF" };
  return labels[stage] + (stage === "runway" ? " " + runway : ["taxiing", "at_gate"].includes(stage) ? " " + gate : "");
}
