import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { IntentPreview } from "./IntentPreview";
import type { Intent } from "./types";

const base: Intent = {
  raw_text: "平均延误不超过22分钟", summary: "平均延误上限 22 分钟",
  weights: {delay: .3, connections: .3, fuel_risk: .2, conflicts: .1, emissions: .1},
  scheduled_constraints: [], needs_confirmation: true, source: "text",
};
function render(intent: Intent, language: "zh" | "en" = "zh", busy = false) {
  return renderToStaticMarkup(<IntentPreview intent={intent} language={language} busy={busy}
    onReject={() => {}} onConfirm={() => {}} onClarify={() => {}} />);
}
const pending: Intent = {
  ...base, version: 3, base_minute: 12, average_delay_cap: 22,
  original_text: "优先保转机，平均延误不超过22分钟；07R预计20分钟后恢复",
  raw_text: "优先保转机，平均延误不超过22分钟；07R开放时间待明确",
  issues: [{code: "forecast", excerpt: "07R开放时间待明确", message: "请明确开放时间"}],
  clarification_questions: [{
    issue_index: 0, target: "clause", question: "请改写当前开放要求", placeholder: "只改写这条子句",
  }],
  clarification_history: [{
    version: 2, issue_index: 0, question: "这是预报还是开放指令？",
    excerpt: "07R预计20分钟后恢复", answer: "需要开放，但时间待明确", replacement: "07R开放时间待明确",
  }],
};
describe("source-bound command preview", () => {
  it("disables approval and displays every unresolved message in legacy records", () => {
    const html = render({...base, issues: [
      {code: "forecast", excerpt: "预计恢复", message: "请确认是否开放"},
      {code: "resource", excerpt: "A99", message: "资源不存在"},
    ]});
    expect(html).toContain('class="confirm-button" disabled=""');
    expect(html).toContain('role="alert"');
    expect(html).toContain("预计恢复");
    expect(html).toContain("A99");
    expect(html).toContain("写清完整要求");
  });
  it("shows original command, clarification history, retained goals and fixed time together", () => {
    const html = render(pending);
    expect(html).toContain(pending.original_text);
    expect(html).toContain(pending.raw_text);
    expect(html).toContain("需要开放，但时间待明确");
    expect(html).toContain("本轮明确为");
    expect(html).toContain("延误上限 ≤ 22 min");
    expect(html).toContain("原草稿时间基准");
    expect(html).toContain("T+12");
    expect(html).toContain("相对时间以此时刻为起点");
    expect(html).toContain("请回答下面的问题");
    expect(html).toContain('class="intent-clarification-form"');
    expect(html).toContain('class="confirm-button" disabled=""');
  });
  it("collapses the complete clarification history by default and counts both languages", () => {
    const history = [...pending.clarification_history!, {
      version: 3, issue_index: 0, question: "何时开放07R？", excerpt: "07R开放时间待明确",
      answer: "暂不开放07R", replacement: "保持07R关闭",
    }];
    for (const language of ["zh", "en"] as const) {
      const html = render({...pending, clarification_history: history}, language);
      const historyTag = html.match(/<details[^>]*class="intent-clarification-history"[^>]*>/)?.[0];
      expect(historyTag).toBeDefined();
      expect(historyTag).not.toMatch(/\bopen(?:=|\s|>)/);
      expect(html).toContain(language === "zh"
        ? "<summary>补充记录（2轮）</summary>"
        : "<summary>Clarification history (2 turns)</summary>");
      for (const item of history) {
        for (const text of [item.question, item.excerpt, item.answer, item.replacement]) {
          expect(html).toContain(text);
        }
      }
    }
  });
  it("renders a separate accessible input for each numeric or clause question", () => {
    const targets = ["average_delay_cap", "minimum_connection_rate", "maximum_missed_connections",
      "maximum_tail_exposure_rate", "clause"] as const;
    const html = render({...pending,
      issues: targets.map((target) => ({code: target, excerpt: target, message: "Please clarify"})),
      clarification_questions: targets.map((target, issue_index) => ({
        issue_index, target, question: "中文问题", placeholder: "中文示例",
      })),
    }, "en");
    expect(html.match(/<form /g)).toHaveLength(5);
    expect(html.match(/<label for=/g)).toHaveLength(5);
    expect(html).toContain("What is the maximum mean delay in minutes?");
    expect(html).toContain("What is the minimum on-time connection rate?");
    expect(html).toContain("For example: 95%");
    expect(html).toContain("For example: 0.5%");
    expect(html).toContain("Rewrite only this clause; other goals are retained");
    expect(html).not.toContain("中文问题");
    expect(html).toContain("Submit clarification");
  });
  it("disables clarification inputs and all review actions while busy", () => {
    const html = render(pending, "en", true);
    expect(html).toMatch(/<input[^>]*disabled=""/);
    expect(html).toContain('<button type="submit" disabled="">Processing…</button>');
    expect(html).toContain('class="confirm-button" disabled=""');
    expect(html.match(/<button[^>]*disabled=""/g)).toHaveLength(3);
  });
  it("still requires human confirmation after all questions are resolved", () => {
    const html = render({...pending, issues: [], clarification_questions: []}, "en");
    expect(html).not.toContain('class="intent-clarification-form"');
    expect(html).not.toContain('disabled=""');
    expect(html).toContain("awaiting your confirmation");
    expect(html).toContain("Clarification history");
  });
  it("keeps hard safety checks explicit without treating soft constraints as safe", () => {
    const html = render({...base, scheduled_constraints: [
      {kind: "open_resource", resource_id: "07R", minute: 32, hard: false},
    ]}, "en");
    expect(html).toContain("T+32 Open 07R");
    expect(html).toContain("Planning checks adequate fuel, safe aircraft spacing and available parking stands");
    expect(html).toContain("available parking stands");
    expect(html).not.toContain("Safety passed");
  });
  it("shows evidence and inherited constraints without hiding decimal thresholds", () => {
    const html = render({...base, base_minute: 12, maximum_tail_exposure_rate: .005,
      inherited_fields: ["average_delay_cap"],
      evidence: [{field: "average_delay_cap", excerpt: base.raw_text}]});
    expect(html).toContain("T+12");
    expect(html).toContain("沿用已确认要求");
    expect(html).toContain("不超过22分钟");
    expect(html).toContain("0.5%");
    expect(html).not.toContain('disabled=""');
  });
  it("requires a full replacement for issues that cannot be localized to one clause", () => {
    const html = render({...pending, clarification_questions: []});
    expect(html).not.toContain('class="intent-clarification-form"');
    expect(html).toContain("请在输入框里写清完整要求");
    expect(html).toContain('class="confirm-button" disabled=""');
  });
  it("marks non-localized issues even when another issue has a clarification input", () => {
    const html = render({...pending, issues: [...pending.issues!,
      {code: "resource_window", excerpt: "multiple clauses", message: "Conflicting resource windows"},
    ]}, "en");
    expect(html.match(/<form /g)).toHaveLength(1);
    expect(html).toContain("Rewrite the full command in the main input");
    expect(html).toContain("Conflicting resource windows");
  });
  it("supports legacy records without metadata and translated UI labels", () => {
    expect(render(base, "en")).not.toContain("undefined");
    expect(render(base, "en")).toContain("Not recorded");
    expect(render({...base, notes: ["No new action."]}, "en")).toContain("Source evidence");
  });
});
