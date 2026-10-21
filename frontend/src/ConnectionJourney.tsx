import type { ConnectingDeparture } from "./types";
import { journeyStatus,journeyStep,journeyRule } from "./connectionProjection";
export function ConnectionJourney({connection:c,language,paused}:{connection:ConnectingDeparture;language:"zh"|"en";paused:boolean}) {
 const zh=language==="zh",step=journeyStep(c);
 const labels=zh?["下机","走到候机区","登机","离开停机位","驶向跑道","起飞"]:["Deplane","Walk to lounge","Board","Pushback","Taxi","Take off"];
 return <section className="connection-journey execution-guide span-2" aria-label={zh?"完整转机行程":"Connecting journey"} data-connection-stage={c.stage} data-passenger-stage={c.passenger_stage}>
  <header><div><small>{zh?"转机，就是换乘下一班飞机":"A connection means changing to the next flight"}</small><strong>{c.source_flight_id} → {c.flight_id} · {c.passenger_count} {zh?"人":"pax"}</strong></div><em>{paused?(zh?"已暂停":"Paused"):c.stage==="completed"?(zh?"行程完成":"Complete"):zh?"换乘下一班":"Changing flights"}</em></header>
  <ol className="connection-steps">{labels.map((label,i)=><li key={label} className={i<step||c.stage==="completed"?"done":i===step?"current":""}><span>{i<step||c.stage==="completed"?"✓":i+1}</span>{label}</li>)}</ol>
  <p className="connection-current" role="status">{journeyStatus(c,language)}</p>
  <footer><span>{journeyRule(c,language)}</span><b>{c.boarded_count}/{c.passenger_count} {zh?"已登机":"boarded"}</b></footer>
 </section>;
}
