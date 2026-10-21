import {expect,it} from "vitest";
import {renderToStaticMarkup} from "react-dom/server";
import {solverHandoff} from "./solverHandoff";
import {SolverPipeline} from "./SolverPipeline";
import type {SessionState} from "./types";
const make=()=>({plans:[{id:"a",profile:"balanced",title:"均衡恢复",assignments:[{flight_id:"F1",runway:"07L",landing_minute:13,gate:"A1",hold_minutes:3}],evidence_basis:{trusted_state_digest:"old"},report:{runs:50,mean_delay_min:12,missed_connections:2,raw_metrics:{missed_connections:2.5},per_flight:{F1:{expected_missed_passengers:.4}}}}],
 scenario:{current_minute:99},decision_lab:{trusted_state:{digest:"old",snapshot_minute:9,solver_input_values:{"runway.07L.status":"open","gate.A1.available_minute":16},compiled_constraints:[{id:"fuel-deadline:F1",rule:"F1 landing deadline T+52"}]},
 acdm_by_plan:{a:{plan_id:"a",generated_minute:9,records:[{flight_id:"F1",milestones:[{code:"STA",role:"solver_input",minute:10}]}]}}}} as unknown as SessionState);
it("binds inputs and outputs to the same candidate and its planning snapshot",()=>{
 const d=solverHandoff(make(),"a")!;expect(d.scheduled).toBe(10);expect(d.fuelDeadline).toBe(52);
 expect(d.trusted?.snapshot_minute).toBe(9);expect(d.raw.snapshot_minute).not.toBe(99);expect(d.assignment?.landing_minute).toBe(13);
});
it("does not substitute another candidate or current airport facts for missing evidence",()=>{
 const s=make();expect(solverHandoff(s,"missing")).toBeUndefined();
 s.decision_lab.acdm_by_plan!.a.plan_id="b";s.decision_lab.trusted_state!.digest="new";
 const d=solverHandoff(s,"a")!;expect(d.ledger).toBeUndefined();expect(d.trusted).toBeUndefined();expect(d.fuelDeadline).toBeUndefined();
});
it("keeps solver scheduling outputs and simulation passenger estimates in separate cards",()=>{
 const html=renderToStaticMarkup(<SolverPipeline session={make()} planId="a" language="zh"/>);
 for(const text of ["输入给求解器","输出：一行航班安排","再评估","T+13","T+52","2.50","0.40"])expect(html).toContain(text);
 expect(html).not.toContain("T+99");
});
it("supports English and an empty selection without fabricated schedule rows",()=>{
 expect(renderToStaticMarkup(<SolverPipeline session={make()} planId="missing" language="en"/>)).toContain("Choose a candidate");
 expect(renderToStaticMarkup(<SolverPipeline session={make()} planId="a" language="en"/>)).toContain("OPTIMIZER INPUT");
});
