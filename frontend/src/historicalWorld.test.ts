import { expect, it } from "vitest";
import { historicalWorld, historicalWorldAt, hasHistoricalWorlds, presentedWorld, type AirportMapState } from "./historicalWorld";
import type { ExecutionEvent, SessionState } from "./types";
function event(sequence:number, minute:number, runwayStatus="closed"):ExecutionEvent {
  const world = {id:"s",phase:"executing",selected_plan_id:null,plans:[],execution:null,decision_lab:{},
    scenario:{current_minute:minute,runways:[{id:"07R",status:runwayStatus}],gates:[],flights:[],weather:{severity:4}}} as unknown as AirportMapState;
  return {session_id:"s",sequence,sim_minute:minute,type:"clock_advanced",created_at:"",
    payload:{world_snapshot:{version:"airport-world-v1",status:"captured",session_id:"s",minute,world}}};
}
it("selects the historical closed runway rather than final open state",()=>{
  const history=[event(1,0),event(2,24,"open")];
  expect(historicalWorldAt("s",history,0)?.scenario.runways[0].status).toBe("closed");
  expect(historicalWorldAt("s",history,24)?.scenario.runways[0].status).toBe("open");
});
it("uses the revealed sequence prefix, including distinct events in the same minute",()=>{
  const before=event(1,9),after=event(2,9,"open");
  expect(historicalWorldAt("s",[before],9)?.scenario.runways[0].status).toBe("closed");
  expect(historicalWorldAt("s",[after,before],9)?.scenario.runways[0].status).toBe("open");
});
it("does not fill legacy gaps or unavailable evidence with another world",()=>{
  const missing={...event(2,9),payload:{}};
  expect(hasHistoricalWorlds([event(1,0),missing])).toBe(false);
  expect(historicalWorldAt("s",[event(1,0),missing],9)).toBeUndefined();
  expect(historicalWorldAt("other",[event(1,0)],9)).toBeUndefined();
});
it("rejects unsupported versions, source identity/time mismatches and malformed shape",()=>{
  const original=event(1,9);
  for(const change of [{version:"future-v2"},{session_id:"other"},{minute:8},{status:"unavailable"},{world:{id:"s"}}]){
    expect(historicalWorld({...original,payload:{world_snapshot:{...(original.payload.world_snapshot as object),...change}}})).toBeUndefined();
  }
});
it("does not change recorded history while seeking backwards and forwards",()=>{
  const history=[event(1,0),event(2,24,"open")],before=JSON.stringify(history);
  for(const minute of [0,25,4,24,0]) historicalWorldAt("s",history,minute);
  expect(JSON.stringify(history)).toBe(before);
  expect(hasHistoricalWorlds(history)).toBe(true);
});

it("live playback reveals reopening before landing, without leaking later gate arrival",()=>{
  const before=event(39,22), opened=event(40,23,"open"), landed=event(41,23,"open"), arrived=event(42,27,"open");
  opened.type="runway_reopened"; landed.type="execution_tick"; arrived.type="gate_arrival";
  historicalWorld(landed)!.execution={scope_flight_ids:["CX551"],committed_operations:[
    {flight_id:"CX551",runway:"07R",gate:"A3",landing_minute:23,stage:"taxiing"}
  ]} as AirportMapState["execution"];
  historicalWorld(arrived)!.execution={scope_flight_ids:["CX551"],committed_operations:[
    {flight_id:"CX551",runway:"07R",gate:"A3",landing_minute:23,stage:"at_gate"}
  ]} as AirportMapState["execution"];
  const final=historicalWorld(arrived)! as SessionState;
  expect(presentedWorld(final,[before],23,false,true)?.scenario.runways[0].status).toBe("closed");
  expect(presentedWorld(final,[before,opened],23,false,true)?.execution).toBeNull();
  const current=presentedWorld(final,[before,opened,landed],23,false,true)!;
  expect(current.scenario.runways[0].status).toBe("open");
  expect(current.execution?.committed_operations[0].stage).toBe("taxiing");
  expect(presentedWorld(final,[before,opened,landed,arrived],23,false,true)?.execution?.committed_operations[0].stage).toBe("taxiing");
  expect(presentedWorld(final,[before,opened,landed,arrived],27,false,true)?.execution?.committed_operations[0].stage).toBe("at_gate");
});
it("idle candidate review keeps current state but a missing playback snapshot cannot expose final state",()=>{
  const final=historicalWorld(event(3,27,"open"))! as SessionState;
  const missing={...event(2,23),payload:{}};
  expect(presentedWorld(final,[event(1,22)],27,false,false)).toBe(final);
  expect(presentedWorld(final,[],0,false,true)).toBe(final);
  expect(presentedWorld(final,[event(1,22),missing],23,false,true)).toBeUndefined();
  expect(presentedWorld(final,[],0,true,false)).toBeUndefined();
});
