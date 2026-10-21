
import { describe, expect, it } from "vitest";
import { passengerGroups } from "./passengerView";
import type { AirportMapState } from "./historicalWorld";
import type { ExecutionEvent } from "./types";

function world(): AirportMapState {
  return { id:"s", scenario:{current_minute:9, gates:[], flights:[
    {id:"F1", connections:60, planned_gate:"A1",state:"holding"},
    {id:"F2", connections:30, planned_gate:"A2",state:"holding"},
  ]}, plans:[{id:"proposal", assignments:[{flight_id:"F1",gate:"A2",gate_start_minute:1}]}],
  selected_plan_id:"proposal", execution:{scope_flight_ids:["F1"],committed_operations:[]},
  decision_lab:{},phase:"executing" } as unknown as AirportMapState;
}
const event={sequence:1,session_id:"s",type:"execution_tick",sim_minute:3,created_at:"",
  payload:{flight_id:"F1",runway:"07L",gate:"A1",runway_release_minute:6,gate_start_minute:9,gate_release_minute:29}} as ExecutionEvent;
describe("passenger cohorts follow only revealed execution",()=>{
  it("does not move people for an unapproved candidate or future event",()=>{
    expect(passengerGroups(world(),[],10)[0].stage).toBe("onboard");
    expect(passengerGroups(world(),[],10)[1].stage).toBe("outside_scope");
    expect(passengerGroups(world(),[event],2)[0].stage).toBe("onboard");
  });
  it("waits for the aircraft gate boundary and never reports transfer success",()=>{
    expect(passengerGroups(world(),[event],8.99)[0].stage).toBe("onboard");
    expect(passengerGroups(world(),[event],9)[0].stage).toBe("terminal");
    expect(passengerGroups(world(),[event],40)[0].stage).toBe("terminal");
    expect(passengerGroups(world(),[event],9).map(g=>g.count)).toEqual([60,30]);
  });
  it("keeps committed gate through local repair and excludes other-session events",()=>{
    const w=world();w.plans=[];w.phase="replanning";
    expect(passengerGroups(w,[event],10)[0].gate).toBe("A1");
    expect(passengerGroups(w,[{...event,session_id:"other"}],10)[0].stage).toBe("onboard");
  });
  it("respects same-minute arrival acknowledgements",()=>{
    const w=world();w.execution!.committed_operations=[{flight_id:"F1",plan_id:"p",revision:1,runway:"07L",gate:"A1",
      landing_minute:3,runway_release_minute:6,gate_start_minute:9,gate_release_minute:29,stage:"taxiing",runway_released:true}];
    expect(passengerGroups(w,[event],9)[0].stage).toBe("onboard");
    w.execution!.committed_operations[0].stage="at_gate";
    expect(passengerGroups(w,[event],9)[0].stage).toBe("terminal");
  });
  it("retains initial ground cohorts and does not mutate input",()=>{
    const w=world();w.scenario.gates=[{id:"A2",occupied_by:"F2",available_minute:10,status:"occupied",remote:false,taxi_minutes:4}];
    const before=JSON.stringify(w);
    expect(passengerGroups(w,[],0)[1].stage).toBe("terminal");
    expect(JSON.stringify(w)).toBe(before);
  });
});
