import { describe, expect, it } from "vitest";
import type { Plan } from "./types";
import { selectVisualPlan } from "./visualPlan";

const plans = [
  { id: "recommended" },
  { id: "low-carbon" },
  { id: "passenger" },
] as Plan[];

describe("selectVisualPlan", () => {
  it("uses the locally selected comparison candidate before the server approval", () => {
    expect(selectVisualPlan(plans, "low-carbon", "recommended")?.id).toBe("low-carbon");
  });

  it("falls back to the approved server plan and then the first candidate", () => {
    expect(selectVisualPlan(plans, undefined, "passenger")?.id).toBe("passenger");
    expect(selectVisualPlan(plans, undefined, null)?.id).toBe("recommended");
  });
});
