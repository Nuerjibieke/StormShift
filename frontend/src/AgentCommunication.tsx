import {useRef, useState} from "react";
import {ArrowRight, BrainCircuit, Check, CloudRain, Gauge, Info, ShieldCheck, Users, Wrench} from "lucide-react";
import {candidateDecisionEvidence} from "./candidateDecisionEvidence";
import {handoffStory, scheduleRows} from "./communicationPresentation";
import {PlanSchedule} from "./PlanSchedule";
import type {SessionState} from "./types";
import "./agentCommunication.css";

export function agentIdentity(name: string, language: "zh" | "en") {
  const zh = language === "zh";
  if (name.includes("Weather")) return {label: zh ? "天气" : "Weather", Icon: CloudRain, tone: "sky"};
  if (name.includes("Runway")) return {label: zh ? "跑道" : "Runway", Icon: Gauge, tone: "blue"};
  if (name.includes("Gate")) return {label: zh ? "地勤" : "Ground", Icon: Wrench, tone: "amber"};
  if (name.includes("Passenger")) return {label: zh ? "旅客" : "Passengers", Icon: Users, tone: "purple"};
  if (name.includes("Safety")) return {label: zh ? "安全" : "Safety", Icon: ShieldCheck, tone: "teal"};
  return {label: zh ? "协调" : "Coordinator", Icon: BrainCircuit, tone: "teal"};
}
const roles = ["Weather Agent", "Runway Agent", "Gate & Ground Agent", "Airline & Passenger Agent", "Safety Critic", "Recovery Coordinator"];
export function AgentAvatars({language}: {language: "zh" | "en"}) {
  return <span className="agent-mini-team" aria-hidden="true">{roles.map(name => {
    const {Icon, tone} = agentIdentity(name, language);
    return <i className={tone} key={name}><Icon size={13}/></i>;
  })}</span>;
}
export function AgentCommunication({session, planId, language, playbackPaused = false}: {
  session: SessionState; planId?: string; language: "zh" | "en"; playbackPaused?: boolean;
}) {
  const [selected, setSelected] = useState(0), [focus, setFocus] = useState("");
  const reader = useRef<HTMLElement>(null);
  const zh = language === "zh";
  const arbitration = planId ? candidateDecisionEvidence(session, planId).arbitration : undefined;
  const checks = arbitration?.checks ?? [];
  const activeIndex = Math.min(selected, Math.max(0, checks.length - 1));
  const active = checks[activeIndex];
  const stories = planId ? checks.map(c => handoffStory(session, planId, c, language, focus)) : [];
  const story = stories[activeIndex], plain = story?.plain;
  const rows = planId ? scheduleRows(session, planId) : [];
  const source = agentIdentity(active?.producer ?? "", language);
  const recipient = agentIdentity(active?.consumer ?? "", language);
  const who = (label: string) => label + (zh ? "助手" : " assistant");
  const status = (value: string) => ({
    aligned: zh ? "已记录：通过" : "Recorded: passed",
    resolved_watch: zh ? "已补信息，仍要观察" : "Information received; monitor",
    needs_evidence: zh ? "已记录：需要查询" : "Recorded: query required",
    human_required: zh ? "已记录：需要人决定" : "Recorded: human decision required",
    blocked: zh ? "已记录：需要修改安排" : "Recorded: repair required",
  }[value] ?? (zh ? "还没有处理结果" : "No outcome recorded"));
  const pickFlight = (id: string) => {
    setFocus(id);
    const index = checks.findIndex(c => c.id === "runway_to_gate");
    if (index >= 0) {setSelected(index); reader.current?.scrollIntoView({behavior: "smooth", block: "nearest"});}
  };
  return <section className="agent-communication" aria-label={zh ? "Agent 沟通证据" : "Agent handoff evidence"}>
    {playbackPaused && <p className="inspection-pause-note" role="status">{zh ? "演示已暂停，放心查看。关闭后请点击右上角「继续」。" : "Playback is paused. After closing, use Resume at the top right."}</p>}
    <div className="communication-intro"><span className="comm-kicker">AGENT HANDOFFS</span>
      <h2>{zh ? "谁把什么信息交给谁，后来怎样了？" : "Who shared what with whom, and what happened next?"}</h2>
      <span className="communication-source"><Info size={13}/>{zh
        ? "交接内容按本次工具记录整理。"
        : "Handoffs are reconstructed from this run's tool records."}</span>
    </div>
    {active && plain && story ? <div className="communication-workspace handoff-story-workspace">
      <nav aria-label={zh ? "信息交接列表" : "Handoff list"}>{checks.map((check, index) => {
        const from = agentIdentity(check.producer, language), to = agentIdentity(check.consumer, language);
        return <button key={check.id} onClick={() => setSelected(index)} className={active.id === check.id ? "selected" : ""} aria-pressed={active.id === check.id}>
          <span className="comm-number">{String(index + 1).padStart(2, "0")}</span>
          <span><b>{from.label}<ArrowRight size={13}/>{to.label}</b>
            <span className="handoff-nav-message">{stories[index]?.message}</span><small>{status(check.status)}</small></span>
          {check.status === "aligned" ? <Check size={16}/> : <Info size={16}/>}
        </button>;
      })}</nav>
      <article className="handoff-reader plain-handoff story-reader" key={active.id} ref={reader}>
        <div className="handoff-route"><b>{who(source.label)}</b><ArrowRight size={20}/><b>{who(recipient.label)}</b></div>
        <h3>{plain.title}</h3>
        {plain.selectableFlights && <label className="handoff-flight-picker">{zh ? "检查哪架飞机" : "Inspect flight"}<select aria-label={zh ? "沟通页航班" : "Handoff flight"} value={plain.flightId} onChange={e => setFocus(e.target.value)}>
          {rows.map(r => <option key={r.flight_id} value={r.flight_id}>{r.flight_id} · {r.runway} → {r.gate}</option>)}
        </select></label>}
        <ol className="handoff-message-steps" aria-label={zh ? "这条信息怎么被处理" : "How this handoff was processed"}>
          <li className="handoff-message-step">
            <span className="message-step-number">1</span>
            <section className="handoff-message sent-message">
              <h4>{zh ? `${who(source.label)}交给${who(recipient.label)}的信息` : `Information from ${who(source.label)} to ${who(recipient.label)}`}</h4>
              <p className="message-content">{story.message}</p>
            </section>
          </li>
          <li className="handoff-message-step">
            <span className="message-step-number">2</span>
            <section className="handoff-message received-message">
              <h4>{zh ? `${who(recipient.label)}收到后，怎样判断` : `How ${who(recipient.label)} checked it`}</h4>
              <p>{story.recipientBasis}</p>
              {story.comparison && <p className="handoff-calculation">{story.comparison}</p>}
              {story.recipientBasis !== plain.problem && <p className="recipient-conclusion">{plain.problem}</p>}
              {active.status !== "aligned" && active.id !== "safety_to_coordinator" && <p className="recipient-action">{plain.next}</p>}
            </section>
          </li>
        </ol>
        {checks[activeIndex + 1] && <button className="handoff-next-record" onClick={() => {setSelected(activeIndex + 1);reader.current?.scrollIntoView({behavior:"smooth",block:"nearest"});}}>
          {zh ? "下一条：" : "Next: "}{who(agentIdentity(checks[activeIndex + 1].producer, language).label)}<ArrowRight size={14}/>{who(agentIdentity(checks[activeIndex + 1].consumer, language).label)}
        </button>}
      </article>
    </div> : <div className="communication-empty"><BrainCircuit size={26}/><p>{zh ? "生成方案后，在这里查看每架飞机的具体检查。当前没有这份方案的检查记录。" : "Generate a plan to inspect its flight checks. This candidate has no saved checks."}</p></div>}
    {planId && <PlanSchedule session={session} planId={planId} language={language} selectedFlight={plain?.flightId} onSelectFlight={pickFlight}/>}

  </section>;
}
