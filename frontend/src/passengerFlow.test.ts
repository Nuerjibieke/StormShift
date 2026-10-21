import { describe, expect, it } from "vitest";
import { PassengerFlow, passengerWalkFrame, PASSENGER_BATCH_MS, PASSENGER_WALK_MS } from "./passengerFlow";
import type { PassengerGroup } from "./passengerView";
const cohort = (flightId="F1", gateMinute=5): PassengerGroup =>
  ({flightId, gateMinute, count:60, gate:"A1", stage:"terminal"});

describe("continuous schematic passenger walks", () => {
  it("only starts acknowledged arrivals, never proposals, onboard people or initial ground populations", () => {
    const flow = new PassengerFlow();
    flow.sync([{...cohort(),stage:"onboard"}, {...cohort("ground"),gateMinute:undefined}], 5);
    expect(flow.active).toBeUndefined();
    flow.sync([cohort()], 5);
    expect(flow.active?.group.flightId).toBe("F1");
    expect(flow.active?.elapsedMs).toBe(0);
  });
  it("walks gradually, freezes exactly on pause and scales with playback speed", () => {
    const flow = new PassengerFlow(); flow.sync([cohort()],5);
    flow.advance(2000,.5,false); expect(flow.active?.elapsedMs).toBe(1000);
    flow.advance(4000,2,true); expect(flow.active?.elapsedMs).toBe(1000);
    flow.advance(1000,2,false); expect(flow.active?.elapsedMs).toBe(3000);
    expect(passengerWalkFrame(3000,0).progress).toBe(.3);
  });
  it("queues new arrivals without replacing a walker or cutting it off after three simulated minutes", () => {
    const flow = new PassengerFlow(); flow.sync([],0); flow.sync([cohort()],5);
    flow.advance(3000,1,false);
    flow.sync([cohort(),cohort("F2",6)],9);
    expect(flow.active?.group.flightId).toBe("F1"); expect(flow.active?.elapsedMs).toBe(3000);
    expect(flow.queued).toBe(1);
    flow.advance(PASSENGER_BATCH_MS-3000,1,false);
    expect(flow.active?.group.flightId).toBe("F2"); expect(flow.active?.elapsedMs).toBe(0);
    flow.advance(PASSENGER_BATCH_MS,1,false); expect(flow.active).toBeUndefined();
    flow.sync([cohort(),cohort("F2",6)],20); expect(flow.active).toBeUndefined();
  });
  it("emerges gradually at the door and stays visible at the waiting area", () => {
    expect(passengerWalkFrame(0,0)).toMatchObject({progress:0,alpha:0});
    expect(passengerWalkFrame(300,0).alpha).toBe(.5);
    expect(passengerWalkFrame(300,1).alpha).toBe(0);
    expect(passengerWalkFrame(1000,0).progress).toBeGreaterThan(passengerWalkFrame(1000,1).progress);
    expect(passengerWalkFrame(PASSENGER_WALK_MS-300,0).alpha).toBe(1);
    expect(passengerWalkFrame(PASSENGER_WALK_MS,0)).toMatchObject({progress:1,alpha:1});
    expect(passengerWalkFrame(999999,2)).toMatchObject({progress:1,alpha:1});
  });
  it("does not reset during a same-time replan, layer change or language change", () => {
    const flow = new PassengerFlow();flow.sync([cohort()],5);flow.advance(2500,1,false);
    flow.sync([],5);flow.sync([cohort()],5);
    expect(flow.active?.elapsedMs).toBe(2500);expect(flow.active?.group.gate).toBe("A1");
  });
  it("skips old arrivals on reload, resets on rewind, and can replay the crossing once", () => {
    const flow = new PassengerFlow();flow.sync([cohort()],20);expect(flow.active).toBeUndefined();
    flow.sync([],0);flow.sync([cohort()],5);expect(flow.active?.group.flightId).toBe("F1");
    flow.reset();flow.sync([],0);expect(flow.active).toBeUndefined();expect(flow.queued).toBe(0);
  });
  it("uses stable chronological order for simultaneous arrivals without mutating input", () => {
    const groups=[cohort("F2"),cohort("F1")], before=JSON.stringify(groups), flow=new PassengerFlow();
    flow.sync(groups,5);expect(flow.active?.group.flightId).toBe("F1");
    flow.advance(PASSENGER_BATCH_MS,1,false);expect(flow.active?.group.flightId).toBe("F2");
    expect(JSON.stringify(groups)).toBe(before);
  });
});

it("runs several acknowledged flight groups concurrently, keeps stable lanes, and drains the queue",()=>{
 const flow=new PassengerFlow(2);flow.sync([],0);
 flow.sync([cohort("F1"),cohort("F2"),cohort("F3")],5);
 expect(flow.walkers.map(w=>w.group.flightId)).toEqual(["F1","F2"]);
 expect(flow.walkers.map(w=>w.lane)).toEqual([0,1]);expect(flow.queued).toBe(1);
 flow.advance(3000,1,false);const saved=flow.walkers.map(w=>w.elapsedMs);
 flow.sync([cohort("F1"),cohort("F2"),cohort("F3")],5);
 expect(flow.walkers.map(w=>w.elapsedMs)).toEqual(saved);
 flow.advance(PASSENGER_BATCH_MS-3000,1,false);
 expect(flow.walkers.map(w=>w.group.flightId)).toEqual(["F3"]);
 flow.advance(PASSENGER_BATCH_MS,1,false);expect(flow.walkers).toHaveLength(0);
 flow.sync([cohort("F1"),cohort("F2"),cohort("F3")],30);expect(flow.walkers).toHaveLength(0);
});

it("keeps exactly one stable waiting icon per arrived flight, including reload and replanning",()=>{
 const flow=new PassengerFlow();flow.sync([],0);flow.sync([cohort("F1")],5);
 flow.advance(PASSENGER_BATCH_MS,1,false);
 expect(flow.walkers).toHaveLength(0);expect(flow.settled.map(p=>[p.group.flightId,p.slot])).toEqual([["F1",0]]);
 flow.sync([cohort("F1"),cohort("F2",6)],6);flow.advance(PASSENGER_BATCH_MS,1,false);
 expect(flow.settled.map(p=>[p.group.flightId,p.slot])).toEqual([["F1",0],["F2",1]]);
 flow.sync([],6);flow.sync([cohort("F1"),cohort("F2",6)],6);expect(flow.settled).toHaveLength(2);
 flow.reset();flow.sync([cohort("F1"),cohort("F2",6)],20);
 expect(flow.active).toBeUndefined();expect(flow.settled.map(p=>p.group.flightId)).toEqual(["F1","F2"]);
});
