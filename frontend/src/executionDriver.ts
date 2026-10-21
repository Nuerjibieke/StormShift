import type { ExecutionEvent, SessionState } from "./types";

export interface AdvanceExecutionResponse {
  session: SessionState;
  execution_events: ExecutionEvent[];
  next_event_minute: number | null;
  stopped_reason: string | null;
  revision: number;
}

export function mergeExecutionEvents(
  current: ExecutionEvent[],
  incoming: ExecutionEvent[],
): ExecutionEvent[] {
  const bySequence = new Map<number, ExecutionEvent>();
  current.forEach((event) => bySequence.set(event.sequence, event));
  incoming.forEach((event) => bySequence.set(event.sequence, event));
  return [...bySequence.values()].sort((left, right) => left.sequence - right.sequence);
}

export function canAdvanceExecution(session: SessionState): boolean {
  const cursor = session.execution;
  return Boolean(
    cursor
      && ["ready", "running"].includes(cursor.status)
      && !["recovered", "infeasible", "awaiting_approval"].includes(session.phase),
  );
}

export function shouldRequestAdvance(
  session: SessionState,
  controls: { paused: boolean; inFlight: boolean },
): boolean {
  return !controls.paused
    && !controls.inFlight
    && canAdvanceExecution(session);
}

/** Revisions alone are not progress: never loop on an empty same-minute reply. */
export function executionMadeProgress(
  before: SessionState,
  response: AdvanceExecutionResponse,
): boolean {
  return response.session.scenario.current_minute > before.scenario.current_minute
    || response.execution_events.length > 0
    || !canAdvanceExecution(response.session);
}

export function executionNeedsApproval(session: SessionState): boolean {
  return session.phase === "awaiting_approval"
    || session.execution?.status === "blocked";
}

export function executionRequestId(
  sessionId: string,
  revision: number,
  targetMinute: number,
): string {
  return `ui-${sessionId}-r${revision}-t${targetMinute}`;
}

export function nextExecutionMinute(session: SessionState): number | null {
  const cursor = session.execution;
  if (!cursor || !canAdvanceExecution(session)) return null;
  const current = session.scenario.current_minute;
  const candidates: number[] = [];
  cursor.pending_assignments.forEach((item) => {
    if (item.landing_minute >= current) candidates.push(item.landing_minute);
  });
  const processed = new Set(cursor.processed_event_ids);
  cursor.scheduled_events.forEach((event) => {
    if (!processed.has(event.id) && event.minute >= current) candidates.push(event.minute);
  });
  session.scenario.runways.forEach((runway) => {
    if (
      runway.status === "closed"
      && runway.reopen_minute !== null
      && runway.reopen_minute >= current
    ) candidates.push(runway.reopen_minute);
  });
  const managedGates = new Set(cursor.committed_operations
    .filter(operation => operation.stage === "at_gate").map(operation => operation.gate));
  const connection = cursor.connection;
  if (connection?.gate && ["turnaround", "boarding", "pushback"].includes(connection.stage)) {
    managedGates.add(connection.gate);
  }
  session.scenario.gates.forEach((gate) => {
    if (
      !managedGates.has(gate.id)
      && ["blocked", "occupied"].includes(gate.status)
      && gate.available_minute >= current
    ) candidates.push(gate.available_minute);
  });
  cursor.committed_operations.forEach((operation) => {
    if (!operation.runway_released && operation.runway_release_minute >= current) {
      candidates.push(operation.runway_release_minute);
    }
    if (operation.stage === "taxiing" && operation.gate_start_minute >= current) {
      candidates.push(operation.gate_start_minute);
    }
    if (operation.stage === "at_gate" && operation.gate_release_minute >= current) {
      candidates.push(operation.gate_release_minute);
    }
  });
  if (cursor.connection?.next_minute != null && cursor.connection.next_minute >= current) candidates.push(cursor.connection.next_minute);
  return candidates.length ? Math.min(...candidates) : null;
}

/** The approval gate can also be the first approval; it is not proof of replanning. */
export function approvalActionLabel(session: Pick<SessionState, "execution">, language: "zh" | "en"): string {
  const repairing = Boolean(session.execution);
  return language === "zh"
    ? repairing ? "批准新方案并继续" : "批准并开始执行"
    : repairing ? "Approve repair and continue" : "Approve and start execution";
}
