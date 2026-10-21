import { expect, it } from "vitest";
import { emptyPlaybackBatchLabel } from "./playback";
import type { ExecutionEvent } from "./types";
const event = (type: string) => ({type} as ExecutionEvent);
it("does not call the end of an incremental batch the end of execution", () => {
  expect(emptyPlaybackBatchLabel(event("execution_tick"),"zh")).toContain("等待下个真实里程碑");
  expect(emptyPlaybackBatchLabel(event("clock_advanced"),"en")).toContain("next live milestone");
});
it("distinguishes human approval from confirmed recovery", () => {
  expect(emptyPlaybackBatchLabel(event("automatic_policy_replan_requires_approval"),"zh")).toContain("审核重规划候选");
  expect(emptyPlaybackBatchLabel(event("recovered"),"zh")).toContain("本轮执行已完成");
  expect(emptyPlaybackBatchLabel(event("recovered"),"en")).toContain("Execution complete");
});
