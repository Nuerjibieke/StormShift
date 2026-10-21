import type { SessionState } from "./types";

/** Candidate review never borrows another candidate's decision evidence. */
export function candidateDecisionEvidence(session: SessionState, planId: string) {
  const lab = session.decision_lab;
  const rawDiff = lab.action_diff_by_plan?.[planId] ?? lab.action_diff;
  const actionDiff = rawDiff?.replacement_plan_id === planId ? rawDiff : undefined;
  const rawRecourse = lab.recourse_by_plan?.[planId] ?? lab.recourse;
  const recourse = rawRecourse?.replacement_plan_id === planId
    || (!rawRecourse?.replacement_plan_id && actionDiff)
    ? rawRecourse : undefined;
  const arbitration = lab.agent_arbitration_by_plan?.[planId]
    ?? (!lab.agent_arbitration_by_plan && session.plans[0]?.id === planId
      ? lab.agent_arbitration : undefined);
  const informationValue = lab.information_value?.source_plan_id === planId
    ? lab.information_value : undefined;
  return { actionDiff, recourse, arbitration, informationValue,
    gate: lab.approval_gates?.[planId] };
}
