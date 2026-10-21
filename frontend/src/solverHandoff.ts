import type {SessionState} from "./types";

/** Read a candidate's own ledger. Never substitute today's live airport state. */
export function solverHandoff(session:SessionState,planId:string,flightId?:string) {
 const plan=session.plans.find(p=>p.id===planId);
 if(!plan)return undefined;
 const lab=session.decision_lab;
 const candidateLedger=lab.acdm_by_plan?.[planId]??(lab.acdm?.plan_id===planId?lab.acdm:undefined);
 const ledger=candidateLedger?.plan_id===planId?candidateLedger:undefined;
 const assignment=plan.assignments.find(a=>a.flight_id===flightId)??plan.assignments[0];
 const record=ledger?.records.find(r=>r.flight_id===assignment?.flight_id);
 const trusted=plan.evidence_basis?.trusted_state_digest
   &&lab.trusted_state?.digest===plan.evidence_basis.trusted_state_digest?lab.trusted_state:undefined;
 const constraints=trusted?.compiled_constraints??[];
 const fuelRule=constraints.find(c=>c.id==="fuel-deadline:"+assignment?.flight_id);
 const fuelMatch=String(fuelRule?.rule??"").match(/T\+([\d.]+)/);
 const facts=trusted?.solver_input_values;
 const runways=facts?Object.entries(facts).filter(([key])=>/^runway\.[^.]+\.status$/.test(key)).map(([key,status])=>{
  const id=key.split(".")[1];
  return {id,status:String(status),reopen:facts["runway."+id+".reopen_minute"]};
 }):[];
 return {plan,ledger,assignment,record,trusted,runways,
  scheduled:record?.milestones.find(m=>m.code==="STA"&&m.role==="solver_input")?.minute,
  fuelDeadline:fuelMatch?Number(fuelMatch[1]):undefined,
  gateAvailable:facts?.["gate."+assignment?.gate+".available_minute"],
  derived:record?.milestones.filter(m=>m.role==="derived")??[],
  raw:{plan_id:plan.id,snapshot_minute:trusted?.snapshot_minute??ledger?.generated_minute,
   profile:plan.profile,solver_input_facts:facts,compiled_constraints:trusted?.compiled_constraints,
   scheduled_arrival:record?.milestones.find(m=>m.code==="STA"&&m.role==="solver_input"),
   solver_output:plan.assignments,simulation_report:plan.report},
 };
}
