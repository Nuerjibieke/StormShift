import { pointOnPath, type Point } from "./aircraftProjection";
import type { ConnectingDeparture } from "./types";

export type ConnectionGeometry = {
  sourceGate: Point; gate: Point; corridorX: number; loungeY: number; terminalX: number;
  runway: {x:number;y:number;width:number;height:number}; spineX:number; width:number; apronLaneY?:number;
  sourceAccess?:Point[]; gateAccess?:Point[]; lounge?:Point;
};
export function journeyFraction(minute:number,start:number|null,end:number|null) {
  if(start===null||end===null||end<=start)return 0;
  return Math.max(0,Math.min(1,(minute-start)/(end-start)));
}
function route(c:ConnectingDeparture,g:ConnectionGeometry): {points:Point[];start:number|null;end:number|null} {
  const {gate,runway:r}=g, push={x:gate.x,y:g.apronLaneY ?? gate.y+52};
  const hold={x:r.x+20,y:r.y+r.height+18};
  const threshold={x:r.x+20,y:r.y+r.height/2}, lift={x:r.x+r.width*.95,y:threshold.y};
  const away={x:g.width+90,y:threshold.y-100};
  switch(c.stage){
    case "pushback":return {points:[gate,push],start:c.pushback_minute,end:c.taxi_start_minute};
    case "taxiing":return {points:[push,{x:g.spineX,y:push.y},{x:g.spineX,y:hold.y},hold],start:c.taxi_start_minute,end:c.holding_short_minute};
    case "holding_short":return {points:[hold],start:null,end:null};
    case "takeoff":return {points:[hold,threshold,lift],start:c.takeoff_minute,end:c.airborne_minute};
    case "airborne":return {points:[lift,away],start:c.airborne_minute,end:c.completed_minute};
    case "completed":return {points:[away],start:null,end:null};
    default:return {points:[gate],start:null,end:null};
  }
}
export function departurePosition(c:ConnectingDeparture,minute:number,g:ConnectionGeometry) {
  const p=route(c,g), t=journeyFraction(minute,p.start,p.end), at=pointOnPath(p.points,t);
  const ahead=pointOnPath(p.points,Math.min(1,t+.002)), behind=pointOnPath(p.points,Math.max(0,t-.002));
  const parkedHeading=(g.apronLaneY ?? g.gate.y+52)>g.gate.y?-90:90;
  const angle=c.stage==="pushback"||["turnaround","boarding"].includes(c.stage)?parkedHeading:p.points.length===1?0:Math.atan2(ahead.y-behind.y,ahead.x-behind.x)*180/Math.PI;
  return {...at,angle};
}
export const PASSENGER_HEADWAY = 30;
function pathLength(points:Point[]) {
  return points.slice(1).reduce((sum,p,i)=>sum+Math.hypot(p.x-points[i].x,p.y-points[i].y),0);
}
export function connectingPassenger(c:ConnectingDeparture,minute:number,slot:number,g:ConnectionGeometry) {
  const source={x:g.sourceGate.x-10,y:g.sourceGate.y};
  const access=g.sourceAccess??[source,{x:g.corridorX,y:source.y}];
  const lounge=g.lounge??{x:g.terminalX+48,y:g.loungeY};
  const incoming=[...access,{x:g.corridorX,y:g.loungeY},lounge];
  const incomingLength=pathLength(incoming), bridgeLength=pathLength(access);
  let points:Point[],distance=0,alpha=1;
  if(c.passenger_stage==="disembarking"||c.passenger_stage==="transferring"){
    const head=c.passenger_stage==="disembarking"
      ? bridgeLength*journeyFraction(minute,c.source_arrival_minute,c.transfer_start_minute)
      : bridgeLength+(incomingLength-bridgeLength)*journeyFraction(minute,c.transfer_start_minute,c.transfer_ready_minute);
    points=incoming;distance=head-slot*PASSENGER_HEADWAY;
    // Followers remain inside the door until there is space; no overlapping fade-ins.
    alpha=Math.max(0,Math.min(1,distance/10));
  }else if(c.passenger_stage==="waiting"){
    points=incoming;distance=incomingLength-slot*PASSENGER_HEADWAY;
  }else if(c.passenger_stage==="boarding"){
    const outward=g.gateAccess??[{x:g.gate.x-10,y:g.gate.y},{x:g.corridorX,y:g.gate.y}];
    points=[lounge,{x:g.corridorX,y:g.loungeY},...outward.slice().reverse()];
    const length=pathLength(points);
    // The nearest person boards first; identities keep their lounge positions.
    distance=slot*PASSENGER_HEADWAY+length*journeyFraction(minute,c.boarding_start_minute,c.pushback_minute);
    alpha=Math.max(0,Math.min(1,(length-distance)/12));
  }else return undefined;
  const progress=Math.max(0,Math.min(1,distance/Math.max(.001,pathLength(points))));
  return {...pointOnPath(points,progress),alpha,progress,stride:Math.sin(minute*15-slot*.8)};
}

export function journeyStep(c:ConnectingDeparture) {
  if(["takeoff","airborne","completed"].includes(c.stage))return 5;
  if(["taxiing","holding_short"].includes(c.stage))return 4;
  if(c.stage==="pushback")return 3;
  if(c.passenger_stage==="boarding"||c.passenger_stage==="boarded")return 2;
  if(c.passenger_stage==="transferring"||c.passenger_stage==="waiting")return 1;
  return 0;
}
export function journeyStatus(c:ConnectingDeparture,language:"zh"|"en") {
  const zh=language==="zh";
  if(c.stage==="blocked")return c.blocked_reason??(zh?"出发已暂停，等待处理问题":"Departure held");
  if(c.stage==="cancelled")return zh?"到达航班改去其他机场，本次转机未完成":"Inbound diverted; connection cancelled";
  const out:Partial<Record<ConnectingDeparture["stage"],string>>=zh?{
    turnaround:"下一班飞机正在下客并准备出发，旅客在候机区等待",
    boarding:"旅客从候机区步行到登机口，登上 "+c.flight_id,
    pushback:"全部旅客已上机；拖车把飞机缓慢移出停机位",
    taxiing:"飞机沿地面通道驶向 "+c.runway+" 跑道入口",
    holding_short:"飞机停在跑道外，等待确认前方安全、可以起飞",
    takeoff:"跑道已空出，飞机正在加速起飞",
    airborne:"飞机已离地，正在飞离机场",
    completed:"下一班飞机已起飞；这 "+c.passenger_count+" 名转机旅客完成行程",
  }:{
    turnaround:"Same airframe turning around; passengers wait in the lounge",
    boarding:"Passengers walk to the gate and board "+c.flight_id,
    pushback:"Boarding complete, doors closed; pushing back",
    taxiing:"Taxiing to the "+c.runway+" holding point",
    holding_short:"Holding outside the runway for clearance",
    takeoff:"Runway open and clear; takeoff roll",
    airborne:"Airborne and climbing out",
    completed:"Connecting flight departed; "+c.passenger_count+" illustrative passengers complete",
  };
  if(out[c.stage] && c.stage!=="turnaround")return out[c.stage]!;
  const pax=zh?{
    onboard:"等待 "+c.source_flight_id+" 落地到位",
    disembarking:"旅客走出 "+c.source_flight_id+"，沿连接通道进入候机大厅",
    transferring:"旅客沿大厅通道走到候机区，准备换乘 "+c.flight_id+"",
    waiting:c.carrier_flight_id?out.turnaround!:"旅客已到候机区，等待 "+c.flight_id+" 的飞机停好",
    boarding:"正在登机",boarded:"已登机",
  }:{
    onboard:"Waiting for "+c.source_flight_id+" to reach its gate",
    disembarking:"Leaving "+c.source_flight_id+" through the concourse",
    transferring:"Walking through the terminal to the transfer lounge",
    waiting:out.turnaround??"Waiting in the lounge for the connecting aircraft",
    boarding:"Boarding",boarded:"On board",
  };
  return pax[c.passenger_stage];
}

/** One plain-language reason for the current wait or movement. */
export function journeyRule(c:ConnectingDeparture,language:"zh"|"en") {
 const zh=language==="zh";
 if(c.stage==="completed")return zh?c.passenger_count+" 人已换乘 "+c.flight_id+" 起飞":c.passenger_count+" passengers departed on "+c.flight_id;
 if(["pushback","taxiing","holding_short","takeoff","airborne"].includes(c.stage))
  return zh?"起飞条件：跑道开放，前方飞机已离开。":"Takeoff needs an open runway, clear of the preceding aircraft.";
 if(["boarding","boarded"].includes(c.passenger_stage))
  return zh?"离开条件：旅客全部登机，机门关闭。":"The aircraft leaves its stand after everyone boards and the doors close.";
 if(c.passenger_stage==="onboard")
  return zh?"下机条件：飞机已停到空闲停机位。":"Passengers deplane after the aircraft reaches a free stand.";
 if(c.passenger_stage==="waiting")
  return zh?"登机条件：下一班飞机停好，并开始登机。":"Boarding starts when the next aircraft is parked and ready.";
 return zh?"目标：从 "+c.source_flight_id+" 换乘 "+c.flight_id+"。":"Goal: change from "+c.source_flight_id+" to "+c.flight_id+".";
}
