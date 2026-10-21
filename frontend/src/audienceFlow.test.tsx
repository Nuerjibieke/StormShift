import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { approvalActionLabel } from "./executionDriver";
import { executionEventLabel } from "./localization";
import { AgentCommunication } from "./AgentCommunication";
import { PassengerPanel } from "./PassengerPanel";
import type { SessionState } from "./types";
import type { AirportMapState } from "./historicalWorld";
it("does not call initial approval a replan",()=>{
  expect(approvalActionLabel({execution:null},"zh")).toBe("批准并开始执行");
  expect(approvalActionLabel({execution:null},"en")).toBe("Approve and start execution");
  const s={execution:{status:"blocked"}} as SessionState;
  expect(approvalActionLabel(s,"zh")).toBe("批准新方案并继续");
});
it("renders plain operational milestones instead of internal event identifiers",()=>{
  expect(executionEventLabel("scheduled_event","zh")).toBe("开始监测运行变化");
  expect(executionEventLabel("gate_recovered","zh")).toBe("机位恢复可用");
  expect(executionEventLabel("plan_certificate_verified","zh")).toBe("执行前方案核验通过");
});
it("discloses paused playback in both inspection views, without claiming auto-resume",()=>{
  const s={llm_tier:"fallback",traces:[],plans:[],decision_lab:{}} as unknown as SessionState;
  const agents=renderToStaticMarkup(<AgentCommunication session={s} language="zh" playbackPaused/>);
  expect(agents).toContain("演示已暂停");expect(agents).toContain("关闭后请点击");
  const world={id:"s",scenario:{current_minute:0,flights:[],gates:[]},execution:null} as unknown as AirportMapState;
  const passengers=renderToStaticMarkup(<PassengerPanel world={world} events={[]} minute={0} language="zh" onClose={()=>{}} playbackPaused/>);
  expect(passengers).toContain("演示已暂停");expect(passengers).toContain("关闭后请点击");
  expect(renderToStaticMarkup(<AgentCommunication session={s} language="en"/>)).not.toContain("Playback is paused");
});
