import { runtimeText } from "./localization";
import { historicalWorld } from "./historicalWorld";
import { candidateDecisionEvidence } from "./candidateDecisionEvidence";
import type { ActionDiff, ActionDiffRow, ExecutionEvent, Plan, RecourseAudit, SessionState } from "./types";
import type { PassengerGroup } from "./passengerView";
type Language = "zh" | "en";
export function recentPassengerCohort(groups: PassengerGroup[], minute: number) {
  return groups.filter(g => g.stage === "terminal" && g.gateMinute !== undefined
    && minute >= g.gateMinute && minute < g.gateMinute + 3)
    .sort((a,b) => (b.gateMinute ?? 0) - (a.gateMinute ?? 0) || a.flightId.localeCompare(b.flightId))[0];
}
export function assignmentChange(row: ActionDiffRow, language: Language) {
  const zh=language==="zh", before=row.before, after=row.after;
  if(!before || !after) return row.flight_id + (after ? (zh?"：加入本轮安排":": added") : (zh?"：移出本轮安排":": removed"));
  const parts:string[]=[];
  if(before.runway!==after.runway)parts.push(before.runway+" → "+after.runway);
  if(before.landing_minute!==after.landing_minute)parts.push("T+"+before.landing_minute+" → T+"+after.landing_minute);
  if(before.gate!==after.gate)parts.push(before.gate+" → "+after.gate);
  return row.flight_id+"："+(parts.join(" · ") || (zh?"安排保持不变":"unchanged"));
}

/** Display-only comparison from recorded approval snapshots. Never changes solver inputs. */
export function recordedReview(session:SessionState,plan:Plan,events:ExecutionEvent[]) {
  if(!session.execution || !session.plans.some(p=>p.id===plan.id))return;
  const log=events.filter(e=>e.session_id===session.id).sort((a,b)=>a.sequence-b.sequence);
  const approvals=log.filter(e=>e.type==="plan_approved");
  const own=approvals.filter(e=>e.payload.plan_id===plan.id).at(-1);
  if(!own && !["awaiting_approval","replanning","infeasible"].includes(session.phase))return;
  const source=approvals.filter(e=>e.sequence<(own?.sequence??Infinity)).at(-1);
  if(!source)return;
  const sourceWorld=historicalWorld(source);
  const before=sourceWorld?.plans.find(p=>p.id===source.payload.plan_id);
  const boundary=own??log.at(-1);
  const world=boundary?historicalWorld(boundary):undefined;
  if(!before||!world?.execution)return;
  const committed=new Set(world.execution.committed_operations.map(o=>o.flight_id));
  const previous=new Map(before.assignments.filter(a=>!committed.has(a.flight_id)).map(a=>[a.flight_id,a]));
  const next=new Map(plan.assignments.map(a=>[a.flight_id,a]));
  const rows:ActionDiffRow[]=[];
  for(const id of new Set([...previous.keys(),...next.keys()])){
    if(committed.has(id))continue;
    const a=previous.get(id)??null,b=next.get(id)??null;
    const fields=a&&b?(["runway","landing_minute","gate"] as const).filter(k=>a[k]!==b[k]):[];
    rows.push({flight_id:id,before:a,after:b,changed_fields:fields,status:!a?"added":!b?"removed":fields.length?"changed":"unchanged"});
  }
  if(!rows.length)return;
  const diff:ActionDiff={source_plan_id:before.id,replacement_plan_id:plan.id,
    changed:rows.filter(r=>r.status==="changed").length,unchanged:rows.filter(r=>r.status==="unchanged").length,
    added:rows.filter(r=>r.status==="added").length,removed:rows.filter(r=>r.status==="removed").length,
    rows,scope:"display comparison with recorded approved schedule",
    committed_count_at_replan:committed.size,replan_minute:world.scenario.current_minute};
  const impactEvent=log.filter(e=>e.sequence>source.sequence&&e.sequence<=(own?.sequence??Infinity)
    && e.type.includes("replan")&&e.payload.recourse).at(-1);
  return {diff,impact:impactEvent?.payload.recourse as RecourseAudit|undefined};
}

export type AgentWork = {actor:string; title:string; detail:string};
export function concreteAgentWork(session:SessionState, plan:Plan|undefined, language:Language,events:ExecutionEvent[]=[]):AgentWork[] {
  const zh=language==="zh";
  if(!plan)return [{actor:zh?"Agent":"Agents",title:zh?"还没有生成方案":"No plan yet",detail:zh?"确认你的目标后，助手会开始分析。":"Confirm a goal and start analysis first."}];
  const {recourse,actionDiff:boundDiff,arbitration,gate}=candidateDecisionEvidence(session,plan.id);
  const recorded=boundDiff?undefined:recordedReview(session,plan,events);
  const actionDiff=boundDiff??recorded?.diff;
  const impact=recourse??recorded?.impact;
  const weather=arbitration?.checks.find(c=>c.producer==="Weather Agent");
  const runwayWindow=plan.risk_shield?.runway_clearance?.windows?.find(w=>w.runway==="07R");
  const weatherDetail=weather?.evidence.includes("未承载")
    ? (zh?"这份方案不使用 07R，航班安排不依赖它开放。":"This plan does not use 07R.")
    : runwayWindow?.point_ready_minute!=null
      ? (zh?"07R 预计 T+"+runwayWindow.point_ready_minute+" 开放；本方案首次使用 T+"+runwayWindow.first_planned_use_minute
        :"07R ETA T+"+runwayWindow.point_ready_minute+"; first planned use T+"+runwayWindow.first_planned_use_minute)
      : weather?.evidence ?? "";
  const finding:AgentWork=impact ? {
    actor:zh?"影响分析 Agent":"Impact agent",
    title:zh?(recourse?"":"故障识别时：")+impact.trigger_resource+" 影响 "+impact.affected_count+" 架航班":impact.trigger_resource+" affected "+impact.affected_count+" flights",
    detail:(impact.affected_flight_ids??[]).join(" · ") + (recourse?(zh?"；其余 "+recourse.frozen_count+" 架锁定原安排":"; "+recourse.frozen_count+" other flights locked"):""),
  } : weather ? {
    actor:zh?"天气 Agent → 跑道 Agent":"Weather → Runway agents",
    title:zh?(weather.status==="needs_evidence"?"发现开放依据不足，要求复核":"检查 07R 能否用于这份方案"):(weather.status==="needs_evidence"?"Requested runway evidence":"Passed runway readiness to scheduling"),
    detail:weatherDetail,
  } : {
    actor:zh?"专业 Agent":"Specialist agents",
    title:zh?"尚无本方案的交接记录":"No handoff record for this plan",
    detail:zh?"选择方案后，可在这里查看它的分析记录。":"Other candidates' conclusions are not substituted.",
  };
  const changes=actionDiff?.rows.filter(r=>r.status!=="unchanged")??[];
  const focus=[...plan.assignments].sort((a,b)=>(session.scenario.flights.find(f=>f.id===b.flight_id)?.connections??0)-(session.scenario.flights.find(f=>f.id===a.flight_id)?.connections??0))[0];
  const solver:AgentWork={
    actor:zh?"排程工具":"Scheduling tool",
    title:actionDiff?(zh?"实际调整 "+changes.length+" 架，"+actionDiff.unchanged+" 架未来安排不变":"Changed "+changes.length+"; preserved "+actionDiff.unchanged):
      (zh?"根据约束排出具体航班安排":"Produced executable flight assignments"),
    detail:changes.length?changes.slice(0,2).map(r=>assignmentChange(r,language)).join("；"):
      focus?focus.flight_id+" → "+focus.runway+" · T+"+focus.landing_minute+" · "+focus.gate:(zh?"没有可执行动作":"No executable actions"),
  };
  const safety:AgentWork={
    actor:zh?"安全 Agent → 你":"Safety agent → You",
    title:gate?.human_approved?(zh?"你已批准这个方案":"You approved this plan"):
      gate?.status==="needs_evidence"?(zh?"证据不齐，暂不能批准":"Evidence missing; approval withheld"):
      gate?.status==="blocked"?(zh?"拦截方案，不执行":"Plan blocked"):
      gate?.status==="human_required"?(zh?"风险未消除，交给你决定":"Remaining risk requires your decision"):
      gate?.can_human_approve?(zh?"检查跑道、机位和燃油，等待你批准":"Checks passed; awaiting your approval"):
      (zh?"审批结论尚未就绪":"Approval check unavailable"),
    detail:runtimeText(gate?.human_approved?(zh?"方案已获你的批准，助手正在跟踪后续变化。":"Agents did not approve on your behalf; keep monitoring."):
      gate?.status==="needs_evidence"?(gate.required_queries[0]?.consequence??gate.summary):
      gate?.status==="blocked"?(gate.hard_block_reasons[0]??gate.summary):
      gate?.risk_disclosure || (zh?"助手整理信息，调度工具安排航班，你确认后开始执行。":"Solver actions must pass safety checks."), language),
  };
  return [finding,solver,safety];
}
export type NextAction = {action:"pause"|"resume"|"observe"|"preview"|"approve"|"review"|"repair"|"results";label:string;hint:string;queryId?:string};
export function audienceNextAction(input:{session:SessionState;plan?:Plan;playing:boolean;paused:boolean;previewSelected:boolean;repairReviewed:boolean},language:Language):NextAction {
  const zh=language==="zh", {session,plan,playing,paused}=input;
  if(playing)return {action:paused?"resume":"pause",label:zh?(paused?"继续演示":"暂停讲解"):(paused?"Resume":"Pause"),hint:zh?(paused?"看完当前说明，再继续。":"飞机正在按批准的方案执行。"):(paused?"Continue after reading.":"Following the approved plan.")};
  if(session.phase==="recovered")return{action:"results",label:zh?"查看恢复结果":"Review results",hint:zh?"执行结束；检查预测和实际执行记录。":"Execution ended; review forecasts and the ledger."};
  const gate=plan?candidateDecisionEvidence(session,plan.id).gate:undefined;
  if(gate?.status==="needs_evidence"&&gate.required_queries[0])return{action:"observe",queryId:gate.required_queries[0].query_id,label:zh?(gate.required_queries[0].producer.includes("Weather")?"补充天气信息":"复核资源状态"):"Check missing evidence",hint:zh?"这项信息缺失，当前方案不能直接执行。":"This evidence is required before execution."};
  if(!gate?.can_human_approve || session.intent?.needs_confirmation || session.intent?.issues?.length)return{action:"review",label:zh?"查看阻碍原因":"Review blockers",hint:zh?"先补齐信息并完成安全检查。":"Resolve evidence or safety issues before approval."};
  if(session.execution && !input.repairReviewed && !gate.human_approved)return{action:"repair",label:zh?"审阅这次调整":"Review the changes",hint:zh?"保留已完成的动作，只调整后续航班；请查看具体变化。":"Review changes; committed operations stay locked."};
  if(!session.execution&&!input.previewSelected)return{action:"preview",label:zh?"预演一下方案":"Preview this plan",hint:zh?"预演可比较方案；批准后开始演示。":"Preview does not execute the plan."};
  return {action:"approve",label:zh?(session.execution?"批准调整并继续":"批准并开始执行"):"Approve and execute",hint:zh?"这是人工审批；点击后才开始执行。":"Your approval starts execution."};
}
