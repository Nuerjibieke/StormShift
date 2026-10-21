import { expect, it } from "vitest";
import { candidateDecisionEvidence } from "./candidateDecisionEvidence";
import type { SessionState } from "./types";

function fixture(): SessionState {
  return { plans:[{id:"recommended"},{id:"chosen"}], selected_plan_id:"chosen",
    decision_lab:{
      action_diff:{replacement_plan_id:"recommended",changed:1},
      action_diff_by_plan:{
        recommended:{replacement_plan_id:"recommended",changed:1},
        chosen:{replacement_plan_id:"chosen",changed:2,committed_count_at_replan:2},
      },
      recourse:{replacement_plan_id:"recommended",changed_count:1},
      recourse_by_plan:{chosen:{replacement_plan_id:"chosen",changed_count:2}},
      agent_arbitration:{status:"needs_evidence"},
      agent_arbitration_by_plan:{
        recommended:{status:"needs_evidence"},chosen:{status:"human_required"},
      },
      information_value:{source_plan_id:"recommended",recommended_query_id:"critical_gate_readiness"},
      approval_gates:{chosen:{status:"human_required",required_queries:[]}},
    }} as unknown as SessionState;
}
it("selects actual candidate diff and arbitration, never recommendation-only sensing advice",()=>{
  const state=fixture(), evidence=candidateDecisionEvidence(state,"chosen");
  expect(evidence.actionDiff?.changed).toBe(2);
  expect(evidence.actionDiff?.committed_count_at_replan).toBe(2);
  expect(evidence.recourse?.changed_count).toBe(2);
  expect(evidence.arbitration?.status).toBe("human_required");
  expect(evidence.gate?.required_queries).toEqual([]);
  expect(evidence.informationValue).toBeUndefined();
  expect(candidateDecisionEvidence(state,"recommended").informationValue?.recommended_query_id)
    .toBe("critical_gate_readiness");
});
it("does not relabel legacy or mismatched evidence as the chosen plan",()=>{
  const state=fixture();
  delete state.decision_lab.action_diff_by_plan;
  delete state.decision_lab.recourse_by_plan;
  delete state.decision_lab.agent_arbitration_by_plan;
  const evidence=candidateDecisionEvidence(state,"chosen");
  expect(evidence.actionDiff).toBeUndefined();
  expect(evidence.recourse).toBeUndefined();
  expect(evidence.arbitration).toBeUndefined();
  expect(candidateDecisionEvidence(state,"recommended").actionDiff?.changed).toBe(1);
});
it("candidate switching and JSON reload preserve scope and do not mutate the source",()=>{
  const state=JSON.parse(JSON.stringify(fixture())) as SessionState,before=JSON.stringify(state);
  for(const id of ["chosen","recommended","chosen"]) candidateDecisionEvidence(state,id);
  expect(JSON.stringify(state)).toBe(before);
  expect(candidateDecisionEvidence(state,"unknown").actionDiff).toBeUndefined();
});
