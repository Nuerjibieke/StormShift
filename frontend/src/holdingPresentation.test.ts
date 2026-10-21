import { describe, expect, it } from "vitest";
import { allocateHoldingSlots, storyFocusFlight, holdingLayout, holdingCaption, hiddenHoldingCaption, holdingStageLabel, completedFlightsLabel } from "./holdingPresentation";
import { deriveGuidedStory } from "./guidedStory";
import type { ExecutionEvent, SessionState } from "./types";

function session(): SessionState {
  return {id:"s",mode:"guided",phase:"awaiting_approval",plans:[],execution:null,decision_lab:{},
    scenario:{current_minute:0,weather:{severity:4},runways:[],gates:[{id:"A1",occupied_by:"PARKED"}],
      flights:[{id:"CX551",connections:98,state:"holding"},{id:"TK608",connections:25,state:"holding"},
        {id:"PARKED",connections:200,state:"holding"}]}} as unknown as SessionState;
}
function initialEvent(s=session()): ExecutionEvent {
  const world=JSON.parse(JSON.stringify(s));
  return {session_id:s.id,sequence:1,sim_minute:0,type:"session_created",created_at:"",
    payload:{world_snapshot:{version:"airport-world-v1",status:"captured",session_id:s.id,minute:0,world}}};
}

describe("stable story identity",()=>{
  it("selects an initial connecting flight, not an existing gate occupant",()=>{
    expect(storyFocusFlight(session(),[])).toBe("CX551");
  });
  it("keeps identity when the focus has landed, completed, or left replacement assignments",()=>{
    const s=session(),event=initialEvent(s);
    s.scenario.flights[0].state="released";s.scenario.flights[0].connections=0;
    s.plans=[{id:"remaining",assignments:[{flight_id:"TK608"}]}] as SessionState["plans"];
    s.execution={scope_flight_ids:["CX551","TK608"],committed_operations:[]} as unknown as SessionState["execution"];
    expect(storyFocusFlight(s,[event])).toBe("CX551");
    const guide=deriveGuidedStory({session:s,phase:"recovered",minute:56,playing:false,revealedEvents:[event],
      introAcknowledged:true,selectedPlanId:"remaining",reviewKey:""});
    expect(guide.focusFlight).toBe("CX551");expect(guide.stage).toBe(6);
  });
  it("uses the fixed scope for old logs and selects no invented aircraft for an empty scope",()=>{
    const s=session();s.scenario.flights[0].state="released";
    s.execution={scope_flight_ids:["CX551","TK608"]} as unknown as SessionState["execution"];
    expect(storyFocusFlight(s,[])).toBe("CX551");
    s.execution!.scope_flight_ids=[];expect(storyFocusFlight(s,[])).toBe("");
  });
  it("ignores foreign or invalid initial snapshots and breaks ties by ID",()=>{
    const s=session(),foreign=initialEvent({...s,id:"other"}),bad=initialEvent();
    bad.payload.world_snapshot={version:"future"};
    s.scenario.flights[0].connections=25;
    expect(storyFocusFlight(s,[foreign,bad])).toBe("CX551");
    s.scenario.flights.reverse();expect(storyFocusFlight(s,[foreign,bad])).toBe("CX551");
  });
  it("reconstructs the same identity after refresh and cannot read a later future event",()=>{
    const s=session(),event=initialEvent(s),before=JSON.stringify([s,event]);
    expect(storyFocusFlight(JSON.parse(JSON.stringify(s)),JSON.parse(JSON.stringify([event])))).toBe("CX551");
    expect(JSON.stringify([s,event])).toBe(before);
    const changed=session();changed.scenario.flights[0].state="at_gate";
    expect(storyFocusFlight(changed,[])).toBe("TK608");
    expect(storyFocusFlight(changed,[event])).toBe("CX551");
  });
});
describe("schematic waiting slots, independent of operational order",()=>{
  it("reserves the focus even when its true queue rank is beyond the icon limit",()=>{
    const queue=["A","B","C","D","CX551","F"];
    const slots=allocateHoldingSlots(queue,new Map([["A",0],["B",1],["C",2],["D",3]]),4,"CX551");
    expect(slots.get("CX551")).toBe(3);expect(slots.has("D")).toBe(false);
    expect(queue.indexOf("CX551")+1).toBe(5);
    expect(holdingStageLabel(5,"07R",true,"zh")).toBe("等待降落 · 07R · 正在跟随");
  });
  it("keeps existing icon positions across replan without changing either input",()=>{
    const prior=new Map([["A",0],["CX551",1],["C",2],["D",3]]),queue=["D","C","B","A","CX551"];
    const before=JSON.stringify([[...prior],queue]),slots=allocateHoldingSlots(queue,prior,4,"CX551");
    expect([...slots]).toEqual([...prior]);expect(JSON.stringify([[...prior],queue])).toBe(before);
  });
  it("removes landed flights and never creates a second focus in the waiting area",()=>{
    const prior=new Map([["CX551",0],["A",1]]),slots=allocateHoldingSlots(["A","B"],prior,4,"CX551");
    expect(slots.has("CX551")).toBe(false);expect(slots.get("A")).toBe(1);expect(slots.get("B")).toBe(0);
  });
  it("repairs invalid/duplicate previous slots and handles viewport capacity changes",()=>{
    const prior=new Map([["A",0],["B",0],["C",-1],["CX551",3],["X",2]]);
    const slots=allocateHoldingSlots(["A","B","C","CX551","CX551"],prior,2,"CX551");
    expect(slots.size).toBe(2);expect(slots.has("CX551")).toBe(true);
    expect(new Set(slots.values()).size).toBe(2);
    expect(allocateHoldingSlots(["A"],prior,0).size).toBe(0);
    expect(allocateHoldingSlots(["A"],prior,NaN).size).toBe(0);
  });
  it("preserves capacity, uniqueness and focus through 20-flight reorder/removal sequences",()=>{
    let slots=new Map<string,number>();
    for(let round=0;round<60;round++){
      const queue=Array.from({length:20},(_,i)=>"F"+((i+round)%20)).filter(id=>round%3!==0||id!=="F0");
      const cap=1+round%4;slots=allocateHoldingSlots(queue,slots,cap,"F19");
      expect(slots.size).toBe(cap);expect(slots.has("F19")).toBe(true);
      expect(new Set(slots.values()).size).toBe(cap);
      for(const [id,pos]of slots){expect(queue).toContain(id);expect(pos).toBeGreaterThanOrEqual(0);expect(pos).toBeLessThan(cap);}
    }
  });
});
describe("legible waiting and completion labels",()=>{
  it("keeps desktop slots apart and separates captions from aircraft labels",()=>{
    for(const width of [1024,1280,1366,1440,1920]){
      const l=holdingLayout(width,150),points=Array.from({length:l.capacity},(_,i)=>l.point(i));
      for(let i=0;i<points.length;i++){
        expect(points[i].x).toBeGreaterThan(l.left);expect(points[i].x+100).toBeLessThan(width);
        expect(points[i].y).toBeLessThan(150);
        for(let j=i+1;j<points.length;j++){
          expect(Math.abs(points[i].x-points[j].x)>=122||Math.abs(points[i].y-points[j].y)>=40).toBe(true);
        }
      }
      if(l.showCaptions)expect(l.captionY+34).toBeLessThan(points[0].y-8);
    }
    expect(holdingLayout(800,85).showCaptions).toBe(false);
  });
  it("explains the schematic view and omitted flights in both languages",()=>{
    expect(holdingCaption(4,8,"zh")).toBe("等待降落 4/8");
    expect(holdingCaption(4,8,"en")).toBe("HOLDING VIEW 4/8");
    expect(hiddenHoldingCaption(["A","B","C","D","E","F","G"],300,"zh")).toContain("+4");
    expect(holdingStageLabel(6,"07L",true,"en")).toBe("WAITING TO LAND · 07L · FOCUS");
  });
  it("keeps the completed focus identifiable without pretending a takeoff or mutating input",()=>{
    const flights=[{id:"A"},{id:"CX551"}],before=JSON.stringify(flights);
    expect(completedFlightsLabel(flights,"CX551","zh")).toBe("下客与地面服务已完成: CX551 (正在跟随) · A");
    expect(completedFlightsLabel(flights,"CX551","en")).toContain("GROUND SERVICES COMPLETE");
    expect(JSON.stringify(flights)).toBe(before);
  });
});
