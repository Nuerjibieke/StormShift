import { aircraftStageAt, executionMilestones } from "./aircraftProjection";
import type { AirportMapState } from "./historicalWorld";
import type { ExecutionEvent } from "./types";

export type PassengerGroup = {
  flightId: string; count: number; gate: string;
  stage: "onboard" | "terminal" | "diverted" | "outside_scope";
  gateMinute?: number;
};
/** A schematic of flight-linked cohorts, never a claim of individual passenger arrival.
 * Only revealed operations can move a cohort; proposed plans cannot move passengers.
 */
export function passengerGroups(world: AirportMapState, events: ExecutionEvent[], minute: number): PassengerGroup[] {
  const operations = executionMilestones(world.execution?.committed_operations ?? [],
    events.filter(e => e.session_id === world.id && e.sim_minute <= minute), minute, world.scenario.current_minute);
  const initialGates = new Map(world.scenario.gates.filter(g => g.occupied_by).map(g => [g.occupied_by!, g.id]));
  return world.scenario.flights.filter(f => f.connections > 0).map(f => {
    const operation = operations.get(f.id);
    const stage = operation ? aircraftStageAt(operation, minute) : undefined;
    const outsideScope = Boolean(world.execution && !world.execution.scope_flight_ids.includes(f.id) && !operation && !initialGates.has(f.id));
    const atTerminal = stage === "at_gate" || stage === "released" || (!operation && initialGates.has(f.id));
    return { flightId: f.id, count: f.connections, gate: operation?.gate ?? initialGates.get(f.id) ?? f.planned_gate,
      stage: f.state === "diverted" ? "diverted" : outsideScope ? "outside_scope" : atTerminal ? "terminal" : "onboard",
      gateMinute: operation?.gate_start_minute };
  });
}
