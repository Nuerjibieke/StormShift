import {expect,it} from "vitest";
import {renderToStaticMarkup} from "react-dom/server";
import {copy} from "./copy";
import {AgentCommunication} from "./AgentCommunication";
import {PassengerPanel} from "./PassengerPanel";
import {ConnectionJourney} from "./ConnectionJourney";
import {journeyRule} from "./connectionProjection";
import type {ConnectingDeparture,SessionState} from "./types";
import type {AirportMapState} from "./historicalWorld";

const c={source_flight_id:"SK218",flight_id:"NH701",passenger_count:24,
 passenger_stage:"waiting",stage:"waiting_aircraft",boarded_count:0} as ConnectingDeparture;
it("keeps entry copy concise and both locale dictionaries aligned",()=>{
 expect(Object.keys(copy.zh).sort()).toEqual(Object.keys(copy.en).sort());
 const text=JSON.stringify(copy);
 for(const phrase of ["默认固定种子","无需外部 API","安全降级","repeatable, no external API","Exercise only"])
  expect(text).not.toContain(phrase);
});
it("explains changing flights and the reason for each movement without airport jargon",()=>{
 const html=renderToStaticMarkup(<ConnectionJourney connection={c} language="zh" paused={false}/>);
 expect(html).toContain("转机，就是换乘下一班飞机");
 expect(html).toContain("登机条件");expect(html).toContain("0/24");
 expect(journeyRule({...c,passenger_stage:"boarding",stage:"boarding"},"zh")).toContain("旅客全部登机");
 expect(journeyRule({...c,passenger_stage:"boarded",stage:"holding_short"},"zh")).toContain("跑道开放");
 expect(journeyRule({...c,passenger_stage:"boarded",stage:"completed"},"zh")).toContain("24 人已换乘 NH701 起飞");
});
it("states the actual assistant mode without defensive dialogue claims",()=>{
 const session={llm_tier:"fallback",traces:[],plans:[],decision_lab:{}} as unknown as SessionState;
 const html=renderToStaticMarkup(<AgentCommunication session={session} language="zh"/>);
 expect(html).toContain("交接内容按本次工具记录整理");
 for(const phrase of ["不代表 LLM","不是虚构","不能被 Agent 共识替代"])expect(html).not.toContain(phrase);
 const world={id:"s",scenario:{current_minute:0,flights:[],gates:[]},execution:null} as unknown as AirportMapState;
 const passengers=renderToStaticMarkup(<PassengerPanel world={world} events={[]} minute={0} language="zh" onClose={()=>{}}/>);
 expect(passengers).toContain("每个图标代表一班旅客");
 expect(passengers).not.toContain("不代表");expect(passengers).not.toContain("合成运营演练");
});
