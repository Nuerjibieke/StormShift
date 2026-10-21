import { BrainCircuit, Users, X, ArrowRight } from "lucide-react";
import { concreteAgentWork, assignmentChange, recordedReview } from "./audienceView";
import { passengerGroups } from "./passengerView";
import { candidateDecisionEvidence } from "./candidateDecisionEvidence";
import { forecastMetric, auditNumber } from "./auditPresentation";
import { planTitleLabel } from "./localization";
import type { AirportMapState } from "./historicalWorld";
import type { ExecutionEvent, Plan, SessionState } from "./types";
type Language="zh"|"en";
export function AudienceBrief({view,session,plan,world,events,minute,language,paused,onClose,onDetails,slots=3}:{
  view:"passengers"|"agents";session:SessionState;plan?:Plan;world:AirportMapState;events:ExecutionEvent[];minute:number;language:Language;
  paused:boolean;onClose:()=>void;onDetails:()=>void;slots?:number;
}){
 const zh=language==="zh", groups=passengerGroups(world,events,minute);
 const scope=new Set(world.execution?.scope_flight_ids??[]);
 const arrived=groups.filter(g=>scope.has(g.flightId)&&g.stage==="terminal").length;
 return <section className="audience-brief" style={{gridColumn:"span "+slots}} aria-label={zh?(view==="agents"?"Agent 做了什么":"旅客简报"):(view==="agents"?"Agent work":"Passenger brief")}>
  <header><strong>{view==="agents"?<BrainCircuit size={17}/>:<Users size={17}/>} {zh?(view==="agents"?"这轮协作做了什么":"旅客 · 只看关键进度"):(view==="agents"?"What changed through collaboration":"Passenger progress")}</strong>
    <span>{paused?(zh?"已暂停":"Paused"):""}</span>
    <button onClick={onDetails}>{zh?"完整记录":"Full record"} <ArrowRight size={12}/></button>
    <button onClick={onClose} aria-label={zh?"返回当前进度":"Back to progress"}><X size={16}/></button></header>
  {view==="agents"?<div className="agent-work-cards">{concreteAgentWork(session,plan,language,events).map((w,i)=><article key={i}>
    <small>{i+1} · {w.actor}</small><b>{w.title}</b><p title={w.detail}>{w.detail}</p>
  </article>)}</div>:<div className="passenger-brief-body">
    <div><b>{groups.reduce((sum,g)=>sum+g.count,0)}</b><span>{zh?"场景转机旅客 · 总人数":"Scenario transfer passengers"}</span></div>
    <div><b>{scope.size?arrived+" / "+scope.size:"—"}</b><span>{zh?"本轮航班已到位":"Scheduled flights at gate"}</span></div>
    <p>{zh?"每个图标代表一班旅客；转机行程按下机、步行、候机、登机推进。":"Each icon represents one flight’s passengers. Follow their transfer through deplaning, walking, waiting and boarding."}</p>
  </div>}
 </section>;
}
export function DecisionSummary({session,plan,language,onTechnical,onObserve,observing,onSelect,events=[]}:{
 session:SessionState;plan?:Plan;language:Language;onTechnical:()=>void;onObserve:(id:string)=>void;observing:boolean;onSelect:(id:string)=>void;events?:ExecutionEvent[];
}){
 const zh=language==="zh", evidence=plan?candidateDecisionEvidence(session,plan.id):undefined;
 const gate=evidence?.gate, diff=evidence?.actionDiff??(plan?recordedReview(session,plan,events)?.diff:undefined);
 const changes=diff?.rows.filter(r=>r.status!=="unchanged")??[];
 const work=concreteAgentWork(session,plan,language,events);
 const audit=session.decision_lab.outcome_audit?.plan_id===plan?.id?session.decision_lab.outcome_audit:undefined;
 return <div className="decision-summary">
  {plan&&<label>{zh?"正在查看":"Reviewing"}<select aria-label={zh?"摘要方案":"Summary plan"} value={plan.id} disabled={observing} onChange={e=>onSelect(e.target.value)}>{session.plans.map(p=><option key={p.id} value={p.id}>{planTitleLabel(p.title,p.profile,language)}</option>)}</select></label>}
  {!plan?<article><h3>{zh?"目前没有可执行方案":"No executable plan"}</h3><p>{session.decision_lab.infeasible?.summary??(zh?"先确认目标并生成候选。":"Confirm a goal and generate candidates.")}</p></article>:<div className="decision-summary-grid">
   <article><small>01</small><h3>{zh?"为什么看这个方案？":"Why consider it?"}</h3>
    <p>{session.intent?.raw_text|| (zh?"按默认的均衡目标生成。":"Generated for the default balanced objective.")}</p>
    <b>{zh?"预计平均延误 ":"Forecast mean delay "}{auditNumber(forecastMetric(plan.report,"mean_delay_min"),1)} {zh?"分钟":"min"}</b>
</article>
   <article><small>02</small><h3>{zh?"具体改了什么？":"What changes?"}</h3>
    <p>{diff?(zh?"未来安排："+changes.length+" 架调整，"+diff.unchanged+" 架不变。":"Future assignments: "+changes.length+" changed; "+diff.unchanged+" unchanged."):work[1]?.title}</p>
    {changes.length?changes.slice(0,2).map(row=><b key={row.flight_id}>{assignmentChange(row,language)}</b>):<b>{work[1]?.detail}</b>}
    <span>{diff?(zh?"保留已完成的航班动作。":"Completed flight actions are preserved."):(zh?"完整航班安排见技术细节。":"Open technical details for the full flight assignments.")}</span></article>
   <article><small>03</small><h3>{zh?"还剩什么风险？":"What risk remains?"}</h3>
    <p>{work[2]?.title}</p><b>{zh?"预计错过转机 ":"Expected missed connections "}{auditNumber(forecastMetric(plan.report,"missed_connections"),2)} {zh?"人":"pax"}</b>
    <span>{audit?(zh?"本轮评估 "+audit.passengers.decision_scope+" 名旅客。":"Forecast scope: "+audit.passengers.decision_scope+" passengers."):(zh?"多次仿真的平均人数。":"Average passenger count across simulations.")}</span>
    {gate?.status==="needs_evidence"&&gate.required_queries.map(q=><button key={q.query_id} disabled={observing} onClick={()=>onObserve(q.query_id)}>{zh?"补充所需信息":"Acquire required evidence"}</button>)}
    {gate?.status==="blocked"&&<p>{gate.hard_block_reasons[0]??gate.summary}</p>}
   </article>
  </div>}
  <footer><span>{zh?"Agent 提供依据 → 求解器生成安排 → 你批准。":"Agents provide evidence → solver schedules → you approve."}</span><button onClick={onTechnical}>{zh?"展开技术细节":"Open technical details"}</button></footer>
 </div>;
}
