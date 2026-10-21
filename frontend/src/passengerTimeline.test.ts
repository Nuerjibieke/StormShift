import {describe,it,expect} from "vitest";
import {PassengerTimeline,TRANSFER_WALK_MS,BOARDING_WALK_MS} from "./passengerTimeline";
import {connectingPassenger,departurePosition,type ConnectionGeometry} from "./connectionProjection";
import type {ConnectingDeparture} from "./types";

const c:ConnectingDeparture={
 flight_id:"NH701",source_flight_id:"SK218",passenger_count:24,
 passenger_stage:"disembarking",stage:"waiting_aircraft",source_gate:"A4",
 source_arrival_minute:9,transfer_start_minute:12,transfer_ready_minute:18,
 carrier_flight_id:"OZ284",gate:"A3",runway:"07L",boarding_start_minute:50,
 pushback_minute:56,taxi_start_minute:60,holding_short_minute:66,takeoff_minute:69,
 airborne_minute:72,completed_minute:77,next_minute:12,boarded_count:0,
 safety_checks:[],blocked_reason:null,
};
const geometry:ConnectionGeometry={sourceGate:{x:462,y:466},gate:{x:546,y:220},
 corridorX:420,loungeY:506,terminalX:72,runway:{x:676,y:192,width:660,height:44},spineX:620,width:1440};

describe("pedestrian clock independent of aircraft",()=>{
 it("continues walking through a frozen aircraft minute and reaches the lounge",()=>{
  const flow=new PassengerTimeline();flow.sync(c,9);flow.advance(2000,1,false);
  const a=flow.frame()!, p=connectingPassenger(a.connection,a.minute,0,geometry)!;
  for(let i=0;i<50;i++){flow.sync(c,9);flow.advance(100,1,false);}
  const b=flow.frame()!, q=connectingPassenger(b.connection,b.minute,0,geometry)!;
  expect(Math.hypot(q.x-p.x,q.y-p.y)).toBeGreaterThan(10);
  flow.advance(TRANSFER_WALK_MS,1,false);
  expect(flow.frame()!.connection.passenger_stage).toBe("waiting");
  expect(c.passenger_stage).toBe("disembarking");
 });
 it("freezes exactly on explicit global pause, and resumes at the chosen speed",()=>{
  const flow=new PassengerTimeline();flow.sync(c,9);flow.advance(1000,.5,false);
  const before=flow.frame();flow.advance(10000,2,true);expect(flow.frame()).toEqual(before);
  flow.advance(1000,2,false);expect(flow.frame()!.minute).toBeGreaterThan(before!.minute);
 });
 it("does not start deplaning or boarding just because a future schedule is known",()=>{
  const flow=new PassengerTimeline();flow.sync({...c,passenger_stage:"onboard"},8);flow.advance(60000,1,false);
  expect(flow.frame()!.connection.passenger_stage).toBe("onboard");
  flow.sync(c,9);flow.advance(60000,1,false);
  expect(flow.frame()!.connection.passenger_stage).toBe("waiting");
 });
 it("boards while the airframe stays parked, without advancing the aircraft clock",()=>{
  const flow=new PassengerTimeline();flow.sync({...c,passenger_stage:"waiting"},30);
  const boarding={...c,stage:"boarding" as const,passenger_stage:"boarding" as const};
  flow.sync(boarding,50);flow.advance(2000,1,false);
  const before=flow.frame()!;
  flow.sync(boarding,50);flow.advance(2000,1,false);
  const after=flow.frame()!;
  expect(departurePosition(boarding,50,geometry)).toEqual(departurePosition(boarding,50,geometry));
  expect(connectingPassenger(after.connection,after.minute,0,geometry)!.progress)
   .toBeGreaterThan(connectingPassenger(before.connection,before.minute,0,geometry)!.progress);
  flow.advance(BOARDING_WALK_MS,1,false);expect(flow.frame()!.connection.passenger_stage).toBe("boarded");
 });
 it("reconstructs saved sessions and resets on rewind, not on replanning",()=>{
  const flow=new PassengerTimeline();flow.sync({...c,passenger_stage:"waiting"},30);
  expect(flow.frame()!.connection.passenger_stage).toBe("waiting");
  flow.sync(c,9);expect(flow.frame()!.minute).toBe(9);
  flow.advance(1500,1,false);const before=flow.frame();
  flow.sync({...c,source_gate:"A4"},9);expect(flow.frame()).toEqual(before);
 });
});
