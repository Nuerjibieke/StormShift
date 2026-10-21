import {planTitleLabel} from "./localization";
import {atMinute, numeric, numberLabel, scheduleRows} from "./communicationPresentation";
import {solverHandoff} from "./solverHandoff";
import type {SessionState} from "./types";
import "./planSchedule.css";

export function PlanSchedule({session, planId, language, selectedFlight, onSelectFlight}: {
  session: SessionState; planId: string; language: "zh" | "en";
  selectedFlight?: string; onSelectFlight?: (id: string) => void;
}) {
  const d = solverHandoff(session, planId);
  if (!d) return null;
  const zh = language === "zh", time = (v: unknown) => atMinute(v, language);
  const rows = scheduleRows(session, planId);
  const end = Math.max(10, Math.ceil(Math.max(...rows.flatMap(r => [r.landing_minute, r.gateArrival ?? 0, r.fuelDeadline ?? 0])) / 10) * 10);
  const x = (v: number) => 12 + Math.max(0, v) / end * 376;
  const ticks = Array.from({length: 6}, (_, i) => end * i / 5);
  return <section className="plan-schedule" aria-label={zh ? "整份计划：输入与输出" : "Complete plan: inputs and outputs"}>
    <header><div><span className="comm-kicker">{zh ? "从输入信息到整张时间表" : "FROM INPUTS TO THE COMPLETE SCHEDULE"}</span>
      <h3>{zh ? `这 ${rows.length} 架飞机最后怎么安排？` : `How are all ${rows.length} flights scheduled?`}</h3></div>
      <span>{planTitleLabel(d.plan.title, d.plan.profile, language)}</span></header>
    <p className="schedule-clock">{zh ? "所有时间都从演示开始计算：第 4 分钟 = T+4。下表是计划，尚不代表飞机已经完成这些动作。"
      : "All times are minutes after the demo starts: minute 4 = T+4. These are planned actions, not completed events."}</p>
    <div className="schedule-inputs">
      <article><b>{zh ? "① 航班信息" : "1 · Flights"}</b><p>{zh ? "每架飞机原定几点到、燃油最晚允许几点落地。" : "Scheduled arrival and the fuel landing deadline for each flight."}</p></article>
      <article><b>{zh ? "② 机场情况" : "2 · Airport"}</b><p>{d.runways.length ? d.runways.map(r => `${r.id} ${r.status === "open" ? (zh ? "可用" : "open") : r.status === "closed" ? (zh ? "关闭" : "closed") : r.status}`).join(" · ") : (zh ? "跑道状态未记录" : "Runway status not recorded")}{zh ? "；各机位最早可用时间见表。" : "; earliest stand availability is listed below."}</p></article>
      <article><b>{zh ? "③ 算出的安排" : "3 · Schedule"}</b><p>{zh ? "选择落地时间、跑道和机位；再加上滑行时间，得到预计到机位时间。" : "Choose landing times, runways and stands; add taxi time to estimate arrival at each stand."}</p></article>
    </div>
    {!d.trusted && <p className="schedule-missing">{zh ? "此候选的完整输入记录缺失。未记录的输入显示为“未记录”，不会用当前机场情况代替。" : "This candidate's full input record is missing. Unrecorded inputs are not replaced by current airport conditions."}</p>}
    {rows.length ? <div className="schedule-table-scroll" tabIndex={0} aria-label={zh ? "全部航班时间表，可横向滚动" : "All-flight schedule, horizontally scrollable"}>
      <table className="schedule-table">
        <thead><tr><th>{zh ? "飞机" : "Flight"}</th><th className="schedule-input-col">{zh ? "输入 · 原定到达 / 燃油期限" : "Input · scheduled / fuel limit"}</th>
          <th className="schedule-timeline-col"><span>{zh ? "输出 · 落地 → 滑行 → 到机位" : "Output · land → taxi → stand"}</span>
            <svg viewBox="0 0 400 24" aria-hidden="true">{ticks.map(t => <text key={t} x={x(t)} y="19" textAnchor={t === 0 ? "start" : t === end ? "end" : "middle"}>{numberLabel(t)}{zh ? "分" : "m"}</text>)}</svg></th>
          <th>{zh ? "输出 · 时间与位置" : "Output · time and place"}</th>
          <th className="schedule-input-col">{zh ? "输入 · 机位最早可用" : "Input · stand available"}</th></tr></thead>
        <tbody>{rows.map(r => <tr key={r.flight_id} className={r.flight_id === selectedFlight ? "schedule-focused" : ""}>
          <th scope="row">{onSelectFlight ? <button onClick={() => onSelectFlight(r.flight_id)} aria-label={zh ? `检查 ${r.flight_id} 的安排` : `Inspect ${r.flight_id}'s assignment`}>{r.flight_id}</button> : r.flight_id}</th>
          <td className="schedule-input-col"><span>{zh ? "原定 " : "Scheduled "}{time(r.scheduled)}</span><small>{zh ? "燃油最晚 " : "Fuel limit "}{time(r.fuelDeadline)}</small></td>
          <td className="schedule-timeline-col"><svg viewBox="0 0 400 62" role="img" aria-label={zh ? `${r.flight_id} ${time(r.landing_minute)}在 ${r.runway} 落地，${time(r.gateArrival)}到 ${r.gate}` : `${r.flight_id} lands on ${r.runway} at ${time(r.landing_minute)}, reaches ${r.gate} at ${time(r.gateArrival)}`}>
            {ticks.map(t => <line className="schedule-grid-line" key={t} x1={x(t)} x2={x(t)} y1="0" y2="62"/>)}
            <line className="schedule-baseline" x1="12" x2="388" y1="29" y2="29"/>
            {numeric(r.gateArrival) && <line className="schedule-taxi" x1={x(r.landing_minute)} x2={x(r.gateArrival)} y1="29" y2="29"/>}
            {numeric(r.fuelDeadline) && <line className="schedule-fuel" x1={x(r.fuelDeadline)} x2={x(r.fuelDeadline)} y1="8" y2="50"/>}
            <circle className="schedule-land" cx={x(r.landing_minute)} cy="29" r="5"/>
            <text x={x(r.landing_minute)} y="14" textAnchor={r.landing_minute / end > .75 ? "end" : "start"}>{r.runway} · {numberLabel(r.landing_minute)}{zh ? "分" : "m"}</text>
            {numeric(r.gateArrival) && <><rect className="schedule-stand" x={x(r.gateArrival) - 4} y="25" width="8" height="8"/>
              <text x={x(r.gateArrival)} y="53" textAnchor={r.gateArrival / end > .75 ? "end" : "start"}>{r.gate} · {numberLabel(r.gateArrival)}{zh ? "分" : "m"}</text></>}
          </svg></td>
          <td><span>{time(r.landing_minute)} · {r.runway} {zh ? "落地" : "landing"}</span><small>{time(r.gateArrival)} · {zh ? "到 " : "at "}{r.gate}</small></td>
          <td className="schedule-input-col"><span>{r.gate} · {time(r.gateAvailable)}</span>
            <small>{zh ? "滑行 " : "Taxi "}{numeric(r.taxi_minutes) ? numberLabel(r.taxi_minutes) : "—"} {zh ? "分钟" : "min"}</small></td>
        </tr>)}</tbody>
      </table>
    </div> : <p>{zh ? "本候选没有新增的落地安排。" : "This candidate has no new landing assignments."}</p>}
    <div className="schedule-legend"><span><i className="land"/>{zh ? "圆点：落地" : "Circle: landing"}</span><span><i className="taxi"/>{zh ? "蓝线：滑行" : "Blue line: taxi"}</span><span><i className="stand"/>{zh ? "方块：到机位" : "Square: stand arrival"}</span><span><i className="fuel"/>{zh ? "红虚线：燃油最晚落地时间" : "Red dash: fuel landing deadline"}</span></div>
    <p className="schedule-footnote">{zh ? "到机位时间 = 落地时间 + 计划滑行时间；图中没有把到机位时间当作机位再次空出的时间。"
      : "Stand arrival = landing time + planned taxi duration; it does not indicate when the stand becomes free again."}</p>
  </section>;
}
