import type { Point } from "./aircraftProjection";

export type PassengerHall = {
  terminalX:number; terminalY:number; terminalW:number; terminalH:number;
};

export function passengerHall(h:PassengerHall, arrivalCount:number) {
  const loungeY=h.terminalY+h.terminalH-26;
  const columns=Math.max(3,Math.floor((h.terminalW-60)/16));
  const rows=Math.max(1,Math.ceil(arrivalCount/columns));
  return {
    corridorX:h.terminalX+h.terminalW-16,
    arrivalsX:h.terminalX+h.terminalW-34,
    lounge:{x:h.terminalX+30,y:loungeY},
    otherExit:{x:h.terminalX-14,y:loungeY-26},
    waitingTop:loungeY-26-(rows-1)*20,
    waitingSlot:(index:number):Point=>({
      x:h.terminalX+30+(index%columns)*16,
      y:loungeY-26-Math.floor(index/columns)*20,
    }),
  };
}

/** A side door, a dedicated lead-in, then the concourse outside the parked row.
 * Never connect one stand horizontally through neighbouring aircraft. */
export function gatePassengerAccess(gate:Point,corridorX:number,terminalMidY:number):Point[] {
  const door={x:gate.x-18,y:gate.y};
  const bridgeY=gate.y+(gate.y<terminalMidY?-34:34);
  return [door,{x:door.x,y:bridgeY},{x:corridorX,y:bridgeY}];
}

export function arrivingPassengerRoute(gate:Point,h:PassengerHall,_slot:number,count:number):Point[] {
  const hall=passengerHall(h,count), exit=hall.otherExit;
  return [...gatePassengerAccess(gate,hall.arrivalsX,h.terminalY+h.terminalH/2),
    {x:hall.arrivalsX,y:exit.y},exit];
}
