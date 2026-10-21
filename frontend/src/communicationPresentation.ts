import {candidateDecisionEvidence} from "./candidateDecisionEvidence";
import {solverHandoff} from "./solverHandoff";
import type {AgentContractCheck, SessionState} from "./types";

export type Language = "zh" | "en";
export const numeric = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
export const numberLabel = (v: number) => Number(v.toFixed(1)).toString();
export function atMinute(v: unknown, language: Language) {
  return numeric(v) ? (language === "zh" ? `第 ${numberLabel(v)} 分钟` : `minute ${numberLabel(v)}`) : (language === "zh" ? "未记录" : "Not recorded");
}
export function scheduleRows(session: SessionState, planId: string) {
  const plan = session.plans.find(p => p.id === planId);
  return (plan?.assignments ?? []).map(a => {
    const d = solverHandoff(session, planId, a.flight_id)!;
    return {...a, scheduled: d.scheduled, fuelDeadline: d.fuelDeadline, gateAvailable: d.gateAvailable,
      gateArrival: numeric(a.landing_minute) && numeric(a.taxi_minutes) ? a.landing_minute + a.taxi_minutes : undefined};
  }).sort((a, b) => a.landing_minute - b.landing_minute || a.flight_id.localeCompare(b.flight_id));
}

export interface PlainCheck {
  title: string;
  input: string;
  schedule: string;
  problem: string;
  next: string;
  flightId?: string;
  selectableFlights?: boolean;
  caution: boolean;
}

export function explainCheck(session: SessionState, planId: string, check: AgentContractCheck, language: Language, focus?: string): PlainCheck {
  const zh = language === "zh", say = (a: string, b: string) => zh ? a : b, time = (v: unknown) => atMinute(v, language);
  const plan = session.plans.find(p => p.id === planId);
  const rows = scheduleRows(session, planId);
  const d = plan ? solverHandoff(session, planId) : undefined;
  const watching = check.status === "resolved_watch";
  const caution = check.status !== "aligned" || check.hard_block;
  const missing: PlainCheck = {
    title: say("这项检查缺少具体记录", "Details are missing for this check"),
    input: say("没有保存足够的航班、时间和资源信息。", "Flight, time or resource details were not saved."),
    schedule: say("暂时无法把这项检查与具体安排对应起来。", "This check cannot yet be tied to a specific assignment."),
    problem: say("现有记录不足以解释检查结论。", "The saved details do not explain the result."),
    next: say("重新生成方案后再检查；详细记录可在「逐条阅读」查看。", "Generate a new plan and review it; details are available in the trace reader."),
    caution: true,
  };
  if (!plan || !rows.length) return {...missing,
    schedule: say("本方案没有新增的落地安排。", "This plan has no new landing assignments.")};

  if (check.id === "weather_to_runway") {
    const windows = plan.risk_shield?.runway_clearance?.windows ?? [];
    const window = windows.find(w => w.status === "blocked" || w.status === "conditional") ?? windows[0];
    if (!window) {
      if (!d?.runways.length) return missing;
      const open = d.runways.filter(r => r.status === "open").map(r => r.id);
      const used = [...new Set(rows.map(r => r.runway))];
      const allOpen = used.every(r => open.includes(r));
      return {title: say("计划使用的跑道能不能用？", "Are the assigned runways available?"),
        input: say(`方案生成时，可用跑道：${open.join("、") || "未记录"}。`, `Open runways when planned: ${open.join(", ") || "not recorded"}.`),
        schedule: say(`${rows.length} 架飞机使用 ${used.join("、")}。`, `${rows.length} flights use ${used.join(", ")}.`),
        problem: allOpen && !caution ? say("这些飞机使用的跑道在当时都可用。", "All assigned runways were open at planning time.") : say("部分跑道的可用时间还需要确认。", "Availability still needs to be confirmed for some runways."),
        next: allOpen && !caution ? say("继续检查飞机到达机位时，机位是否已经空出。", "Next, check whether each stand is free when its aircraft arrives.") : say("核实跑道恢复时间，再检查落地安排。", "Confirm reopening times, then review the landings."),
        caution: caution || !allOpen};
    }
    const first = rows.find(r => r.runway === window.runway);
    const input = say(`${window.runway} 预计${time(window.point_ready_minute)}恢复；较保守的估计是${time(window.p90_ready_minute)}。这两个时间都是预测。`,
      `${window.runway}: expected reopening at ${time(window.point_ready_minute)}; conservative estimate at ${time(window.p90_ready_minute)}. Both are forecasts.`);
    if (!first) return {title: say(`这份计划用不用 ${window.runway}？`, `Does this plan use ${window.runway}?`), input,
      schedule: say(`这 ${rows.length} 架飞机都不用 ${window.runway} 落地，安排在 ${[...new Set(rows.map(r => r.runway))].join("、")}。`,
        `None of these ${rows.length} flights land on ${window.runway}; they use ${[...new Set(rows.map(r => r.runway))].join(", ")}.`),
      problem: say(`${window.runway} 的恢复时间不会影响这份计划里的落地安排。`, `${window.runway}'s reopening time does not affect this plan's landings.`),
      next: caution ? say("这项检查仍有待办，请查看详细记录后再决定。", "This check still has an outstanding item; review the details before deciding.")
        : say(`这份计划不需要为 ${window.runway} 追加天气查询。下一步检查机位。`, `No additional weather query for ${window.runway} is needed for this plan. Check the stands next.`), caution};
    const ready = window.p90_ready_minute;
    if (!numeric(ready)) return {...missing, input, flightId: first.flight_id,
      title: say(`${first.flight_id} 落地前，${first.runway} 能恢复吗？`, `Will ${first.runway} reopen before ${first.flight_id} lands?`),
      schedule: say(`${first.flight_id} 计划${time(first.landing_minute)}在 ${first.runway} 落地。`, `${first.flight_id} is scheduled to land on ${first.runway} at ${time(first.landing_minute)}.`)};
    const early = ready - first.landing_minute;
    return {title: say(`${first.flight_id} 落地前，${first.runway} 能恢复吗？`, `Will ${first.runway} reopen before ${first.flight_id} lands?`), input, flightId: first.flight_id,
      schedule: say(`${first.flight_id} 是首架使用 ${first.runway} 的飞机，计划${time(first.landing_minute)}落地。`, `${first.flight_id} is the first arrival on ${first.runway}, at ${time(first.landing_minute)}.`),
      problem: early > 0 ? say(`落地比保守恢复估计早 ${numberLabel(early)} 分钟。如果跑道到那时还没恢复，这个安排就不能执行。`,
        `Landing is ${numberLabel(early)} minutes before the conservative reopening estimate. It cannot proceed if the runway is still closed.`)
        : say(`落地时间不早于${time(ready)}的保守恢复估计。`, `Landing is no earlier than the conservative reopening estimate at ${time(ready)}.`),
      next: watching ? say("天气查询已经完成；落地前仍要确认跑道已开放。若仍关闭，就推迟落地或改用其他可用跑道。", "The weather query is complete. Confirm the runway is open before landing; otherwise delay or use another available runway.")
        : early > 0 || caution ? say("先查询最新天气和跑道恢复时间；仍赶不上时，重新安排落地时间或跑道。", "Get the latest weather and reopening estimate; reschedule the landing or runway if needed.")
        : say("这项时间检查通过。执行前确认跑道开放，再检查机位。", "This timing check passes. Confirm the runway is open before execution, then check the stand."),
      caution: caution || early > 0};
  }

  if (check.id === "runway_to_gate") {
    const bad = rows.find(r => numeric(r.gateArrival) && numeric(r.gateAvailable) && r.gateArrival < r.gateAvailable);
    const a = rows.find(r => r.flight_id === focus) ?? bad ?? rows[0];
    if (!numeric(a.gateArrival) || !numeric(a.gateAvailable)) return {...missing, title: say(`${a.flight_id} 到 ${a.gate} 时，机位空出来了吗？`, `Will ${a.gate} be free when ${a.flight_id} arrives?`),
      input: say(`${a.gate} 的可用时间：${time(a.gateAvailable)}；落地后滑行时间：${numeric(a.taxi_minutes) ? numberLabel(a.taxi_minutes) + " 分钟" : "未记录"}。`,
        `${a.gate} available: ${time(a.gateAvailable)}; taxi duration: ${numeric(a.taxi_minutes) ? numberLabel(a.taxi_minutes) + " min" : "not recorded"}.`),
      schedule: say(`${a.flight_id} 计划${time(a.landing_minute)}在 ${a.runway} 落地，前往 ${a.gate}。`, `${a.flight_id} lands on ${a.runway} at ${time(a.landing_minute)}, then goes to ${a.gate}.`),
      flightId: a.flight_id, selectableFlights: true};
    const gap = a.gateAvailable - a.gateArrival;
    return {title: say(`${a.flight_id} 到 ${a.gate} 时，机位空出来了吗？`, `Will ${a.gate} be free when ${a.flight_id} arrives?`),
      input: say(`${a.gate} 最早${time(a.gateAvailable)}可停靠；${a.flight_id} 落地后需要滑行 ${numberLabel(a.taxi_minutes)} 分钟。`,
        `${a.gate} is available from ${time(a.gateAvailable)}; ${a.flight_id} needs ${numberLabel(a.taxi_minutes)} minutes to taxi.`),
      schedule: say(`${time(a.landing_minute)}在 ${a.runway} 落地 + 滑行 ${numberLabel(a.taxi_minutes)} 分钟 = ${time(a.gateArrival)}到 ${a.gate}。`,
        `Land on ${a.runway} at ${time(a.landing_minute)} + ${numberLabel(a.taxi_minutes)} minutes taxi = arrive at ${a.gate} at ${time(a.gateArrival)}.`),
      problem: gap > 0 ? say(`飞机会早到 ${numberLabel(gap)} 分钟，那时 ${a.gate} 还不能停靠。`, `The aircraft arrives ${numberLabel(gap)} minutes before ${a.gate} is available.`)
        : say(`${a.flight_id} 不会早于机位可用时间到达。这一项时间检查通过。`, `${a.flight_id} does not arrive before the stand is available. This timing check passes.`),
      next: gap > 0 ? say(`重新给 ${a.flight_id} 选择更早空出的机位，或推迟到达 ${a.gate} 的时间。`, `Choose an earlier available stand for ${a.flight_id}, or delay its arrival at ${a.gate}.`)
        : bad ? say(`但 ${bad.flight_id} 到 ${bad.gate} 时机位还没空出，仍须修改它的安排。`, `However, ${bad.flight_id} reaches ${bad.gate} too early; that assignment still needs repair.`)
        : caution ? say("这架飞机的时间检查通过；整项机位检查仍有待办，请查看其他航班和详细记录。", "This flight's timing passes, but the stand check still has an outstanding item. Review other flights and the detailed record.")
        : say("继续检查其他航班是否争用同一机位，以及转机旅客来不来得及。下拉菜单可切换航班。", "Continue checking other flights for shared stands and tight connections. Use the selector to inspect another flight."),
      flightId: a.flight_id, selectableFlights: true, caution: caution || gap > 0 || !!bad};
  }

  if (check.id === "gate_to_passenger") {
    const risks = plan.risk_shield?.rows ?? [];
    const r = [...risks].filter(r => rows.some(a => a.flight_id === r.flight_id)).sort((a, b) => b.transfer_passengers - a.transfer_passengers)[0];
    const a = rows.find(a => a.flight_id === r?.flight_id);
    if (!r || !a || !numeric(r.upper_gate_delay_min) || !numeric(r.transfer_buffer_min)) return missing;
    const exposed = r.transfer_exposed;
    const effective = (r as typeof r & {effective_transfer_buffer_min?: number}).effective_transfer_buffer_min;
    const buffer = numeric(effective) ? effective : r.transfer_buffer_min;
    return {title: say(`${r.flight_id} 的 ${r.transfer_passengers} 名转机旅客来得及吗？`, `Can ${r.flight_id}'s ${r.transfer_passengers} connecting passengers make it?`),
      input: say(`这架飞机有 ${r.transfer_passengers} 名转机旅客；转机时间最多允许到机位晚 ${numberLabel(buffer)} 分钟。`,
        `This flight has ${r.transfer_passengers} connecting passengers; its connection buffer allows ${numberLabel(buffer)} minutes of gate-arrival delay.`),
      schedule: say(`${r.flight_id} 计划${time(a.gateArrival)}到 ${a.gate}。仿真较保守的到机位延误估计为 ${numberLabel(r.upper_gate_delay_min)} 分钟。`,
        `${r.flight_id} is scheduled at ${a.gate} at ${time(a.gateArrival)}. Simulation estimates a conservative gate delay of ${numberLabel(r.upper_gate_delay_min)} minutes.`),
      problem: exposed ? say("预计延误超过了这批旅客能承受的转机等待时间，有人可能赶不上下一班飞机。", "The estimated delay exceeds this group's connection buffer; some passengers may miss their next flight.")
        : say("这项估计没有超过转机时间限制；它是预测，不能保证每位旅客都赶得上。", "This estimate is within the connection buffer. It is a forecast, not a guarantee for every passenger."),
      next: watching ? say("地勤查询已经完成。继续确认实际到机位时间，必要时安排地面接驳或改签。", "The ground query is complete. Track actual stand arrival and arrange ground transfer or rebooking if needed.")
        : exposed || caution ? say(`查询 ${a.gate} 和地面接驳是否能及时就绪；仍来不及时，为旅客安排后续帮助。`, `Check whether ${a.gate} and ground transport will be ready; arrange passenger assistance if connections remain at risk.`)
        : say("继续执行前检查；若飞机继续晚点，再重新估计转机是否来得及。", "Continue the pre-execution checks; recalculate connections if the aircraft is delayed further."),
      flightId: r.flight_id, caution: caution || exposed};
  }

  if (check.id === "safety_to_coordinator") {
    const gate = candidateDecisionEvidence(session, planId).gate;
    const fuel = plan.risk_shield?.rows.filter(r => r.fuel_level !== "clear") ?? [];
    const issue = fuel.map(r => say(`${r.flight_id}：较晚落地估计${time(r.eldt_upper_90)}，保留燃油余量后的落地期限为${time(r.fuel_reserve_deadline)}。`,
      `${r.flight_id}: late landing estimate ${time(r.eldt_upper_90)}, reserve-adjusted fuel deadline ${time(r.fuel_reserve_deadline)}.`)).join(" ");
    const ready = gate?.status === "ready" && gate.can_human_approve;
    if (gate?.human_approved && ready && !check.hard_block) return {
      title: say("这份计划已经批准了吗？", "Has this plan already been approved?"),
      input: say("这份计划已有人确认批准。下面保留的是这份计划的检查结果。", "A person has approved this plan. These are its saved check results."),
      schedule: say(`计划安排 ${rows.length} 架飞机；具体时间和位置见下方时间表。`, `The plan schedules ${rows.length} flights; times and locations are listed below.`),
      problem: issue || say("没有新的检查问题记录在此处；实际执行进展以机场现场和回放为准。", "No new check issue is recorded here. Use the airport view or replay for execution progress."),
      next: say("查看机场现场或回放，确认哪些飞机已经落地、到达机位和完成地面服务。", "Check the airport view or replay for actual landings, stand arrivals and ground-service completion."),
      caution: fuel.length > 0,
    };
    return {title: say("整份计划现在可以交给人批准吗？", "Can the complete plan be submitted for approval?"),
      input: issue || say("汇总前面的跑道、机位、转机和燃油检查记录。", "Review the runway, stand, connection and fuel check results."),
      schedule: say(`本方案安排 ${rows.length} 架飞机，使用 ${[...new Set(rows.map(r => r.runway))].join("、")}，具体时间见下方整份计划。`,
        `This plan schedules ${rows.length} flights on ${[...new Set(rows.map(r => r.runway))].join(", ")}. The complete schedule is below.`),
      problem: !gate ? say("本方案没有保存批准条件，暂时不能确认是否可以批准。", "Approval requirements were not saved for this candidate.")
        : gate.status === "blocked" || check.hard_block ? say("至少一项必须满足的检查未通过，目前不能批准执行。", "At least one mandatory check failed. Execution cannot be approved.")
        : gate.status === "needs_evidence" ? say("还有天气或地勤信息要查询，暂时不能批准。", "Weather or ground information is still required before approval.")
        : ready ? say("本方案已满足当前记录的批准条件；仍需要人确认后才能执行。", "The recorded approval requirements are met; a person must still confirm execution.")
        : say("需要人查看风险并决定是否接受，系统不能自行开始执行。", "A person must review the risks and decide whether to accept them; execution cannot start automatically."),
      next: !gate ? say("重新生成方案并补齐检查记录。", "Generate a new plan with complete review records.")
        : gate.status === "blocked" || check.hard_block ? say("先修改有问题的航班安排，再重新检查整份计划。", "Repair the affected assignments, then check the complete plan again.")
        : gate.status === "needs_evidence" ? say("先完成页面提示的天气或地勤查询，再重新检查方案。", "Complete the requested weather or ground query, then review the plan again.")
        : say("回到方案卡查看风险和批准按钮；批准后才按时间表执行。", "Return to the plan card to review risks and the approval control. Execute the schedule only after approval."),
      caution: caution || !ready};
  }
  return missing;
}

export interface HandoffStory {
  plain: PlainCheck;
  message: string;
  recipientBasis: string;
  comparison?: string;
}

export function handoffStory(session: SessionState, planId: string, check: AgentContractCheck, language: Language, focus?: string): HandoffStory {
  const zh = language === "zh", say = (a: string, b: string) => zh ? a : b;
  const plain = explainCheck(session, planId, check, language, focus);
  const rows = scheduleRows(session, planId);
  const plan = session.plans.find(p => p.id === planId);
  const a = rows.find(r => r.flight_id === plain.flightId);
  let message = plain.input, recipientBasis = plain.schedule, comparison: string | undefined;
  if (check.id === "runway_to_gate") {
    message = a ? say(`${a.flight_id} 计划${atMinute(a.landing_minute, language)}在 ${a.runway} 落地，安排停靠 ${a.gate}。`,
      `${a.flight_id} is scheduled to land on ${a.runway} at ${atMinute(a.landing_minute, language)}, with stand ${a.gate} assigned.`) : plain.schedule;
    recipientBasis = plain.input;
    if (a && numeric(a.gateAvailable) && numeric(a.gateArrival) && numeric(a.taxi_minutes)) {
      comparison = say(`${numberLabel(a.landing_minute)} 分落地 + ${numberLabel(a.taxi_minutes)} 分滑行 = 第 ${numberLabel(a.gateArrival)} 分到机位；${a.gate} 第 ${numberLabel(a.gateAvailable)} 分可用。`,
        `Landing ${numberLabel(a.landing_minute)} + taxi ${numberLabel(a.taxi_minutes)} = stand arrival ${numberLabel(a.gateArrival)} min; ${a.gate} is available at ${numberLabel(a.gateAvailable)} min.`);
    }
  } else if (check.id === "gate_to_passenger") {
    message = plain.schedule;
    recipientBasis = plain.input;
    const risk = plan?.risk_shield?.rows.find(r => r.flight_id === plain.flightId);
    const effective = (risk as (typeof risk & {effective_transfer_buffer_min?: number}))?.effective_transfer_buffer_min;
    const buffer = numeric(effective) ? effective : risk?.transfer_buffer_min;
    if (risk && numeric(buffer) && numeric(risk.upper_gate_delay_min)) {
      const delay = risk.upper_gate_delay_min, margin = buffer - delay;
      comparison = say(
        `预计晚 ${numberLabel(delay)} 分钟 ${margin >= 0 ? "≤" : ">"} 允许晚 ${numberLabel(buffer)} 分钟 → ${margin >= 0 ? "还留有 " + numberLabel(margin) + " 分钟余量" : "超过 " + numberLabel(-margin) + " 分钟"}。`,
        `Estimated delay ${numberLabel(delay)} min ${margin >= 0 ? "≤" : ">"} allowed ${numberLabel(buffer)} min → ${margin >= 0 ? numberLabel(margin) + " min buffer remains" : numberLabel(-margin) + " min over the limit"}.`);
    }
  } else if (check.id === "safety_to_coordinator") {
    const fuel = plan?.risk_shield?.rows.filter(r => r.fuel_level !== "clear") ?? [];
    const fuelMessage = fuel.map(r => say(
      `${r.flight_id} 较晚落地估计为${atMinute(r.eldt_upper_90, language)}；保留燃油余量后的落地期限为${atMinute(r.fuel_reserve_deadline, language)}。`,
      `${r.flight_id}: late landing estimate ${atMinute(r.eldt_upper_90, language)}; reserve-adjusted deadline ${atMinute(r.fuel_reserve_deadline, language)}.`)).join(" ");
    message = say(`这份方案安排 ${rows.length} 架飞机，使用 ${[...new Set(rows.map(r => r.runway))].join("、")}。`,
      `This candidate schedules ${rows.length} flights on ${[...new Set(rows.map(r => r.runway))].join(", ")}.`)
      + " " + (fuelMessage || say("跑道、机位、转机和燃油检查结果已经交给协调助手核对批准条件。", "Runway, stand, connection and fuel results are available to the coordinator for its approval review."));
    recipientBasis = say("协调助手核对：还有没有未完成的查询或必须修改的安排，以及有没有人工批准记录。", "The coordinator checks for outstanding queries, mandatory repairs and a recorded human approval.");
  }
  return {plain, message, recipientBasis, comparison};
}
