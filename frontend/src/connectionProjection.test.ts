import {describe,it,expect} from "vitest";
import {connectingPassenger,departurePosition,journeyStep,type ConnectionGeometry} from "./connectionProjection";
import {nextExecutionMinute} from "./executionDriver";
import {buildPlaybackSchedule} from "./playback";
import type {ConnectingDeparture,ExecutionEvent,SessionState} from "./types";
const c:ConnectingDeparture={
 flight_id:"NH701",source_flight_id:"SK218",passenger_count:24,passenger_stage:"disembarking",stage:"turnaround",
 source_gate:"A1",source_arrival_minute:9,transfer_start_minute:12,transfer_ready_minute:18,carrier_flight_id:"F8",
 gate:"A3",runway:"07L",boarding_start_minute:50,pushback_minute:56,taxi_start_minute:60,holding_short_minute:66,
 takeoff_minute:69,airborne_minute:72,completed_minute:77,next_minute:12,boarded_count:0,safety_checks:[],blocked_reason:null,
};
const g:ConnectionGeometry={sourceGate:{x:230,y:100},gate:{x:260,y:180},corridorX:180,loungeY:230,terminalX:20,
 runway:{x:350,y:80,width:600,height:32},spineX:320,width:1000};
const dist=(a:{x:number;y:number},b:{x:number;y:number})=>Math.hypot(a.x-b.x,a.y-b.y);
describe("one continuous connection journey",()=>{
 it("keeps people continuous between deplaning, transfer, lounge and boarding",()=>{
  for(let slot=0;slot<3;slot++){
   const deplane=connectingPassenger(c,12,slot,g)!;
   const transferStart=connectingPassenger({...c,passenger_stage:"transferring"},12,slot,g)!;
   expect(dist(deplane,transferStart)).toBeLessThan(.001);
   const transferEnd=connectingPassenger({...c,passenger_stage:"transferring"},18,slot,g)!;
   const waiting=connectingPassenger({...c,passenger_stage:"waiting"},40,slot,g)!;
   const boarding=connectingPassenger({...c,passenger_stage:"boarding"},50,slot,g)!;
   expect(dist(transferEnd,waiting)).toBeLessThan(.001);
   expect(dist(waiting,boarding)).toBeLessThan(.001);
   expect(connectingPassenger({...c,passenger_stage:"boarding"},56,slot,g)!.alpha).toBe(0);
  }
 });
 it("only reveals people after gate arrival and hides them inside the airplane after boarding",()=>{
  expect(connectingPassenger({...c,passenger_stage:"onboard"},8,0,g)).toBeUndefined();
  expect(connectingPassenger(c,9,0,g)!.alpha).toBe(0);
  expect(connectingPassenger(c,10,0,g)!.alpha).toBe(1);
  expect(connectingPassenger({...c,passenger_stage:"boarded"},60,0,g)).toBeUndefined();
 });
 it("keeps the same physical plane at every outbound boundary, including the screen exit",()=>{
  const phases=[["boarding",56],["pushback",60],["taxiing",66],["holding_short",69],["takeoff",72],["airborne",77]] as const;
  const next=["pushback","taxiing","holding_short","takeoff","airborne","completed"] as const;
  phases.forEach(([stage,minute],i)=>{
   const before=departurePosition({...c,stage},minute,g),after=departurePosition({...c,stage:next[i]},minute,g);
   expect(dist(before,after)).toBeLessThan(.001);
  });
  expect(departurePosition({...c,stage:"completed"},77,g).x).toBeGreaterThan(g.width+40);
 });
 it("holds outside the runway until takeoff clearance, never extrapolates a future stage",()=>{
  const hold=departurePosition({...c,stage:"holding_short"},100,g);
  expect(hold.y).toBeGreaterThan(g.runway.y+g.runway.height);
  expect(departurePosition({...c,stage:"boarding"},100,g)).toMatchObject(g.gate);
  expect(journeyStep({...c,passenger_stage:"waiting"})).toBe(1);
 });
 it("uses the connection milestone when all arrivals have completed",()=>{
  const session={phase:"monitoring",scenario:{current_minute:60,runways:[],gates:[]},execution:{
   status:"running",pending_assignments:[],scheduled_events:[],processed_event_ids:[],committed_operations:[],connection:{...c,next_minute:66}}} as unknown as SessionState;
  expect(nextExecutionMinute(session)).toBe(66);
 });
 it("slows the entire stage interval, including clock samples, without changing existing inbound timing",()=>{
  const event={type:"departure_pushback",sim_minute:56,sequence:1,payload:{}} as ExecutionEvent;
  expect(buildPlaybackSchedule([event],50)[0].delayMs).toBe(13200);
  expect(buildPlaybackSchedule([{...event,type:"clock_advanced"},{...event,sequence:2}],50)[0].delayMs).toBe(13200);
  expect(buildPlaybackSchedule([{...event,type:"gate_released"}],50)[0].delayMs).toBe(4200);
 });
});

it("keeps a fixed walking headway without body overlap, including bends and door fades",()=>{
 for(const geometry of [g,{...g,sourceGate:{x:462,y:466},gate:{x:462,y:466},corridorX:420,loungeY:506,terminalX:72},
   {...g,sourceGate:{x:430,y:110},gate:{x:510,y:250},corridorX:390,loungeY:285,terminalX:45}]){
  for(const passenger_stage of ["disembarking","transferring","waiting","boarding"] as const){
   const from=passenger_stage==="disembarking"?9:passenger_stage==="transferring"?12:passenger_stage==="waiting"?18:50;
   const to=passenger_stage==="disembarking"?12:passenger_stage==="transferring"?18:passenger_stage==="waiting"?50:56;
   for(let i=0;i<=200;i++){
    const minute=from+(to-from)*i/200;
    const people=[0,1,2].map(slot=>connectingPassenger({...c,passenger_stage},minute,slot,geometry)!)
      .filter(p=>p.alpha>=.75);
    for(let left=0;left<people.length;left++)for(let right=left+1;right<people.length;right++){
     const dx=Math.abs(people[left].x-people[right].x),dy=Math.abs(people[left].y-people[right].y);
     expect(dx>=8||dy>=19).toBe(true);
    }
   }
  }
 }
});

it("walks passengers while their aircraft stays stationary during turnaround and boarding",()=>{
 for(const passenger_stage of ["disembarking","transferring","boarding"] as const){
  const stage = passenger_stage==="boarding" ? "boarding" : "turnaround";
  const state={...c,stage,passenger_stage} as ConnectingDeparture;
  const minute=passenger_stage==="disembarking"?10:passenger_stage==="transferring"?13:51;
  expect(departurePosition(state,minute,g)).toEqual(departurePosition(state,minute+1,g));
  expect(dist(connectingPassenger(state,minute,0,g)!,connectingPassenger(state,minute+1,0,g)!)).toBeGreaterThan(5);
 }
});

it("pushes back into a separate lane before taxiing past the neighbouring stands",()=>{
 const geometry={...g,gate:{x:462,y:220},apronLaneY:284,spineX:620,
   runway:{x:676,y:192,width:660,height:44},width:1440};
 const parked=[{x:504,y:220},{x:546,y:220},{x:462,y:466},{x:504,y:466},{x:546,y:466}];
 for(const stage of ["pushback","taxiing"] as const){
  const start=stage==="pushback"?56:60,end=stage==="pushback"?60:66;
  for(let i=0;i<=300;i++){
   const point=departurePosition({...c,stage},start+(end-start)*i/300,geometry);
   for(const other of parked)expect(Math.hypot(point.x-other.x,point.y-other.y)).toBeGreaterThan(31);
  }
 }
});
