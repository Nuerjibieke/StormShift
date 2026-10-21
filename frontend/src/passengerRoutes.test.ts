import {describe,it,expect} from "vitest";
import {pointOnPath} from "./aircraftProjection";
import {passengerHall,gatePassengerAccess,arrivingPassengerRoute,type PassengerHall} from "./passengerRoutes";
import {connectingPassenger,type ConnectionGeometry} from "./connectionProjection";
import type {ConnectingDeparture} from "./types";
const connection:ConnectingDeparture={
 flight_id:"NH701",source_flight_id:"SK218",passenger_count:24,passenger_stage:"disembarking",stage:"turnaround",
 source_gate:"A4",source_arrival_minute:9,transfer_start_minute:12,transfer_ready_minute:18,carrier_flight_id:"F8",
 gate:"A3",runway:"07L",boarding_start_minute:50,pushback_minute:56,taxi_start_minute:60,holding_short_minute:66,
 takeoff_minute:69,airborne_minute:72,completed_minute:77,next_minute:12,boarded_count:0,safety_checks:[],blocked_reason:null,
};
const distance=(a:{x:number;y:number},b:{x:number;y:number})=>Math.hypot(a.x-b.x,a.y-b.y);
describe("visible passenger-only routes",()=>{
 for(const [width,height] of [[900,500],[1440,650],[1920,720]]){
  const w=Math.max(210,Math.min(width*.25,360)),h=Math.min(height*.56,w*1.35);
  const t:PassengerHall={terminalX:width*.05,terminalY:height*.28,terminalW:w,terminalH:h};
  const hall=passengerHall(t,9),spacing=Math.max(34,Math.min(42,width*.035));
  const gates=Array.from({length:6},(_,i)=>({x:t.terminalX+w+30+i%3*spacing,y:t.terminalY+h*(i<3?.18:.82)}));
  gates.push({x:t.terminalX+w+30+spacing*2.8,y:t.terminalY+h*.82+52});
  const board={x:t.terminalX+18,y:t.terminalY+58,right:t.terminalX+w-54,bottom:hall.waitingTop-19};
  it("avoids parked aircraft and the information board at "+width+"px, including the remote stand",()=>{
   for(const gate of gates)for(let slot=0;slot<9;slot++){
    const route=arrivingPassengerRoute(gate,t,slot,9);
    for(let i=0;i<=400;i++){
     const p=pointOnPath(route,i/400);
     // Account for the passenger's head/body, not just the path centre.
     expect(p.x+4<board.x||p.x-4>board.right||p.y+12<board.y||p.y-8>board.bottom).toBe(true);
     for(const parked of gates)expect(Math.abs(p.x-parked.x)>=14.5||Math.abs(p.y-parked.y)>=28).toBe(true);
    }
    expect(route.at(-1)).toEqual(hall.otherExit);
   }
  });
  it("uses the identical visible bridge in reverse for boarding at "+width+"px",()=>{
   const geometry:ConnectionGeometry={sourceGate:gates[3],gate:gates[2],
    sourceAccess:gatePassengerAccess(gates[3],hall.corridorX,t.terminalY+h/2),
    gateAccess:gatePassengerAccess(gates[2],hall.corridorX,t.terminalY+h/2),
    corridorX:hall.corridorX,loungeY:hall.lounge.y,lounge:hall.lounge,terminalX:t.terminalX,
    runway:{x:width*.47,y:height*.28,width:width*.46,height:40},spineX:width*.43,width};
   for(const [stage,start,end] of [["disembarking",9,12],["transferring",12,18],["boarding",50,56]] as const){
    let previous:{x:number;y:number}|undefined;
    for(let i=0;i<=400;i++){
     const p=connectingPassenger({...connection,passenger_stage:stage},start+(end-start)*i/400,0,geometry)!;
     expect(p.x+4<board.x||p.x-4>board.right||p.y+12<board.y||p.y-8>board.bottom).toBe(true);
     for(const parked of gates)expect(Math.abs(p.x-parked.x)>=14.5||Math.abs(p.y-parked.y)>=28).toBe(true);
     if(previous)expect(distance(p,previous)).toBeLessThan(5);previous=p;
    }
   }
   const end=connectingPassenger({...connection,passenger_stage:"transferring"},18,0,geometry)!;
   const wait=connectingPassenger({...connection,passenger_stage:"waiting"},40,0,geometry)!;
   const boardStart=connectingPassenger({...connection,passenger_stage:"boarding"},50,0,geometry)!;
   expect(distance(end,wait)).toBeLessThan(.001);expect(distance(wait,boardStart)).toBeLessThan(.001);
  });
  it("keeps up to twenty flight icons separated in the waiting area at "+width+"px",()=>{
   const large=passengerHall(t,20),points=Array.from({length:20},(_,i)=>large.waitingSlot(i));
   for(let i=0;i<points.length;i++)for(let j=i+1;j<points.length;j++){
    expect(Math.abs(points[i].x-points[j].x)>=8||Math.abs(points[i].y-points[j].y)>=19).toBe(true);
   }
  });
 }
});
