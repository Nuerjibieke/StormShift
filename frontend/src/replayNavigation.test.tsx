import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { historicalSelection, replayInitialCursor, replayLandmarks } from "./replayNavigation";
import { historicalWorldAt, type AirportMapState } from "./historicalWorld";
import { ReplayPanel } from "./ReplayPanel";
import { copy } from "./copy";
import type { ExecutionEvent } from "./types";

function event(sequence: number, minute: number, type = "clock_advanced", committed = 0): ExecutionEvent {
  const world = {
    id: "s", phase: type === "recovered" ? "recovered" : "monitoring",
    selected_plan_id: null, plans: [], decision_lab: {},
    execution: {scope_flight_ids: ["F1", "F2"], committed_operations: Array.from({length: committed}, (_, i) => ({flight_id: `F${i + 1}`}))},
    scenario: {current_minute: minute, flights: [], gates: [],
      runways: [{id: "07R", status: type === "recovered" ? "open" : "closed"}], weather: {severity: 4}},
  } as unknown as AirportMapState;
  return {session_id: "s", sequence, sim_minute: minute, type, created_at: "",
    payload: {world_snapshot: {version: "airport-world-v1", status: "captured", session_id: "s", minute, world}}};
}
const history = () => [
  event(1, 0, "session_created"), event(2, 0, "plan_approved"),
  event(3, 9, "clock_advanced", 2), event(4, 9, "ground_vehicle_failure", 2),
  event(5, 9, "automatic_replan", 2), event(6, 56, "recovered", 2),
];

describe("Recorded story landmarks", () => {
  it("locates pre-fault and fault records separately, even in the same minute", () => {
    expect(replayLandmarks(history())).toEqual([
      {kind: "approved", sequence: 2}, {kind: "beforeFault", sequence: 3},
      {kind: "fault", sequence: 4}, {kind: "decision", sequence: 5}, {kind: "recovered", sequence: 6},
    ]);
  });
  it("does not invent failure, repair or completion in an unfinished history", () => {
    expect(replayLandmarks(history().slice(0, 3))).toEqual([{kind: "approved", sequence: 2}]);
    expect(replayLandmarks([event(1, 9, "ground_vehicle_failure")])).toEqual([{kind: "fault", sequence: 1}]);
  });
  it("labels a failed replan as a decision, not a successful repair", () => {
    const events = history().slice(0, 5); events[4] = event(5, 9, "automatic_policy_replan_failed", 2);
    expect(replayLandmarks(events).at(-1)).toEqual({kind: "decision", sequence: 5});
    expect(replayLandmarks(events).some(x => x.kind === "recovered")).toBe(false);
  });
  it("opens the recorded fault for the story entry and the latest event otherwise", () => {
    expect(replayInitialCursor(history(), "disruption")).toBe(3);
    expect(replayInitialCursor(history(), "latest")).toBe(5);
    expect(replayInitialCursor(history().slice(0, 3), "disruption")).toBe(2);
    expect(replayInitialCursor([], "disruption")).toBe(0);
  });
});

describe("Exact, read-only historical selection", () => {
  it("uses sequence, not time, and does not reveal a same-minute future replan", () => {
    const selected = historicalSelection(history(), "s", 4)!;
    expect(selected.prefix.map(e => e.sequence)).toEqual([1, 2, 3, 4]);
    expect(selected.event.type).toBe("ground_vehicle_failure");
    expect(historicalWorldAt("s", selected.prefix, 9)?.execution?.committed_operations).toHaveLength(2);
    expect(selected.world.scenario.runways[0].status).toBe("closed");
  });
  it("supports unsorted input without mutating stored history", () => {
    const events = history().reverse(), before = JSON.stringify(events);
    for (const sequence of [6, 3, 4, 1, 5]) expect(historicalSelection(events, "s", sequence)).toBeDefined();
    expect(JSON.stringify(events)).toBe(before);
  });
  it("rejects foreign-session events, duplicates, missing target and invalid sequences", () => {
    expect(historicalSelection(history(), "other", 3)).toBeUndefined();
    expect(historicalSelection([...history(), {...event(7, 60), session_id: "other"}], "s", 3)).toBeUndefined();
    expect(historicalSelection([...history(), event(3, 9)], "s", 3)).toBeUndefined();
    expect(historicalSelection(history(), "s", 99)).toBeUndefined();
    expect(historicalSelection([event(0, 0)], "s", 0)).toBeUndefined();
  });
  it("rejects missing evidence, mismatched world identity and backwards time", () => {
    const missing = history(); missing[2] = {...missing[2], payload: {}};
    expect(historicalSelection(missing, "s", 4)).toBeUndefined();
    const mismatch = history(); const snap = mismatch[2].payload.world_snapshot as {session_id: string}; snap.session_id = "other";
    expect(historicalSelection(mismatch, "s", 4)).toBeUndefined();
    expect(historicalSelection([event(1, 9), event(2, 8)], "s", 2)).toBeUndefined();
  });
  it("allows a verified earlier prefix even if later snapshot evidence is missing", () => {
    const events = history(); events[5] = {...events[5], payload: {}};
    expect(historicalSelection(events, "s", 4)?.prefix).toHaveLength(4);
    expect(historicalSelection(events, "s", 6)).toBeUndefined();
  });
});

describe("Replay entry and honest labels", () => {
  const render = (events: ExecutionEvent[], focus: "disruption" | "latest" = "disruption") =>
    renderToStaticMarkup(<ReplayPanel events={events} language="zh" initialFocus={focus}
      onClose={() => {}} onPlay={() => {}} onSeek={() => {}} />);
  it("shows direct inspection plus failure landmarks on the story entry", () => {
    const html = render(history());
    expect(html).toContain("查看此刻现场");
    expect(html).toContain("故障前");
    expect(html).toContain('aria-pressed="true">故障发生');
    expect(html).toContain("EVENT #4");
    expect(html).toContain("不改写执行账本");
  });
  it("disables map inspection when historical evidence is incomplete", () => {
    const events = history(); events[2] = {...events[2], payload: {}};
    expect(render(events)).toMatch(/class="replay-primary replay-seek" disabled=""/);
  });
  it("does not call playback or seek simply by rendering chapter navigation", () => {
    let calls = 0;
    renderToStaticMarkup(<ReplayPanel events={history()} language="en" initialFocus="disruption"
      onClose={() => {}} onPlay={() => {calls++;}} onSeek={() => {calls++;}} />);
    expect(calls).toBe(0);
  });
  it("names the total connecting passengers rather than claiming everybody is at risk", () => {
    expect(copy.zh.transferPassengers).toBe("转机旅客");
    expect(copy.en.transferPassengers).toBe("Connecting passengers");
    expect(Object.keys(copy.zh).sort()).toEqual(Object.keys(copy.en).sort());
  });
});
