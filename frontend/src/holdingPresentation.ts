import { historicalWorld } from "./historicalWorld";
import type { ExecutionEvent, Flight, SessionState } from "./types";

/** The story follows an initial flight, never re-picks a protagonist per plan. */
export function storyFocusFlight(
  session: Pick<SessionState, "id" | "scenario" | "execution">, events: ExecutionEvent[],
): string {
  const created = events.filter(e => e.session_id === session.id && e.type === "session_created")
    .sort((a, b) => a.sequence - b.sequence).find(e => historicalWorld(e));
  const initial = created ? historicalWorld(created)!.scenario : undefined;
  const source = initial ?? session.scenario;
  const occupied = new Set((source.gates ?? []).map(g => g.occupied_by).filter(Boolean));
  // Old logs without an initial snapshot may still have a fixed execution scope.
  const scope = !initial ? session.execution?.scope_flight_ids : undefined;
  const eligible = source.flights.filter(f => scope ? scope.includes(f.id)
    : !occupied.has(f.id) && !["at_gate", "released", "diverted", "landed", "taxiing"].includes(f.state));
  return [...eligible].sort((a, b) => b.connections - a.connections || a.id.localeCompare(b.id))[0]?.id ?? "";
}

/** Slots are schematic positions, not landing priorities. Never mutate the queue.
 * Preserve surviving positions and reserve visibility for the story focus.
 */
export function allocateHoldingSlots(
  pendingIds: readonly string[], previous: ReadonlyMap<string, number>,
  capacity: number, focusId?: string,
): Map<string, number> {
  const limit = Number.isFinite(capacity) ? Math.max(0, Math.floor(capacity)) : 0;
  const pending = [...new Set(pendingIds)], ranks = new Map(pending.map((id, i) => [id, i]));
  const slots = new Map<string, number>(), used = new Set<number>();
  for (const [id, slot] of previous) {
    if (!ranks.has(id) || !Number.isInteger(slot) || slot < 0 || slot >= limit || used.has(slot)) continue;
    slots.set(id, slot); used.add(slot);
  }
  if (!limit) return slots;
  const firstFree = () => Array.from({length: limit}, (_, i) => i).find(i => !used.has(i));
  if (focusId && ranks.has(focusId) && !slots.has(focusId)) {
    let slot = firstFree();
    if (slot === undefined) {
      const victim = [...slots.keys()].sort((a, b) => ranks.get(b)! - ranks.get(a)!)[0];
      slot = slots.get(victim)!; slots.delete(victim);
    }
    slots.set(focusId, slot); used.add(slot);
  }
  for (const id of pending) {
    if (slots.has(id)) continue;
    const slot = firstFree();
    if (slot === undefined) break;
    slots.set(id, slot); used.add(slot);
  }
  return slots;
}

export function holdingLayout(width: number, runwayY: number) {
  const left = width * .60, availableWidth = Math.max(1, width - 24 - left);
  const columns = Math.max(1, Math.min(4, Math.floor(availableWidth / 122)));
  const lastRowY = Math.max(12, runwayY - 24);
  const rows = columns < 4 && lastRowY >= 104 ? 2 : 1;
  const capacity = Math.min(4, columns * rows), firstRowY = lastRowY - (rows - 1) * 40;
  return {
    left, availableWidth, columns, rows, capacity,
    showCaptions: firstRowY >= 64, captionY: firstRowY - 52,
    point: (slot: number) => ({x: left + 16 + (slot % columns) * availableWidth / columns,
      y: firstRowY + Math.floor(slot / columns) * 40}),
  };
}

const holdingText = {
  zh: {area: "等待降落",notPriority: "图中位置不代表先后顺序",focus: "正在跟随",remaining: "其他等待航班",complete: "下客与地面服务已完成"},
  en: {area: "HOLDING VIEW",notPriority: "Position is not landing order",focus: "FOCUS",remaining: "OTHER WAITING",complete: "GROUND SERVICES COMPLETE"},
} as const;
export function holdingCaption(shown: number, total: number, language: "zh" | "en") {
  const t = holdingText[language];
  return `${t.area} ${shown}/${total}`;
}
export function hiddenHoldingCaption(ids: readonly string[], width: number, language: "zh" | "en") {
  const count = Math.max(1, Math.min(6, Math.floor((width - 130) / 52)));
  return `${holdingText[language].remaining} ${ids.length}: ${ids.slice(0, count).join(" · ")}${ids.length > count ? ` · +${ids.length - count}` : ""}`;
}
export function holdingStageLabel(rank: number, runway: string, focus: boolean, language: "zh" | "en") {
  void rank;
  return `${language === "zh" ? "等待降落" : "WAITING TO LAND"} · ${runway}${focus ? ` · ${holdingText[language].focus}` : ""}`;
}
export function completedFlightsLabel(flights: Pick<Flight, "id">[], focusId: string | undefined, language: "zh" | "en") {
  const names = [...flights].sort((a, b) => Number(b.id === focusId) - Number(a.id === focusId))
    .map(f => f.id + (f.id === focusId ? ` (${holdingText[language].focus})` : ""));
  return `${holdingText[language].complete}: ${names.join(" · ")}`;
}
