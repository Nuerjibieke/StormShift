import {pointOnPath, type Point} from "./aircraftProjection";

// Display time only: these never change a committed landing or stand reservation.
export const STAND_EXIT_MS = 1800;
export const STAND_ENTRY_MS = 900;

export function standExitFrame(
  progress: number, gate: Point, inboundLaneY: number, stands: readonly Point[], spineX: number,
) {
  // Leave on the outside of the stand rows; the arriving aircraft waits on the
  // inside lead-in. This prevents the two airframes moving through each other.
  const top = Math.min(gate.y, ...stands.map(p => p.y));
  const bottom = Math.max(gate.y, ...stands.map(p => p.y));
  const upper = gate.y <= (top+bottom)/2;
  const laneY = (upper ? top : bottom) + (upper ? -56 : 56);
  // The remote stand receives arrivals from below, so leave to the right first
  // instead of driving through an aircraft waiting below that stand.
  const remoteLeadIn = !upper && inboundLaneY > gate.y;
  const points = remoteLeadIn
    ? [gate, {x:spineX+40,y:gate.y}, {x:spineX+40,y:laneY}]
    : upper ? [gate, {x:gate.x,y:laneY}, {x:spineX+40,y:laneY}]
    // The lower row's diagonal lead-out also clears the offset remote stand.
    : [gate, {x:gate.x-10,y:gate.y+32}, {x:gate.x-10,y:laneY}, {x:spineX+40,y:laneY}];
  const at = pointOnPath(points,progress);
  const before = pointOnPath(points,Math.max(0,progress-.001));
  const ahead = pointOnPath(points,Math.min(1,progress+.001));
  return {...at, angle:Math.atan2(ahead.y-before.y,ahead.x-before.x)*180/Math.PI,
    alpha:Math.min(1,Math.max(0,(1-progress)/.15))};
}

export function standEntryFrame(progress: number, gate: Point, fromOffset: Point) {
  return pointOnPath([{x:gate.x+fromOffset.x,y:gate.y+fromOffset.y},gate],progress);
}
