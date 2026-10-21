import type { ExecutionCursor, ExecutionEvent, Plan, SessionState } from "./types";

export type AirportMapState = Pick<SessionState, "id" | "scenario" | "phase" | "selected_plan_id"> & {
  plans: Array<Pick<Plan, "id" | "assignments">>;
  execution: Pick<ExecutionCursor, "scope_flight_ids" | "committed_operations" | "connection"> | null;
  decision_lab: Pick<SessionState["decision_lab"], "recourse">;
};

type Snapshot = { version: string; status: string; session_id: string; minute: number; world?: AirportMapState };

export function historicalWorld(event: ExecutionEvent): AirportMapState | undefined {
  const snapshot = event.payload.world_snapshot as Snapshot | undefined;
  const world = snapshot?.world;
  if (snapshot?.version !== "airport-world-v1" || snapshot.status !== "captured"
    || snapshot.session_id !== event.session_id || snapshot.minute !== event.sim_minute
    || !world || world.id !== event.session_id || world.scenario?.current_minute !== event.sim_minute
    || !Array.isArray(world.scenario.runways) || !Array.isArray(world.scenario.gates)
    || !Array.isArray(world.scenario.flights) || !world.scenario.weather
    || !Array.isArray(world.plans) || !world.decision_lab
    || (world.execution !== null && (!world.execution
      || !Array.isArray(world.execution.committed_operations) || !Array.isArray(world.execution.scope_flight_ids)))) return;
  return world;
}

/** Caller supplies the revealed prefix. Same-minute future events must not be included. */
export function historicalWorldAt(sessionId: string, revealed: ExecutionEvent[], minute: number) {
  const latest = revealed.filter(event => event.session_id === sessionId && event.sim_minute <= minute)
    .sort((a,b) => b.sequence - a.sequence)[0];
  return latest ? historicalWorld(latest) : undefined;
}

/** Live animation and replay must project the same revealed event prefix.
 * Before the first live event the existing scene is safe; once disclosure has
 * started, a missing snapshot is not permission to expose the final session.
 */
export function presentedWorld(
  session: SessionState,
  revealed: ExecutionEvent[],
  minute: number,
  historical: boolean,
  playing: boolean,
): AirportMapState | undefined {
  if (!historical && !playing) return session;
  const hasVisibleEvent = revealed.some(event =>
    event.session_id === session.id && event.sim_minute <= minute);
  if (!historical && !hasVisibleEvent) return session;
  return historicalWorldAt(session.id, revealed, minute);
}

export function hasHistoricalWorlds(events: ExecutionEvent[]): boolean {
  return events.length > 0 && events.every(event => Boolean(historicalWorld(event)));
}
