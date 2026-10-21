import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { EvidenceReceipt, latestStoryObservation, observationReplacement } from "./EvidenceReceipt";
import type { Plan, SessionState } from "./types";

const observation = {
  query_id:"weather_reopen_nowcast", target:"07R", valid:true, synthetic:true, acquired_minute:0,
  evidence_sequence:6, before:{reopen_minute:24,closure_std_min:7}, observed:{reopen_minute:23,closure_std_min:2.5},
  review_context:{plan_id:"old",profile:"balanced",title:"均衡恢复",gate_status:"needs_evidence",
    required_evidence:[{evidence:"T+24 vs P90 T+33",consequence:"获取观测并重新求解"}]},
};
const plan = {id:"new",profile:"balanced",title:"均衡恢复",evidence_basis:{trusted_state_digest:"new-digest"}} as Plan;
function session(entry: unknown = observation): SessionState {
  return {id:"s",phase:"awaiting_approval",sequence:8,plans:[plan],
    data_snapshot:{active_observations:[entry],resolved_observation_ids:["weather_reopen_nowcast"]},
    decision_lab:{approval_gates:{new:{status:"human_required",can_human_approve:true,
      evidence_binding:{valid:true,trusted_state_digest:"new-digest"},risk_disclosure:"仍有燃油风险",
      required_queries:[],hard_block_reasons:[]}}},
  } as unknown as SessionState;
}
const render = (s=session(), id="new", language: "zh"|"en"="zh") =>
  renderToStaticMarkup(<EvidenceReceipt session={s} selectedPlanId={id} language={language}/>);
describe("evidence acquisition receipt", () => {
  it("shows persisted old query, measured inputs, new candidate and unresolved risks after reload", () => {
    const html=render(JSON.parse(JSON.stringify(session())));
    for (const text of ["缺少证据","T+24","T+23","2.5","T+24 vs P90 T+33","同一策略的新候选","仍有燃油风险","合成演练观测","不是原来的安排","尚未执行"]) expect(html).toContain(text);
    expect(html).not.toContain("undefined");
  });
  it("keeps the same strategy even when the ranking changes", () => {
    const other={...plan,id:"first",profile:"green"};
    expect(observationReplacement({...session(),plans:[other,plan]}, {...plan,id:"old"})?.id).toBe("new");
    expect(observationReplacement({...session(),plans:[other,plan]})?.id).toBe("new");
    expect(observationReplacement({...session(),plans:[other]})?.id).toBe("first");
    expect(observationReplacement({...session(),plans:[]})).toBeUndefined();
  });
  it("does not mistake an observation-only response for successful re-solving", () => {
    const html=render({...session(),phase:"incident",plans:[]});
    expect(html).toContain("观测已保存，尚无绑定新证据的候选");
    expect(html).not.toContain("当前审批门允许人工审批");
  });
  it("never treats invalidated or unresolved observations as authorization", () => {
    const invalid=render(session({...observation,valid:false}));
    expect(invalid).toContain("已被后续事件作废");expect(invalid).not.toContain("当前审批门允许人工审批");
    const unresolved=render({...session(),data_snapshot:{...session().data_snapshot,resolved_observation_ids:[]}});
    expect(unresolved).toContain("尚未被当前证据状态接受");expect(unresolved).not.toContain("当前审批门允许人工审批");
  });
  it("requires a newer replan and a matching evidence digest", () => {
    expect(render({...session(),sequence:6})).toContain("尚无绑定新证据的候选");
    expect(render({...session(),plans:[{...plan,evidence_basis:{...plan.evidence_basis!,trusted_state_digest:"old"}}]})).toContain("尚无绑定新证据的候选");
  });
  it("does not reuse old approval after the operator changes the intent", () => {
    const s={...session(),intent:{needs_confirmation:true,issues:[]}} as unknown as SessionState;
    expect(render(s)).not.toContain("当前审批门允许人工审批");
    expect(render(s)).toContain("尚无绑定新证据的候选");
  });
  it("shows a still-blocked candidate without claiming observation always fixes it", () => {
    const s=session();s.decision_lab.approval_gates!.new={...s.decision_lab.approval_gates!.new,
      status:"blocked",can_human_approve:false,hard_block_reasons:["燃油截止时间冲突"]};
    expect(render(s)).toContain("当前审批门仍未放行");expect(render(s)).toContain("燃油截止时间冲突");
  });
  it("discloses a strategy change instead of attributing its gate to evidence", () => {
    const s=session();s.plans=[{...plan,profile:"green",title:"低碳滑行"}];
    expect(render(s)).toContain("当前查看的是不同策略");
    expect(render(s)).not.toContain("同一策略的新候选");
  });
  it("supports gate observations, missing legacy context and finite values only", () => {
    const html=render(session({...observation,query_id:"critical_gate_readiness",target:"A2",
      review_context:null,before:{available_minute:12},observed:{available_minute:13}}));
    expect(html).toContain("机位预计可用时刻");expect(html).toContain("未记录查询前候选");
    const malformed=render(session({...observation,before:{reopen_minute:Infinity},observed:{reopen_minute:"23"}}));
    expect(malformed).not.toContain("Infinity");expect(malformed).toContain("—");
  });
  it("does not ask to repeat a preview already selected from the same snapshot", () => {
    const html=renderToStaticMarkup(<EvidenceReceipt session={session()} selectedPlanId="new" language="zh" previewReviewed/>);
    expect(html).toContain("已查看当前快照的预演");
    expect(html).not.toContain("仍须重新预演");
    expect(html).toContain("仍需人工最终批准");
  });
  it("uses bilingual disclosure and hides absent or unknown observations", () => {
    expect(render(session(),"new","en")).toContain("Synthetic exercise observation");
    expect(render({...session(),data_snapshot:{}})).toBe("");
    expect(latestStoryObservation(session({query_id:"unknown"}))).toBeUndefined();
  });
});
