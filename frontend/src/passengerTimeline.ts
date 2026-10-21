import type { ConnectingDeparture } from "./types";

const bounded = (value:number) => Math.max(0,Math.min(1,value));
export const TRANSFER_WALK_MS = 18000;
export const BOARDING_WALK_MS = 8000;

/** Visual-only pedestrian clock. Walking permission comes from acknowledged facts,
 * then footsteps use wall-clock delta, not movement or waiting of any aircraft. */
export class PassengerTimeline {
  private connection?: ConnectingDeparture;
  private key = "";
  private previousMinute?: number;
  private incoming = 0;
  private boarding = 0;

  reset() {
    this.connection=undefined; this.key=""; this.previousMinute=undefined;
    this.incoming=0; this.boarding=0;
  }

  sync(c:ConnectingDeparture, minute:number) {
    const key=c.source_flight_id+":"+c.flight_id;
    const rewind=this.previousMinute!==undefined && minute<this.previousMinute-.01;
    if(key!==this.key || rewind) {
      this.reset(); this.key=key;
      // Opening an existing session reconstructs its position, not every old walk.
      if(c.source_arrival_minute!=null && c.transfer_ready_minute!=null) {
        this.incoming=bounded((minute-c.source_arrival_minute)/Math.max(1,c.transfer_ready_minute-c.source_arrival_minute));
      }
      if(c.boarding_start_minute!=null && c.pushback_minute!=null && ["boarding","boarded"].includes(c.passenger_stage)) {
        this.boarding=bounded((minute-c.boarding_start_minute)/Math.max(1,c.pushback_minute-c.boarding_start_minute));
      }
    }
    this.connection=c; this.previousMinute=minute;
  }

  advance(deltaMs:number, rate:number, paused:boolean) {
    const c=this.connection;
    if(!c || paused || !Number.isFinite(deltaMs) || !Number.isFinite(rate) || deltaMs<=0 || rate<=0
      || c.source_arrival_minute==null || c.passenger_stage==="onboard"
      || ["cancelled","blocked"].includes(c.stage)) return;
    let remaining=deltaMs*rate;
    const step=Math.min(remaining,(1-this.incoming)*TRANSFER_WALK_MS);
    this.incoming=bounded(this.incoming+step/TRANSFER_WALK_MS); remaining-=step;
    // Merely knowing a departure's future schedule does not allow boarding.
    if(remaining>0 && ["boarding","boarded"].includes(c.passenger_stage)) {
      this.boarding=bounded(this.boarding+remaining/BOARDING_WALK_MS);
    }
  }

  frame(c=this.connection):{connection:ConnectingDeparture; minute:number}|undefined {
    if(!c)return;
    if(c.source_arrival_minute==null || c.transfer_start_minute==null || c.transfer_ready_minute==null
      || c.passenger_stage==="onboard" || ["cancelled","blocked"].includes(c.stage)) {
      return {connection:c,minute:this.previousMinute??0};
    }
    if(this.incoming<1) {
      const minute=c.source_arrival_minute+(c.transfer_ready_minute-c.source_arrival_minute)*this.incoming;
      return {connection:{...c,passenger_stage:minute<c.transfer_start_minute?"disembarking":"transferring"},minute};
    }
    if(["boarding","boarded"].includes(c.passenger_stage) && c.boarding_start_minute!=null && c.pushback_minute!=null) {
      return {connection:{...c,passenger_stage:this.boarding<1?"boarding":"boarded"},
        minute:c.boarding_start_minute+(c.pushback_minute-c.boarding_start_minute)*this.boarding};
    }
    return {connection:{...c,passenger_stage:"waiting"},minute:c.transfer_ready_minute};
  }
}
