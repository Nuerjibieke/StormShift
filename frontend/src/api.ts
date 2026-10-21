import type { AirportEvent, DataMode, ExecutionEvent, FlightContrastiveExplanation, Mode, SessionState } from "./types";
import type { AdvanceExecutionResponse } from "./executionDriver";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(String(payload.detail ?? "请求失败"), response.status, payload.detail);
  }
  return response.json() as Promise<T>;
}

async function requestWithNetworkRetry<T>(
  path: string,
  init: RequestInit,
  retries = 2,
): Promise<T> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await request<T>(path, init);
    } catch (reason) {
      if (!(reason instanceof TypeError) || attempt >= retries) throw reason;
      await new Promise<void>((resolve) => {
        globalThis.setTimeout(resolve, 350 * (attempt + 1));
      });
    }
  }
}

export async function createSession(input: {
  mode: Mode;
  scenario_id: string;
  data_mode: DataMode;
  seed?: number;
  sandbox_flights?: number;
  imported_scenario?: unknown;
}): Promise<SessionState> {
  return request("/api/v2/sessions", {
    method: "POST",
    body: JSON.stringify({ seed: 2026, sandbox_flights: 12, ...input }),
  });
}

export async function listSessions(limit = 4): Promise<SessionState[]> {
  return request(`/api/v2/sessions?limit=${limit}`);
}

export async function getSession(id: string): Promise<SessionState> {
  return request(`/api/v2/sessions/${id}`);
}

export async function createBranchPreview(
  session: SessionState,
  planIds: string[],
  feedback = false,
  runwayDelay?: import("./branchPreview").RunwayDelayRequest,
): Promise<import("./branchPreview").BranchPreviewReport> {
  if (feedback && runwayDelay) throw new Error("Feedback repair and runway-delay hypotheses cannot run together.");
  return request(`/api/v2/sessions/${session.id}/previews`, {
    method: "POST", body: JSON.stringify({
      plan_ids: planIds, expected_sequence: session.sequence, expected_updated_at: session.updated_at,
      ...(feedback ? {feedback: true} : {}),
      ...(runwayDelay ? {runway_delay: runwayDelay} : {}),
    }),
  });
}

export async function getBranchPreview(id: string, previewId: string): Promise<import("./branchPreview").BranchPreviewReport> {
  return request(`/api/v2/sessions/${id}/previews/${previewId}`);
}

export async function cloneSession(id: string): Promise<SessionState> {
  return request(`/api/v2/sessions/${id}/clone`, { method: "POST" });
}

export async function submitIntent(id: string, text: string): Promise<SessionState> {
  return request(`/api/v2/sessions/${id}/intent`, {
    method: "POST",
    body: JSON.stringify({ text, source: "text" }),
  });
}

export async function clarifyIntent(
  id: string,
  expectedVersion: number,
  issueIndex: number,
  text: string,
): Promise<SessionState> {
  return request("/api/v2/sessions/" + encodeURIComponent(id) + "/intent/clarify", {
    method: "POST",
    body: JSON.stringify({ expected_version: expectedVersion, issue_index: issueIndex, text }),
  });
}

export async function confirmIntent(id: string, approved: boolean): Promise<SessionState> {
  return request(`/api/v2/sessions/${id}/intent/confirm`, {
    method: "POST",
    body: JSON.stringify({ approved }),
  });
}

export async function acquireObservation(id: string, queryId: string, sourcePlanId?: string): Promise<SessionState> {
  return request(`/api/v2/sessions/${id}/observations`, {
    method: "POST",
    body: JSON.stringify({ query_id: queryId, source_plan_id: sourcePlanId }),
  });
}

export async function submitPreference(
  id: string,
  leftPlanId: string,
  rightPlanId: string,
  preferredPlanId: string,
): Promise<SessionState> {
  return request(`/api/v2/sessions/${id}/preferences`, {
    method: "POST",
    body: JSON.stringify({
      left_plan_id: leftPlanId,
      right_plan_id: rightPlanId,
      preferred_plan_id: preferredPlanId,
    }),
  });
}

export async function resetPreferenceMemory(): Promise<{
  status: string;
  revoked: number;
  hard_safety_rules_changed: number;
  message: string;
}> {
  return request("/api/v2/preferences", {
    method: "DELETE",
  });
}

export async function generatePlans(id: string, deep = false, replan = false): Promise<SessionState> {
  return request(`/api/v2/sessions/${id}/plans`, {
    method: "POST",
    body: JSON.stringify({
      profiles: ["balanced", "passenger", "resilient", "green"],
      deep,
      replan,
    }),
  });
}

export async function updateSandbox(
  id: string,
  update: {
    weather_severity?: number;
    runway_closure_min?: number;
    passenger_multiplier?: number;
    fuel_margin_delta?: number;
    priority_flight_id?: string;
    weights?: Record<string, number>;
  },
): Promise<SessionState> {
  return request(`/api/v2/sessions/${id}/sandbox`, {
    method: "PATCH",
    body: JSON.stringify(update),
  });
}

export async function getFlightExplanation(
  sessionId: string,
  planId: string,
  flightId: string,
): Promise<FlightContrastiveExplanation> {
  return request(`/api/v2/sessions/${encodeURIComponent(sessionId)}/plans/${encodeURIComponent(planId)}/flights/${encodeURIComponent(flightId)}/explanation`);
}

export async function approvePlan(id: string, planId: string): Promise<{
  session: SessionState;
  execution_events: ExecutionEvent[];
}> {
  return request(`/api/v2/sessions/${id}/plans/${planId}/approve`, {
    method: "POST",
  });
}

export async function advanceExecution(
  id: string,
  targetMinute: number,
  expectedRevision: number,
  requestId: string,
): Promise<AdvanceExecutionResponse> {
  return requestWithNetworkRetry(`/api/v2/sessions/${id}/execution/advance`, {
    method: "POST",
    body: JSON.stringify({
      target_minute: targetMinute,
      expected_revision: expectedRevision,
      request_id: requestId,
    }),
  });
}

export async function injectEvent(
  id: string,
  event: Partial<AirportEvent>,
  options?: { expectedRevision?: number; requestId?: string },
): Promise<SessionState> {
  return request(`/api/v2/sessions/${id}/events`, {
    method: "POST",
    body: JSON.stringify({
      event,
      expected_revision: options?.expectedRevision,
      request_id: options?.requestId,
    }),
  });
}

export async function getReplay(id: string): Promise<ExecutionEvent[]> {
  const result = await request<{ events: ExecutionEvent[] }>(`/api/v2/sessions/${id}/replay`);
  return result.events;
}

export async function transcribe(audio: Blob): Promise<{ text: string }> {
  const form = new FormData();
  form.append("file", audio, "command.webm");
  const response = await fetch("/api/v2/audio/transcribe", { method: "POST", body: form });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: "语音不可用" }));
    throw new Error(payload.detail);
  }
  return response.json();
}

export function streamReconnectDelay(attempt: number): number {
  return Math.min(500 * (2 ** Math.max(0, attempt)), 8_000);
}

export function connectStream(
  id: string,
  onState: (state: SessionState) => void,
  onEvent: (event: ExecutionEvent) => void,
): () => void {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const endpoint = `${protocol}://${location.host}/api/v2/sessions/${encodeURIComponent(id)}/stream`;
  let socket: WebSocket | null = null;
  let reconnectTimer: number | null = null;
  let reconnectAttempt = 0;
  let lastSequence = 0;
  let disposed = false;

  const remember = (event: ExecutionEvent) => {
    lastSequence = Math.max(lastSequence, event.sequence);
    onEvent(event);
  };

  const connect = () => {
    if (disposed) return;
    const query = lastSequence > 0 ? `?after_sequence=${lastSequence}` : "";
    const nextSocket = new WebSocket(`${endpoint}${query}`);
    socket = nextSocket;
    nextSocket.onopen = () => {
      reconnectAttempt = 0;
    };
    nextSocket.onmessage = ({ data }) => {
      const message = JSON.parse(data);
      if (message.type === "session.state") onState(message.payload);
      if (message.type === "execution.event") remember(message.payload as ExecutionEvent);
      if (message.type === "replay.events") {
        (message.payload as ExecutionEvent[]).forEach(remember);
      }
    };
    nextSocket.onerror = () => {
      nextSocket.close();
    };
    nextSocket.onclose = () => {
      if (disposed || socket !== nextSocket) return;
      const delay = streamReconnectDelay(reconnectAttempt++);
      reconnectTimer = window.setTimeout(connect, delay);
    };
  };

  connect();
  const keepAlive = window.setInterval(() => {
    if (socket?.readyState === WebSocket.OPEN) socket.send("ping");
  }, 20_000);

  return () => {
    disposed = true;
    window.clearInterval(keepAlive);
    if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
    if (socket) {
      socket.onclose = null;
      socket.close();
    }
  };
}
