import { describe, expect, it } from "vitest";
import { audienceNextAction, concreteAgentWork, recentPassengerCohort, recordedReview } from "./audienceView";
import type { ExecutionEvent, Plan, SessionState } from "./types";
import type { PassengerGroup } from "./passengerView";
const plan={id:"p",assignments:[{flight_id:"F1",runway:"07L",landing_minute:4,gate:"A1"}]} as Plan;
function state(status="ready"):SessionState {
 return {id:"s",phase:"awaiting_approval",plans:[plan],scenario:{flights:[{id:"F1",connections:20}]},
 intent:{needs_confirmation:false,issues:[]},execution:null,traces:[],decision_lab:{
 approval_gates:{p:{status,can_human_approve:status==="ready"||status==="human_required",required_queries:[],human_approved:false}},
 }} as unknown as SessionState;
}
function next(session:SessionState,extra={}) {
 return audienceNextAction({session,plan,playing:false,paused:false,previewSelected:true,repairReviewed:true,...extra},"zh");
}
describe("audience navigation preserves decision boundaries",()=>{
 it("keeps missing evidence and hard blocks ahead of execution even after preview and review",()=>{
  const s=state("needs_evidence");
  s.decision_lab.approval_gates!.p.required_queries=[{query_id:"runway_readiness",producer:"Weather Agent"}] as never;
  expect(next(s).action).toBe("observe");
  expect(next(s).queryId).toBe("runway_readiness");
  expect(next(state("blocked")).action).toBe("review");
  const pending=state();pending.intent!.needs_confirmation=true;
  expect(next(pending).action).toBe("review");
 });
 it("requires change review before the repaired plan's approval and resumes explicit pauses",()=>{
  const s=state("human_required");s.execution={} as never;
  expect(next(s,{repairReviewed:false}).action).toBe("repair");
  expect(next(s).action).toBe("approve");
  expect(next(s,{playing:true,paused:true}).action).toBe("resume");
  expect(next(s,{playing:true,paused:false}).action).toBe("pause");
  s.phase="recovered";expect(next(s).action).toBe("results");
 });
 it("keeps preview selection distinct from approval",()=>{
  expect(next(state(),{previewSelected:false}).action).toBe("preview");
  expect(next(state()).action).toBe("approve");
 });
});
it("shows only a recent acknowledged cohort, never the accumulated terminal population",()=>{
 const groups:PassengerGroup[]=[
 {flightId:"ground",count:100,gate:"A1",stage:"terminal"},
 {flightId:"old",count:60,gate:"A2",stage:"terminal",gateMinute:4},
 {flightId:"new",count:70,gate:"A3",stage:"terminal",gateMinute:9},
 {flightId:"future",count:80,gate:"A4",stage:"onboard",gateMinute:10},
 ];
 expect(recentPassengerCohort(groups,9)?.flightId).toBe("new");
 expect(recentPassengerCohort(groups,12)).toBeUndefined();
 expect(recentPassengerCohort(groups,2)).toBeUndefined();
 expect(groups.reduce((n,g)=>n+g.count,0)).toBe(310);
});
it("describes the selected candidate's flight changes without borrowing other candidates' conclusions",()=>{
 const s=state();const other={...plan,id:"other"};
 s.plans=[plan,other];
 s.decision_lab.action_diff_by_plan={p:{replacement_plan_id:"p",source_plan_id:"old",changed:1,unchanged:2,rows:[
 {flight_id:"F1",status:"changed",before:{runway:"07L",landing_minute:4,gate:"A1"},after:{runway:"07R",landing_minute:8,gate:"A1"}}
 ]}} as never;
 const selected=concreteAgentWork(s,plan,"zh");
 expect(selected[1].detail).toContain("F1：07L → 07R");
 expect(selected[1].detail).toContain("T+4 → T+8");
 expect(concreteAgentWork(s,other,"zh")[1].title).not.toContain("实际调整");
 expect(concreteAgentWork(s,undefined,"zh")[0].title).toBe("还没有生成方案");
});

function snapshotEvent(sequence:number,type:string,minute:number,planId:string,assignments:Plan["assignments"],committed:string[]=[]){
 return {sequence,session_id:"s",type,sim_minute:minute,payload:{plan_id:planId,world_snapshot:{
  version:"airport-world-v1",status:"captured",session_id:"s",minute,world:{
   id:"s",scenario:{current_minute:minute,runways:[],gates:[],flights:[],weather:{}},
   phase:"awaiting_approval",plans:[{id:planId,assignments}],selected_plan_id:planId,
   execution:{committed_operations:committed.map(flight_id=>({flight_id})),scope_flight_ids:["F1","F2"]},decision_lab:{}
  }
 }}} as unknown as ExecutionEvent;
}
it("rebuilds post-observation changes from approval records and keeps the original committed boundary on reload",()=>{
 const original=[{flight_id:"F1",runway:"07L",landing_minute:4,gate:"A1"},
  {flight_id:"F2",runway:"07L",landing_minute:10,gate:"A2"}] as Plan["assignments"];
 const revised={...plan,id:"new",assignments:[{...original[1],landing_minute:19}]} as Plan;
 const s=state();s.plans=[revised];s.execution={committed_operations:[{flight_id:"F1"},{flight_id:"F2"}]} as never;
 const source=snapshotEvent(1,"plan_approved",0,"original",original);
 const replan=snapshotEvent(2,"plan_generated",9,revised.id,revised.assignments,["F1"]);
 const approval=snapshotEvent(3,"plan_approved",9,revised.id,revised.assignments,["F1"]);
 expect(recordedReview(s,revised,[source,replan])?.diff.changed).toBe(1);
 s.phase="recovered";
 const review=recordedReview(s,revised,[source,replan,approval]);
 expect(review?.diff.committed_count_at_replan).toBe(1);
 expect(review?.diff.rows.map(r=>r.flight_id)).toEqual(["F2"]);
 expect(review?.diff.rows[0].before?.landing_minute).toBe(10);
 expect(review?.diff.rows[0].after?.landing_minute).toBe(19);
 expect(recordedReview(s,revised,[source,replan])).toBeUndefined();
 expect(recordedReview(s,revised,[{...source,session_id:"other"},replan,approval])).toBeUndefined();
});
