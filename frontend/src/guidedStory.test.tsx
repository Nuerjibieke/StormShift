import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { deriveGuidedStory, storyActions, storyReviewKey } from "./guidedStory";
import { StoryGuideContent } from "./GuidedStory";
import { guidedStoryCopy } from "./guidedStoryCopy";
import { forecastMetric, auditPassengerMetric } from "./auditPresentation";
import type { ExecutionEvent, SessionState } from "./types";
const base = {id:"s",mode:"guided",phase:"incident",intent:null,plans:[],sequence:2,updated_at:"now",execution:null,
  scenario:{flights:[{id:"F1",connections:20,state:"holding"},{id:"F2",connections:90,state:"at_gate"}]},
  decision_lab:{}} as unknown as unknown as SessionState;
function state(session=base, extra: Partial<Parameters<typeof deriveGuidedStory>[0]>={}) {
  return deriveGuidedStory({session,phase:session.phase,minute:0,playing:false,revealedEvents:[],
    introAcknowledged:false,selectedPlanId:"p",reviewKey:"",...extra});
}
function planned(allowed=true) {
  return {...base,phase:"awaiting_approval",plans:[{id:"p",assignments:[{flight_id:"F1"}]}],
    decision_lab:{approval_gates:{p:{status:allowed?"ready":"needs_evidence",can_human_approve:allowed}}}} as unknown as SessionState;
}
const event=(id:string,minute:number,type:string):ExecutionEvent=>({
  session_id:id,sim_minute:minute,type,sequence:1,payload:{},created_at:"",
});
describe("live six-act guide",()=>{
  it("starts at the scene and focuses an uncommitted connecting flight",()=>{
    expect(state().stage).toBe(1);expect(state().focusFlight).toBe("F1");
    expect(state(base,{introAcknowledged:true}).stage).toBe(2);
  });
  it("requires intent review before planning and never equates it with flight approval",()=>{
    const intent={raw_text:"protect connections",needs_confirmation:true,issues:[]};
    const pending={...planned(),intent} as unknown as SessionState;
    expect(state(pending).stage).toBe(2);expect(state(pending).canApprove).toBe(false);
    expect(storyActions(state(pending),{playing:false,paused:false,canResume:false,needsApproval:false})).toEqual(["intent"]);
    const confirmed={...base,intent:{...intent,needs_confirmation:false}} as unknown as SessionState;
    expect(state(confirmed).intentConfirmed).toBe(true);expect(state(confirmed).canApprove).toBe(false);
  });
  it("moves from rehearsal to approval only for a selected fresh preview and permitted candidate",()=>{
    const current=planned();
    expect(state(current).stage).toBe(3);
    expect(state(current,{reviewKey:storyReviewKey(current,"p")}).stage).toBe(4);
    expect(state(planned(false),{reviewKey:storyReviewKey(planned(false),"p")}).stage).toBe(3);
  });
  it("invalidates preview navigation after source updates or candidate changes",()=>{
    const current=planned(),reviewKey=storyReviewKey(current,"p");
    expect(state({...current,sequence:3},{reviewKey}).stage).toBe(3);
    expect(state({...current,updated_at:"later"},{reviewKey}).stage).toBe(3);
    expect(state(current,{reviewKey,selectedPlanId:"missing"}).stage).toBe(3);
  });
  it("does not invent permission when approval evidence is absent or the intent has issues",()=>{
    expect(state({...planned(),decision_lab:{}}).canApprove).toBe(false);
    const invalid={...planned(),intent:{raw_text:"ambiguous",needs_confirmation:false,issues:[{code:"unknown"}]}} as unknown as SessionState;
    expect(state(invalid).canApprove).toBe(false);
  });
  it("uses visible phase and revealed event prefix rather than future final-session state",()=>{
    const future={...planned(),phase:"recovered"} as unknown as SessionState;
    const view=state(future,{phase:"executing",playing:true,minute:8,revealedEvents:[
      event("s",9,"ground_vehicle_failure"),event("another",8,"ground_vehicle_failure")]});
    expect(view.stage).toBe(5);expect(view.faultVisible).toBe(false);
    expect(state(future,{phase:"replanning",minute:9,playing:true,revealedEvents:[event("s",9,"ground_vehicle_failure")]}).faultVisible).toBe(true);
  });
  it("keeps blocked post-fault execution in the recovery chapter, not a fresh scenario",()=>{
    const repairing={...planned(false),execution:{status:"blocked"},phase:"awaiting_approval"} as unknown as SessionState;
    expect(state(repairing).stage).toBe(5);
    expect(state(repairing,{phase:"analyzing"}).stage).toBe(5);
    expect(storyActions(state(repairing),{playing:false,paused:false,canResume:false,needsApproval:true})).toEqual(["approve","lab"]);
  });
  it("offers replay and audit at recovery without claiming passenger observations",()=>{
    const done=state({...base,phase:"recovered"});
    expect(done.stage).toBe(6);
    expect(storyActions(done,{playing:false,paused:false,canResume:false,needsApproval:false})).toEqual(["lab","replay"]);
  });
  it("offers resume only for paused or server-resumable execution",()=>{
    const executing=state(base,{phase:"executing",playing:true});
    expect(storyActions(executing,{playing:true,paused:false,canResume:true,needsApproval:false})).toEqual(["lab"]);
    expect(storyActions(executing,{playing:true,paused:true,canResume:true,needsApproval:false})).toEqual(["continue","lab"]);
    expect(storyActions(executing,{playing:false,paused:false,canResume:false,needsApproval:false})).toEqual(["lab"]);
  });
});
describe("readable story content",()=>{
  it("keeps Chinese and English guide keys aligned",()=>{
    expect(Object.keys(guidedStoryCopy.zh).sort()).toEqual(Object.keys(guidedStoryCopy.en).sort());
    expect(Object.keys(guidedStoryCopy.zh.actions).sort()).toEqual(Object.keys(guidedStoryCopy.en.actions).sort());
  });
  const render=(story=state(),extra={})=>renderToStaticMarkup(<StoryGuideContent state={story} language="zh" busy={false}
    playing={false} paused={false} canResume={false} needsApproval={false} fallback={true} onAction={()=>{}} {...extra}/>);
  it("shows six chapters, a focus flight and honest fallback provenance",()=>{
    const html=render();
    expect(html).toContain("六幕主线");expect(html).toContain("F1");
    expect(html).toContain("内置规则助手");expect(html).toContain("选择一个章节");
    expect(html).not.toContain("undefined");
  });
  it("distinguishes an approvable candidate from a preview not yet selected",()=>{
    const html=render(state(planned()));
    expect(html).toContain("先预演方案");expect(html).not.toContain("尚无可用的审批许可");
  });
  it("never places an enabled approval button in a blocked recovery",()=>{
    const s=state({...planned(false),execution:{status:"blocked"}} as unknown as SessionState);
    const html=render(s,{needsApproval:true});
    expect(html).toMatch(/<button disabled="">批准所选方案并执行/);
  });
  it("preserves fractional expected passengers instead of rounding them to zero",()=>{
    const report={missed_connections:0,raw_metrics:{missed_connections:.5}} as unknown as SessionState["plans"][number]["report"];
    expect(forecastMetric(report,"missed_connections")).toBe(.5);
    expect(forecastMetric({...report,raw_metrics:undefined},"missed_connections")).toBe(0);
    expect(forecastMetric({...report,raw_metrics:{missed_connections:NaN}},"missed_connections")).toBe(0);
  });
  it("keeps final forecast counts on the same unrounded scope and supports legacy audits",()=>{
    const passengers={decision_scope:289,predicted_on_time:287,predicted_missed:2,
      expected_on_time_unrounded:287.18,expected_missed_unrounded:1.82} as unknown as Parameters<typeof auditPassengerMetric>[0];
    expect(auditPassengerMetric(passengers,"missed")).toBe(1.82);
    expect(auditPassengerMetric(passengers,"on_time")).toBe(287.18);
    expect(auditPassengerMetric({...passengers,expected_missed_unrounded:undefined},"missed")).toBe(2);
  });
  it("shows the selected candidate, actual risk disclosure, evidence request and full Agent summaries",()=>{
    const plan={id:"p",title:"均衡恢复",profile:"balanced",report:{mean_delay_min:12.3,p90_delay_min:18.2,missed_connections:2.5}};
    const gate={status:"needs_evidence",summary:"缺少跑道观测",risk_disclosure:"预计开放不是已确认开放",
      hard_block_reasons:[],required_queries:[{query_id:"weather_reopen_nowcast",producer:"Weather",consumer:"Runway",evidence:"T+24 vs T+33",consequence:"重新求解"}]};
    const html=render(state(planned(false)),{plan,plans:[plan],gate,traces:[{agent:"Weather",summary:"需要观测"}],onObserve:()=>{}});
    expect(html).toContain("所选候选");expect(html).toContain("12.30");
    expect(html).toContain("预计开放不是已确认开放");expect(html).toContain("T+24 vs T+33");
    expect(html).toContain("获取这项证据并重新规划");expect(html).toContain("完整 Agent 结论接力");
    expect(html).toContain("需要观测");expect(html).not.toContain("批准所选方案并执行");
    const paused=render({...state(planned(false)),stage:5},{plan,plans:[plan],gate,playing:true,paused:true,needsApproval:true,onObserve:()=>{}});
    expect(paused).toMatch(/<button disabled="">获取这项证据并重新规划/);
  });
  it("states forecasts versus execution facts and supports English",()=>{
    const done=state({...base,phase:"recovered"});
    expect(render(done)).toContain("方案预计能帮助多少旅客");
    expect(render(done,{language:"en"})).toContain("Review completed flight actions");
  });
});
