import {renderToStaticMarkup} from "react-dom/server";
import {expect, it} from "vitest";
import {AgentCommunication} from "./AgentCommunication";
import {explainCheck, handoffStory, scheduleRows} from "./communicationPresentation";
import type {AgentContractCheck, SessionState} from "./types";

const participants: Record<string, [string, string]> = {
  weather_to_runway: ["Weather Agent", "Runway Agent"], runway_to_gate: ["Runway Agent", "Gate & Ground Agent"],
  gate_to_passenger: ["Gate & Ground Agent", "Airline & Passenger Agent"], safety_to_coordinator: ["Safety Critic", "Recovery Coordinator"],
};
const check = (id: string, status = "aligned") => ({id, status, producer: participants[id][0], consumer: participants[id][1],
  evidence: "旧文案：承载、覆盖、对齐", contract: "旧文案", consequence: "旧文案", hard_block: false}) as AgentContractCheck;
const make = () => ({
  llm_tier: "fallback", traces: [],
  scenario: {current_minute: 99, gates: [{id: "A2", available_minute: 999}]},
  plans: [{id: "a", title: "均衡恢复", profile: "balanced", evidence_basis: {trusted_state_digest: "old"},
    assignments: [
      {flight_id: "LV412", runway: "07L", landing_minute: 22, taxi_minutes: 6, gate: "A2", hold_minutes: 16},
      {flight_id: "CX551", runway: "07L", landing_minute: 7, taxi_minutes: 4, gate: "A3", hold_minutes: 2}],
    risk_shield: {rows: [], runway_clearance: {windows: [{runway: "07R", point_ready_minute: 24, p90_ready_minute: 33, status: "unused", first_planned_use_minute: null}]}}}],
  decision_lab: {
    trusted_state: {digest: "old", snapshot_minute: 0, solver_input_values: {"runway.07L.status": "open", "gate.A2.available_minute": 28, "gate.A3.available_minute": 0},
      compiled_constraints: [{id: "fuel-deadline:LV412", rule: "LV412 by T+39"}]},
    acdm_by_plan: {a: {plan_id: "a", records: [{flight_id: "LV412", milestones: [{code: "STA", role: "solver_input", minute: 6}]}]}},
    agent_arbitration_by_plan: {a: {checks: [check("weather_to_runway"), check("runway_to_gate")]}},
  },
}) as unknown as SessionState;

it("explains an unused closed runway without implying any flight lands on it", () => {
  const text = explainCheck(make(), "a", check("weather_to_runway"), "zh");
  expect(text.schedule).toContain("2 架飞机都不用 07R");
  expect(text.schedule).toContain("07L");
  expect(text.next).toContain("不需要为 07R");
});
it("shows all candidate flights in landing order and uses the matching historical inputs", () => {
  const s = make(), rows = scheduleRows(s, "a");
  expect(rows.map(r => r.flight_id)).toEqual(["CX551", "LV412"]);
  expect(rows[1]).toMatchObject({gateArrival: 28, gateAvailable: 28, scheduled: 6, fuelDeadline: 39});
  const html = renderToStaticMarkup(<AgentCommunication session={s} planId="a" language="zh"/>);
  expect(html).toContain("LV412"); expect(html).toContain("CX551");
  expect(html).toContain("整份计划：输入与输出"); expect(html).not.toContain("第 999 分钟");
  for (const abstract of ["承载", "覆盖", "对齐"]) expect(html).not.toContain(abstract);
});
it("accepts arrival exactly when a stand becomes available", () => {
  const d = explainCheck(make(), "a", check("runway_to_gate"), "zh", "LV412");
  expect(d.caution).toBe(false); expect(d.schedule).toContain("第 28 分钟到 A2");
  expect(d.problem).toContain("时间检查通过");
});
it("highlights an early arrival even while inspecting another flight", () => {
  const s = make(); s.decision_lab.trusted_state!.solver_input_values["gate.A2.available_minute"] = 31;
  const first = explainCheck(s, "a", check("runway_to_gate", "blocked"), "zh");
  expect(first.flightId).toBe("LV412"); expect(first.problem).toContain("早到 3 分钟");
  const other = explainCheck(s, "a", check("runway_to_gate", "blocked"), "zh", "CX551");
  expect(other.next).toContain("LV412 到 A2"); expect(other.caution).toBe(true);
});
it("never replaces missing candidate inputs with live airport facts", () => {
  const s = make(); s.decision_lab.trusted_state!.digest = "new";
  const d = explainCheck(s, "a", check("runway_to_gate"), "zh", "LV412");
  expect(d.caution).toBe(true); expect(d.input).toContain("未记录");
  expect(d.input).not.toContain("999"); expect(d.problem).not.toContain("通过");
});
it("shows the first landing and remaining runway risk after a weather query", () => {
  const s = make(); s.plans[0].assignments[0].runway = "07R"; s.plans[0].assignments[0].landing_minute = 26;
  const d = explainCheck(s, "a", check("weather_to_runway", "resolved_watch"), "zh");
  expect(d.flightId).toBe("LV412"); expect(d.problem).toContain("早 7 分钟");
  expect(d.next).toContain("查询已经完成"); expect(d.next).toContain("仍要确认跑道已开放");
  expect(d.caution).toBe(true);
});
it("does not substitute the recommended plan's checks for another candidate", () => {
  const s = make(); s.plans.push({...s.plans[0], id: "b", assignments: [{...s.plans[0].assignments[0], flight_id: "OTHER"}]});
  const html = renderToStaticMarkup(<AgentCommunication session={s} planId="b" language="zh"/>);
  expect(html).toContain("当前没有这份方案的检查记录"); expect(html).toContain("OTHER"); expect(html).not.toContain("CX551");
});
it("does not treat a passed safety check as permission when other approval requirements are pending", () => {
  const s = make(); s.decision_lab.approval_gates = {a: {status: "needs_evidence", can_human_approve: false}} as unknown as SessionState["decision_lab"]["approval_gates"];
  const d = explainCheck(s, "a", check("safety_to_coordinator"), "zh");
  expect(d.problem).toContain("暂时不能批准"); expect(d.caution).toBe(true);
});
it("renders English and missing-candidate states without invented assignments", () => {
  expect(renderToStaticMarkup(<AgentCommunication session={make()} planId="a" language="en"/>)).toContain("Complete plan: inputs and outputs");
  const html = renderToStaticMarkup(<AgentCommunication session={make()} planId="missing" language="zh"/>);
  expect(html).toContain("生成方案后"); expect(html).not.toContain("LV412");
});

it("does not request approval again when the recorded plan is already approved", () => {
  const s = make();
  s.decision_lab.approval_gates = {a: {status: "ready", can_human_approve: true, human_approved: true}} as unknown as SessionState["decision_lab"]["approval_gates"];
  const d = explainCheck(s, "a", check("safety_to_coordinator"), "zh");
  expect(d.input).toContain("已有人确认批准");
  expect(d.next).toContain("查看机场现场或回放");
  expect(d.next).not.toContain("批准按钮");
});

const passengerExample = () => {
  const s = make();
  s.plans[0].risk_shield!.rows = [{
    flight_id: "CX551", fuel_level: "clear", eldt_point: 7, eldt_upper_90: 13.4,
    fuel_reserve_deadline: 37, fuel_slack_min: 23.6, fuel_risk_probability: 0,
    transfer_passengers: 74, transfer_buffer_min: 32, upper_gate_delay_min: 13.6, transfer_exposed: false,
  }];
  s.decision_lab.agent_arbitration_by_plan!.a.checks = [check("gate_to_passenger")];
  s.decision_lab.approval_gates = {a: {status: "ready", can_human_approve: true, human_approved: false}} as unknown as SessionState["decision_lab"]["approval_gates"];
  return s;
};

it("keeps ground information separate from the recipient's passenger data and computes the remaining buffer", () => {
  const story = handoffStory(passengerExample(), "a", check("gate_to_passenger"), "zh");
  expect(story.message).toContain("CX551 计划第 11 分钟到 A3");
  expect(story.message).toContain("13.6");
  expect(story.message).not.toContain("74");
  expect(story.recipientBasis).toContain("74 名");
  expect(story.recipientBasis).toContain("32 分钟");
  expect(story.comparison).toContain("13.6 分钟 ≤ 允许晚 32 分钟");
  expect(story.comparison).toContain("18.4 分钟余量");
});
it("keeps a required query as a concrete action within the recipient response", () => {
  const s = passengerExample();
  s.decision_lab.agent_arbitration_by_plan!.a.checks = [check("gate_to_passenger", "needs_evidence")];
  const html = renderToStaticMarkup(<AgentCommunication session={s} planId="a" language="zh"/>);
  expect(html).toContain("查询 A3");
  expect(html).toContain("已记录：需要查询");
  expect(html).not.toContain("查询已经完成");
});
it("shows only the sender content and recipient reasoning, without repeated status or approval cards", () => {
  const html = renderToStaticMarkup(<AgentCommunication session={passengerExample()} planId="a" language="zh"/>);
  expect(html).toContain("地勤助手交给旅客助手的信息");
  expect(html).toContain("旅客助手收到后，怎样判断");
  expect(html).toContain("18.4 分钟余量");
  expect(html.match(/class="handoff-message-step"/g)).toHaveLength(2);
  for (const phrase of ["所以，程序已经记录了什么结果", "已发生 · 保存的检查结果", "整份方案进展", "接下来要做", "待办 · 这条检查的下一步"]) expect(html).not.toContain(phrase);
});
it("keeps the safety handoff separate from a later human approval", () => {
  const s = passengerExample();
  s.decision_lab.approval_gates!.a.human_approved = true;
  const story = handoffStory(s, "a", check("safety_to_coordinator", "human_required"), "zh");
  expect(story.message).not.toContain("已有人确认批准");
  expect(story.recipientBasis).toContain("有没有人工批准记录");
});
