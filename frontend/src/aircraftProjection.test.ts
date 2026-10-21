import { describe, it, expect } from "vitest";
import { aircraftStageAt, aircraftPositionAt, executionMilestones, milestonesFromEvent, apronSpineX } from "./aircraftProjection";
import type { CommittedOperation, ExecutionEvent } from "./types";
const operation: CommittedOperation = { flight_id: "F1", plan_id: "old", revision: 1,
  runway: "07L", gate: "A1", landing_minute: 3, runway_release_minute: 6,
  gate_start_minute: 9, gate_release_minute: 29, stage: "released", runway_released: true };
const event: ExecutionEvent = { sequence: 1, session_id: "s", type: "execution_tick", sim_minute: 3,
  created_at: "", payload: { ...operation } };
const runway = { x: 100, y: 100, width: 300, height: 20 };
const gate = { x: 10, y: 220 };

describe("committed aircraft projection", () => {
  it("reconstructs historical stages from time, not the latest released flag", () => {
    expect([2.9, 3, 5.99, 6, 8.99, 9, 28.99, 29].map(t => aircraftStageAt(operation,t)))
      .toEqual(["waiting","runway","runway","taxiing","taxiing","at_gate","at_gate","released"]);
  });
  it("keeps position continuous at runway release and gate entry", () => {
    for (const time of [6,9,29]) {
      const before=aircraftPositionAt(operation,time-1e-6,runway,gate,80);
      const after=aircraftPositionAt(operation,time,runway,gate,80);
      expect(Math.hypot(before.x-after.x,before.y-after.y)).toBeLessThan(.001);
    }
    expect(aircraftPositionAt(operation,9,runway,gate,80)).toEqual(gate);
    expect(aircraftPositionAt(operation,4,runway,gate,80).y).toBe(110);
  });
  it("is unchanged by refresh, terminal snapshot stage, or a revised future plan", () => {
    const before=executionMilestones([], [event], 7).get("F1")!;
    const after=executionMilestones([operation], [event], 7).get("F1")!;
    expect(aircraftStageAt(before,7)).toBe("taxiing");
    expect(aircraftPositionAt(before,7,runway,gate,80)).toEqual(aircraftPositionAt(after,7,runway,gate,80));
    expect(executionMilestones([operation], [event], 2).size).toBe(0);
  });
  it("retains two committed aircraft through a fault and never double-occupies a legal stand", () => {
    const second={...operation, flight_id:"F2", landing_minute:23, runway_release_minute:26,
      gate_start_minute:29, gate_release_minute:49};
    expect(executionMilestones([operation,second],[],27).size).toBe(2);
    for(let t=0;t<=50;t+=.25) {
      expect([operation,second].filter(op=>aircraftStageAt(op,t)==="at_gate").length).toBeLessThanOrEqual(1);
    }
    expect(aircraftStageAt(operation,29)).toBe("released");
    expect(aircraftStageAt(second,29)).toBe("at_gate");
  });
  it("does not fabricate milestone times for legacy or malformed logs", () => {
    expect(milestonesFromEvent({...event,payload:{flight_id:"F1",runway:"07L",gate:"A1"}})).toBeUndefined();
    expect(milestonesFromEvent({...event,payload:{...event.payload,gate_start_minute:2}})).toBeUndefined();
    expect(milestonesFromEvent({...event,payload:{...event.payload,gate_release_minute:NaN}})).toBeUndefined();
  });
});

it("does not announce same-minute releases or gate arrival before their recorded acknowledgement",()=>{
  const pending={...operation,stage:"taxiing" as const,runway_released:false};
  let snapshot=executionMilestones([pending],[event],6,6).get("F1")!;
  expect(aircraftStageAt(snapshot,6)).toBe("runway");
  snapshot=executionMilestones([{...pending,runway_released:true}],[event],9,9).get("F1")!;
  expect(aircraftStageAt(snapshot,9)).toBe("taxiing");
  snapshot=executionMilestones([{...pending,stage:"at_gate",runway_released:true}],[event],9,9).get("F1")!;
  expect(aircraftStageAt(snapshot,9)).toBe("at_gate");
  snapshot=executionMilestones([{...pending,stage:"at_gate",runway_released:true}],[event],29,29).get("F1")!;
  expect(aircraftStageAt(snapshot,29)).toBe("at_gate");
  snapshot=executionMilestones([operation],[event],29,29).get("F1")!;
  expect(aircraftStageAt(snapshot,29)).toBe("released");
});

it("uses a separate apron lane and never crosses another parked aircraft",()=>{
 const r={x:676,y:192,width:660,height:44},spine=620;
 const stands=[{x:462,y:220},{x:504,y:220},{x:546,y:220},{x:462,y:466},{x:504,y:466},{x:546,y:466},{x:580,y:514}];
 for(const target of stands){
  const lane=target.y===220?284:target.y===466?402:566;
  for(let i=0;i<=400;i++){
   const point=aircraftPositionAt({...operation,landing_minute:4,runway_release_minute:7,gate_start_minute:12},
     7+5*i/400,r,target,spine,lane);
   for(const parked of stands.filter(s=>s!==target)){
    expect(Math.hypot(point.x-parked.x,point.y-parked.y)).toBeGreaterThan(31);
   }
  }
 }
});

it("holds an incoming aircraft clear of a still-occupied destination stand", () => {
  const previous = {...operation, flight_id: "SK218", gate: "A4", gate_release_minute: 31};
  const next = {...operation, flight_id: "JR330", gate: "A4", landing_minute: 26,
    runway_release_minute: 29, gate_start_minute: 31, gate_release_minute: 53};
  for (const t of [30, 30.5, 30.9, 30.99, 30.999]) {
    const occupied = aircraftStageAt(previous, t) === "at_gate";
    const point = aircraftPositionAt(next, t, runway, gate, 80, gate.y - 64, occupied);
    expect(Math.hypot(point.x - gate.x, point.y - gate.y)).toBeGreaterThanOrEqual(38);
  }
  expect(aircraftPositionAt(next, 30.999, runway, gate, 80, gate.y - 64, true).waitingForStand).toBe(true);
  expect(aircraftStageAt(previous, 31)).toBe("released");
  expect(aircraftPositionAt(next, 31, runway, gate, 80, gate.y - 64, false)).toEqual(gate);
  // The hold is display-only; the approved milestone times are unchanged.
  expect(next.gate_start_minute).toBe(31);
});

it("keeps taxi routes clear of parked stands in narrow windows", () => {
  // Viewport fixtures include the remote stand to the right of the six terminal stands.
  const layouts = [
    {width: 900, xs: [300,334,368,395.2]},
    {width: 1024, xs: [337.2,373.04,408.88,437.552]},
    {width: 1172, xs: [381.6,422.62,463.64,496.456]},
    {width: 1440, xs: [462,504,546,579.6]},
  ];
  for (const {width, xs} of layouts) {
    const stands = [...xs.slice(0,3).map(x => ({x,y:240})),
      ...xs.slice(0,3).map(x => ({x,y:480})), {x:xs[3],y:532}];
    const spine = apronSpineX(width * .43, stands);
    const r = {x:width*.47, y:180, width:width*.46, height:40};
    for (const target of stands) {
      const lane = target.y === 240 ? 304 : target.y === 480 ? 416 : 584;
      for (let i=0; i<=200; i++) {
        const point = aircraftPositionAt(operation, 6+3*i/200, r, target, spine, lane);
        for (const parked of stands.filter(p => p !== target)) {
          expect(Math.hypot(point.x-parked.x,point.y-parked.y),
            `width ${width}, target ${target.x}, parked ${parked.x}`).toBeGreaterThan(31);
        }
      }
    }
  }
});
