import {describe,it,expect} from "vitest";
import {standExitFrame,standEntryFrame,STAND_EXIT_MS,STAND_ENTRY_MS} from "./standHandover";
import {buildPlaybackSchedule} from "./playback";
import type {ExecutionEvent} from "./types";

describe("visible stand handover",()=>{
  it("moves the old aircraft out before fading, while the arrival waits on the other side",()=>{
    const stands=[{x:300,y:225},{x:334,y:225},{x:368,y:225},
      {x:300,y:420},{x:334,y:420},{x:368,y:420},{x:395,y:472}];
    for(const gate of stands){
      const inboundY=gate.y===225?gate.y+64:gate.y===472?gate.y+52:gate.y-64;
      const waiting={x:gate.x,y:gate.y+(inboundY>gate.y?38:-38)};
      const start=standExitFrame(0,gate,inboundY,stands,443);
      expect(start).toMatchObject({...gate,alpha:1});
      for(let i=0;i<=200;i++){
        const frame=standExitFrame(i/200,gate,inboundY,stands,443);
        for(const other of [...stands.filter(p=>p!==gate),waiting]){
          expect(Math.hypot(frame.x-other.x,frame.y-other.y),
            `gate ${gate.x},${gate.y} past ${other.x},${other.y} at ${i/200}`).toBeGreaterThan(31);
        }
        if(Math.hypot(frame.x-gate.x,frame.y-gate.y)<48)expect(frame.alpha).toBe(1);
      }
      const end=standExitFrame(1,gate,inboundY,stands,443);
      expect(end.x).toBeGreaterThan(443);
      expect(end.alpha).toBe(0);
    }
  });
  it("brings the next aircraft continuously from its waiting point into the stand",()=>{
    const gate={x:300,y:420},offset={x:0,y:-38};
    expect(standEntryFrame(0,gate,offset)).toEqual({x:300,y:382});
    expect(standEntryFrame(.5,gate,offset)).toEqual({x:300,y:401});
    expect(standEntryFrame(1,gate,offset)).toEqual(gate);
  });
  it("leaves room for exit and entry at a same-minute reservation boundary",()=>{
    const events:ExecutionEvent[]=[
      {session_id:"s",created_at:"",sequence:57,type:"gate_released",sim_minute:31,payload:{gate:"A4",flight_id:"SK218"}},
      {session_id:"s",created_at:"",sequence:58,type:"gate_arrived",sim_minute:31,payload:{gate:"A4",flight_id:"JR330"}},
    ];
    const schedule=buildPlaybackSchedule(events,31);
    expect(schedule[1].delayMs-schedule[0].delayMs).toBeGreaterThan(STAND_EXIT_MS);
    expect(STAND_ENTRY_MS).toBeLessThan(2300);
    expect(events.map(e=>e.sim_minute)).toEqual([31,31]);
  });
});
