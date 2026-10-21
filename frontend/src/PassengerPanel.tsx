import { useEffect } from "react";
import { Plane, Users, X } from "lucide-react";
import { passengerGroups } from "./passengerView";
import type { AirportMapState } from "./historicalWorld";
import type { ExecutionEvent } from "./types";

export function PassengerPanel({world,events,minute,language,onClose,playbackPaused = false}: {
  world: AirportMapState; events: ExecutionEvent[]; minute: number; language:"zh"|"en"; onClose:()=>void; playbackPaused?: boolean;
}) {
  const groups=passengerGroups(world,events,minute), zh=language==="zh";
  const total=groups.reduce((s,g)=>s+g.count,0);
  const arrived=groups.filter(g=>g.stage==="terminal").reduce((s,g)=>s+g.count,0);
  useEffect(()=>{const key=(e:KeyboardEvent)=>{if(e.key==="Escape")onClose()};window.addEventListener("keydown",key);return()=>window.removeEventListener("keydown",key)},[onClose]);
  return <div className="modal-backdrop passenger-backdrop" onMouseDown={onClose}>
    <section className="passenger-dialog" role="dialog" aria-modal="true" aria-label={zh?"旅客视角":"Passenger view"} onMouseDown={e=>e.stopPropagation()}>
      <header><span><small>PASSENGER PERSPECTIVE</small><h2><Users size={24}/>{zh?"把飞机背后的人看见":"People behind the flights"}</h2></span><button autoFocus onClick={onClose} aria-label={zh?"关闭旅客视角":"Close passenger view"}><X/></button></header>
      {playbackPaused && <p className="inspection-pause-note" role="status">{zh ? "演示已暂停，放心查看。关闭后请点击右上角「继续」。" : "Playback is paused. After closing, use Resume at the top right."}</p>}
      <div className="passenger-numbers"><span><b>{total}</b><small>{zh?"场景转机旅客":"Scenario transfer passengers"}</small></span>
        <span><b>{arrived}</b><small>{zh?"已到位航班关联旅客":"Passengers on gate-arrived flights"}</small></span></div>
      <p className="passenger-caveat">{zh?"每个图标代表一班旅客。这里查看航班到达进度，转机和登机进度见行程卡片。"
        :"Each icon represents one flight’s passengers. View flight arrivals here and follow transfers and boarding in the journey card."}</p>
      <div className="passenger-list">{groups.map(g=><article key={g.flightId}><span className={`passenger-flight ${g.stage}`}><Plane size={19}/></span>
        <div><b>{g.flightId} <small>→ {g.gate}</small></b><span>{g.stage==="terminal"?(zh?"航班已到位":"Aircraft at gate"):g.stage==="diverted"?(zh?"航班已备降":"Flight diverted"):g.stage==="outside_scope"?(zh?"本轮未调度":"Outside this round’s schedule"):(zh?"航班尚未到位":"Aircraft not yet at gate")}</span></div>
        <strong>{g.count}<small>{zh?"人":"pax"}</small></strong>
        <span className="cohort-icons" aria-hidden="true">{Array.from({length:Math.min(6,Math.ceil(g.count/20))},(_,i)=><Users key={i} size={16}/>)}</span>
      </article>)}</div>
    </section>
  </div>;
}
