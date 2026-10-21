import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { deltaLabel, FalsificationPanel } from "./FalsificationPanel";
import type { FalsificationAnalysis, FalsificationAxis } from "./types";
const axis = {status:"lower_bound",search_limit:45} as FalsificationAxis;
it("never presents a configured cap as a verified searched range",()=>{
  expect(deltaLabel(axis,"zh")).toBe("范围未核实");
  expect(deltaLabel({...axis,target_search_limits:{A1:2,A2:5}},"zh")).toBe("≥2 分钟（共同范围）");
});
it("discloses unsearched resources and preserves concrete witness distances",()=>{
  expect(deltaLabel({...axis,target_search_limits:{A1:0,A2:45}},"en")).toBe("PARTLY UNSEARCHED");
  expect(deltaLabel({...axis,status:"witness",delta:3},"zh")).toBe("+3 分钟");
});

it("does not show a legacy cap as a green PASS proof", () => {
  const item = {...axis, id:"gate_outage", label:"机位", label_en:"GATE", minimality:{baseline_passed:true,predecessor_delta:45,predecessor_passed:true,witness_failed:false}} as FalsificationAxis;
  const analysis = {axes:[item],margin_tier:"buffered",evaluations:1,categories_tested:1,witness_count:0} as FalsificationAnalysis;
  const legacy = renderToStaticMarkup(<FalsificationPanel analysis={analysis} language="zh" />);
  expect(legacy).not.toContain("minimality-chain");
  expect(legacy).not.toContain("Δ45");
  const verified = renderToStaticMarkup(<FalsificationPanel analysis={{...analysis,axes:[{...item,target_search_limits:{A1:2,A2:5}}]}} language="zh" />);
  expect(verified).toContain("minimality-chain");
  expect(verified).toContain("Δ2");
  expect(verified).not.toContain("Δ45");
});
