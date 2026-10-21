import { afterEach, describe, expect, it, vi } from "vitest";

import { advanceExecution, connectStream, streamReconnectDelay } from "./api";
import type { ExecutionEvent, SessionState } from "./types";

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];

  readonly url: string;
  readyState = FakeWebSocket.OPEN;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn(() => {
    this.readyState = 3;
  });

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }
}

const executionEvent = (sequence: number): ExecutionEvent => ({
  sequence,
  session_id: "session reconnect",
  sim_minute: 7,
  type: "clock_advanced",
  payload: {},
  created_at: "2026-09-05T00:00:00Z",
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  FakeWebSocket.instances = [];
});

describe("session event stream", () => {
  it("caps exponential reconnect backoff", () => {
    expect([0, 1, 2, 8].map(streamReconnectDelay)).toEqual([500, 1_000, 2_000, 8_000]);
  });

  it("retries an idempotent execution advance after a transport failure", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue({
        ok: true,
        json: async () => ({ revision: 3 }),
      });
    vi.stubGlobal("fetch", fetchMock);

    const resultPromise = advanceExecution("session_test", 4, 2, "ui-session_test-r2-t4");
    await vi.advanceTimersByTimeAsync(350);

    await expect(resultPromise).resolves.toMatchObject({ revision: 3 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][1]?.body).toContain("ui-session_test-r2-t4");
    expect(fetchMock.mock.calls[1][1]?.body).toBe(fetchMock.mock.calls[0][1]?.body);
  });

  it("reconnects after the last received sequence and stops cleanly", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("location", { protocol: "http:", host: "demo.local" });
    vi.stubGlobal("window", {
      setTimeout: globalThis.setTimeout,
      clearTimeout: globalThis.clearTimeout,
      setInterval: globalThis.setInterval,
      clearInterval: globalThis.clearInterval,
    });

    const states: SessionState[] = [];
    const events: ExecutionEvent[] = [];
    const stop = connectStream("session reconnect", (state) => states.push(state), (event) => events.push(event));

    expect(FakeWebSocket.instances[0].url).toBe(
      "ws://demo.local/api/v2/sessions/session%20reconnect/stream",
    );
    FakeWebSocket.instances[0].onmessage?.({
      data: JSON.stringify({ type: "execution.event", payload: executionEvent(7) }),
    });
    FakeWebSocket.instances[0].onclose?.();

    await vi.advanceTimersByTimeAsync(500);
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(FakeWebSocket.instances[1].url).toBe(
      "ws://demo.local/api/v2/sessions/session%20reconnect/stream?after_sequence=7",
    );

    FakeWebSocket.instances[1].onmessage?.({
      data: JSON.stringify({
        type: "replay.events",
        payload: [executionEvent(8), executionEvent(9)],
      }),
    });
    expect(events.map((event) => event.sequence)).toEqual([7, 8, 9]);

    stop();
    FakeWebSocket.instances[1].onclose?.();
    await vi.advanceTimersByTimeAsync(8_000);
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(states).toEqual([]);
  });
});
