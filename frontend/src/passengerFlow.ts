import type { PassengerGroup } from "./passengerView";

export const PASSENGER_WALK_MS = 10000;
export const PASSENGER_STAGGER_MS = 650;
export const PASSENGER_BATCH_MS = PASSENGER_WALK_MS + 2 * PASSENGER_STAGGER_MS;

export function passengerWalkFrame(elapsedMs: number, slot: number) {
  const local = elapsedMs - slot * PASSENGER_STAGGER_MS;
  return {
    progress: Math.max(0, Math.min(1, local / PASSENGER_WALK_MS)),
    alpha: Math.max(0, Math.min(1, local / 600, 1)),
    stride: Math.sin(Math.max(0, local) / 135),
  };
}

export type PassengerStay = { group:PassengerGroup; slot:number };
export type PassengerWalk = PassengerStay & { elapsedMs:number; lane:number };

/** Bounded independent walks; arrivals keep moving even if the aircraft clock is held. */
export class PassengerFlow {
  private seen = new Set<string>();
  private pending: PassengerStay[] = [];
  private waiting: PassengerStay[] = [];
  private nextSlot = 0;
  private previousMinute?: number;
  private running: PassengerWalk[] = [];
  constructor(private readonly capacity = 1) {}
  get active() { return this.running[0]; }
  get walkers(): readonly PassengerWalk[] { return this.running; }
  get queued() { return this.pending.length; }
  get settled(): readonly PassengerStay[] { return this.waiting; }

  reset() {
    this.seen.clear(); this.pending = []; this.running = []; this.waiting = []; this.nextSlot=0; this.previousMinute = undefined;
  }

  sync(groups: PassengerGroup[], minute: number) {
    if (this.previousMinute !== undefined && minute < this.previousMinute - .01) this.reset();
    const initial = this.previousMinute === undefined;
    const arrivals = groups.filter(g => g.stage === "terminal" && g.gateMinute !== undefined
      && Number.isFinite(g.gateMinute) && g.gateMinute <= minute)
      .sort((a,b) => a.gateMinute! - b.gateMinute! || a.flightId.localeCompare(b.flightId));
    for (const group of arrivals) {
      if (this.seen.has(group.flightId)) continue;
      this.seen.add(group.flightId);
      const stay={group:{...group},slot:this.nextSlot++};
      // Restore old arrivals at their waiting seats, without replaying old walks.
      if (initial && group.gateMinute! < minute - .25) this.waiting.push(stay);
      else this.pending.push(stay);
    }
    this.previousMinute = minute;
    this.startNext();
  }

  advance(deltaMs: number, rate: number, paused: boolean) {
    if (paused || !Number.isFinite(deltaMs) || !Number.isFinite(rate) || deltaMs <= 0 || rate <= 0) return;
    let remaining = deltaMs * rate;
    while (this.running.length && remaining > 0) {
      const step = Math.min(remaining, ...this.running.map(w => PASSENGER_BATCH_MS - w.elapsedMs));
      this.running.forEach(w => { w.elapsedMs += step; });
      remaining -= step;
      this.waiting.push(...this.running.filter(w=>w.elapsedMs>=PASSENGER_BATCH_MS)
        .map(({group,slot})=>({group,slot})));
      this.running = this.running.filter(w => w.elapsedMs < PASSENGER_BATCH_MS);
      this.startNext();
    }
  }

  private startNext() {
    const limit = Math.max(1, Math.min(3, Math.floor(this.capacity) || 1));
    while (this.running.length < limit && this.pending.length) {
      const lane = Array.from({length: limit},(_,i)=>i)
        .find(i=>!this.running.some(w=>w.lane===i))!;
      this.running.push({ ...this.pending.shift()!, elapsedMs: 0, lane });
    }
  }
}
