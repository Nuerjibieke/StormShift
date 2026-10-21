import {PlanSchedule} from "./PlanSchedule";
import {useState} from "react";
import {forecastMetric} from "./auditPresentation";
import {planTitleLabel} from "./localization";
import {solverHandoff} from "./solverHandoff";
import type {AgentTrace,SessionState} from "./types";
import "./solverPipeline.css";
export function SolverPipeline({session,planId,language,trace}:{
 session:SessionState;planId?:string;language:"zh"|"en";trace?:AgentTrace;
}){
 const zh=language==="zh",[focus,setFocus]=useState("");
 const d=solverHandoff(session,planId??"",focus);
 if(!d)return <section className="solver-pipeline"><p>{zh?"选择一个候选方案，查看算法输入与输出。":"Choose a candidate to inspect its inputs and outputs."}</p></section>;
 const {plan,assignment:a}=d;
 const minute=(v:unknown)=>typeof v==="number"?"T+"+v:"—";
 const row=(name:string,value:string|number)=><div><dt>{name}</dt><dd>{value}</dd></div>;
 return <section className="solver-pipeline" aria-label={zh?"算法输入与输出":"Algorithm inputs and outputs"}>
  <nav className="pipeline-stages" aria-label={zh?"决策处理流程":"Decision pipeline"}>{(zh?
   ["目标与现场","Agent 核对","CP-SAT 排航班","SimPy 评估","审查与批准"]:
   ["Goal & airport","Agent checks","CP-SAT schedule","SimPy evaluation","Review & approve"]).map((s,i)=><span key={s}><b>{i+1}</b>{s}{i<4&&<i>→</i>}</span>)}</nav>
  <header><div><strong>{zh?"传统算法具体收到了什么、算出了什么？":"What did the optimizer receive and produce?"}</strong>
   <small>{planTitleLabel(plan.title,plan.profile,language)} · {zh?"输入时刻":"Input time"} {minute(d.trusted?.snapshot_minute??d.ledger?.generated_minute)}</small></div>
   {a&&<label>{zh?"跟随航班":"Follow flight"}<select aria-label={zh?"输入输出航班":"Input/output flight"} value={a.flight_id} onChange={e=>setFocus(e.target.value)}>
    {plan.assignments.map(r=><option key={r.flight_id} value={r.flight_id}>{r.flight_id}</option>)}</select></label>}</header>
  <div className="pipeline-io-grid">
   <article className="pipeline-input"><small>01 · {zh?"输入给求解器":"OPTIMIZER INPUT"}</small><h3>{zh?"机场数据与限制":"Airport facts & limits"}</h3>
    <dl>{row(zh?"原计划到达":"Scheduled arrival",minute(d.scheduled))}
     {row(zh?"燃油最晚落地":"Fuel landing deadline",minute(d.fuelDeadline))}
     {d.runways.map(r=><div key={r.id}><dt>{r.id} {zh?"跑道":"runway"}</dt><dd>{r.status==="open"?(zh?"可用":"Open"):r.status==="closed"?(zh?"关闭；预计 ":"Closed; expected ")+minute(r.reopen):r.status}</dd></div>)}
     {a&&row(a.gate+(zh?" 最早可停靠":" earliest stand use"),minute(d.gateAvailable))}
    </dl>{!d.trusted&&<p>{zh?"输入快照待补充。":"Input snapshot pending."}</p>}
   </article>
   <article className="pipeline-output"><small>02 · OR-TOOLS CP-SAT</small><h3>{zh?"输出：一行航班安排":"Output: one schedule row"}</h3>
    {a?<><b className="pipeline-flight">{a.flight_id}</b><dl>
     {row(zh?"使用跑道":"Runway",a.runway)}{row(zh?"落地时刻":"Landing",minute(a.landing_minute))}
     {row(zh?"停机位置":"Stand",a.gate)}{row(zh?"空中等待":"Airborne hold",a.hold_minutes+(zh?" 分钟":" min"))}
    </dl></>:<p>{zh?"本候选无需新增进港安排。":"No additional arrival assignments."}</p>}
   </article>
   <article className="pipeline-simulation"><small>03 · SIMPY × {plan.report.runs}</small><h3>{zh?"再评估：这样安排会怎样":"Evaluate the schedule"}</h3><dl>
    {row(zh?"全方案平均延误":"Plan mean delay",forecastMetric(plan.report,"mean_delay_min").toFixed(2)+(zh?" 分钟":" min"))}
    {row(zh?"全方案预计误机":"Plan expected missed connections",forecastMetric(plan.report,"missed_connections").toFixed(2)+(zh?" 人":" pax"))}
    {a&&plan.report.per_flight?.[a.flight_id]&&row(a.flight_id+(zh?" 预计误机":" expected missed"),Number(plan.report.per_flight[a.flight_id].expected_missed_passengers??0).toFixed(2)+(zh?" 人":" pax"))}
   </dl><p>{zh?"先由 CP-SAT 排出时间和位置，再用仿真计算延误与旅客影响。":"CP-SAT selects times and resources; simulation estimates delays and passenger impact."}</p></article>
  </div>
  <PlanSchedule session={session} planId={plan.id} language={language} selectedFlight={a?.flight_id} onSelectFlight={setFocus}/>
  {trace&&<p className="pipeline-node-summary"><b>{zh?"选中节点":"Selected node"} · {trace.agent}</b>{trace.summary}</p>}
  <details><summary>{zh?"查看本候选记录的输入、输出与仿真字段":"Inspect this candidate’s input, output and simulation fields"}</summary><pre>{JSON.stringify(d.raw,null,2)}</pre></details>
 </section>;
}
