import { describe, expect, it } from "vitest";

import {
  dataBadgeLabel,
  executionEventLabel,
  terminalTransferLabel,
  planTitleLabel,
  runtimeText,
  scenarioLabel,
  weatherLabel,
} from "./localization";

const containsHan = (value: string) => /[\u3400-\u9fff]/u.test(value);

describe("English main-screen localization", () => {
  it("localizes backend scenario, weather, data, and plan labels", () => {
    const labels = [
      scenarioLabel("storm", "强雷暴与单跑道恢复", "en"),
      weatherLabel("强雷暴", 4, "en"),
      dataBadgeLabel("synthetic", "合成数据 · 固定种子", "en"),
      planTitleLabel("不确定性护航", "resilient", "en"),
    ];

    expect(labels).toEqual([
      "Severe Storm & Single-Runway Operations",
      "Severe thunderstorm",
      "Synthetic data · fixed seed",
      "Uncertainty Guard",
    ]);
    expect(labels.some(containsHan)).toBe(false);
  });

  it("translates restored and live story text even after switching language", () => {
    const labels = [
      runtimeText("已恢复保存的演练 · 可查看 Decision Lab 或任务回放", "en"),
      runtimeText("二次故障：TUG-3 在 Taxiway C 失效", "en"),
      runtimeText("Safety Critic 已验证新方案 · 动态航迹已自动切换", "en"),
      runtimeText("回放暂停于 T+18 · 可继续选择后续事件", "en"),
      runtimeText("主动获取跑道开放时间 · 新证据到达后由 CP-SAT 重新求解", "en"),
    ];

    expect(labels.every((item) => item.length > 0 && !containsHan(item))).toBe(true);
    expect(labels[0]).toContain("Saved exercise restored");
    expect(labels[3]).toContain("T+18");
  });

  it("preserves Chinese labels in Chinese mode", () => {
    expect(weatherLabel("强雷暴", 4, "zh")).toBe("强雷暴");
    expect(planTitleLabel("不确定性护航", "resilient", "zh")).toBe("稳妥恢复");
    expect(runtimeText("机场运行稳定 · 恢复完成", "zh")).toBe("机场运行稳定 · 恢复完成");
  });
});


it("labels observed replay milestones without claiming a successful repair", () => {
  expect(executionEventLabel("automatic_policy_replan_requires_approval", "zh")).toBe("重规划等待人工审批");
  expect(executionEventLabel("automatic_policy_replan_failed", "zh")).toBe("策略安全停机");
  expect(executionEventLabel("clock_advanced", "zh")).toBe("时钟推进");
  expect(executionEventLabel("gate_arrived", "en")).toBe("Gate arrived");
  expect(executionEventLabel("unknown_event", "en")).toBe("unknown event");
  expect(runtimeText("只读历史现场 · #20 · T+09:00", "en")).toBe("Read-only historical world · #20 · T+09:00");
});

it("uses a transfer total, not a risk count, on the airport board", () => {
  expect(terminalTransferLabel(428, "zh")).toBe("转机旅客 428");
  expect(terminalTransferLabel(428, "en")).toBe("CONNECTIONS 428");
});
