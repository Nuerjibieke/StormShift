export type UiLanguage = "zh" | "en";

const scenarioEnglish: Record<string, string> = {
  storm: "Severe Storm & Single-Runway Operations",
  vhhh_storm: "VHHH Public Layout · Synthetic Storm Recovery",
  gate_outage: "Gate Failure & Peak-Hour Cascade",
  medical: "Medical Diversion & Connection Recovery",
  sandbox: "Open Airport Recovery Sandbox",
};

const weatherEnglish: Record<string, string> = {
  "正常": "Normal",
  "强雷暴": "Severe thunderstorm",
  "阵雨": "Rain showers",
  "多云": "Cloudy",
  "不稳定天气": "Unstable weather",
  "动态强雷暴": "Dynamic severe thunderstorm",
  "动态降雨": "Dynamic rainfall",
  "实时：正常": "Live · Normal",
  "实时：轻度低能见度": "Live · Mild low visibility",
  "实时：降水/低能见度": "Live · Precipitation / low visibility",
  "实时：雷暴或强降水": "Live · Thunderstorm / heavy precipitation",
  "实时：严重对流": "Live · Severe convection",
};

const planChinese: Record<string,string> = {
  "均衡恢复":"均衡安排", "保护转机":"优先帮助转机旅客", "容量优先":"尽快恢复航班",
  "不确定性护航":"稳妥恢复", "漂移自适应护航":"适应天气变化",
  "低碳滑行":"减少耗油", "按指挥目标":"按你的目标安排",
};

const planEnglish: Record<string, string> = {
  "漂移自适应护航": "Adapt to Weather Changes",
  "均衡恢复": "Balanced Recovery",
  "保护转机": "Protect Connections",
  "容量优先": "Capacity First",
  "不确定性护航": "Uncertainty Guard",
  "低碳滑行": "Low-Carbon Taxi",
  "按指挥目标": "Operator Objective",
  "无优化 · FCFS": "No Optimization · FCFS",
};

const planProfileEnglish: Record<string, string> = {
  balanced: "Balanced Recovery",
  passenger: "Protect Connections",
  capacity: "Capacity First",
  resilient: "Uncertainty Guard",
  green: "Low-Carbon Taxi",
  operator: "Operator Objective",
};

const runtimeEnglish: Record<string, string> = {
  "一条跑道暂时关闭，一个停机位被占用，需要重新安排降落": "A runway is closed and a stand is occupied. Reschedule the arrivals.",
  "执行游标已同步 · 正在等待下一个真实里程碑": "Waiting for the next recorded aircraft or passenger action",
  "讲解停顿：故障已生效，已提交飞机保持不变": "Briefing pause: the tug has failed; committed aircraft actions stay fixed",
  "讲解停顿：风险闸门要求人工确认，系统没有擅自继续": "Briefing pause: the revised plan needs operator approval",
  "讲解停顿：未来动作已重算，已执行前缀保持不变": "Briefing pause: pending flights have been rescheduled; completed actions stay fixed",
  "拖车故障：先暂停受影响的航班，调整剩余安排": "Tug failure: hold affected flights and revise the remaining assignments",
  "新方案已生成 · 安全闸门等待操作员批准": "Revised plan ready; waiting for operator approval",
  "Safety Critic 已验证新未来 · 执行游标可以继续": "Safety checks passed; execution can continue",
  "恢复完成，查看航班进度和旅客登机结果": "Recovery complete; review aircraft movements and passenger boarding",
  "真实执行账本已完成并通过资源校验": "Execution complete; runway and stand checks passed",
  "原安排暂时无法继续，请审阅调整后的方案": "Review the revised assignments before continuing",
  "Safety Critic 要求人工确认；已提交飞机保持原状态": "Operator approval required; committed aircraft actions stay fixed",
  "跑道、停机位或燃油要求有冲突，请先处理": "Resolve the runway, stand or fuel conflict before continuing",
  "执行已停止，可从当前位置继续": "Execution stopped; continue from the current state",
  "正在执行：观察飞机与旅客的下一步": "Following the next aircraft and passenger action",
  "已暂停，点击“继续演示”接着看": "Paused; choose Resume to continue",
  "正在从保存的执行游标继续…": "Resuming from the saved execution state…",
  "恢复方案已批准 · 正在提交第一条执行事件…": "Plan approved; starting aircraft movements…",
  "候选证据、硬约束和 Agent 接口契约全部通过，可人工批准。": "Evidence and runway, stand and fuel checks passed. The operator may approve.",
  "先获取跑道观测，再由 CP-SAT 重算": "Check the runway reopening forecast, then generate a new schedule.",
  "已恢复保存的演练 · 可查看 Decision Lab 或任务回放": "Saved exercise restored · open Decision Lab or Replay",
  "已恢复保存的决策快照": "Saved decision snapshot restored",
  "已恢复保存的机场态势": "Saved airport state restored",
  "正在从事件日志重建机场运行…": "Reconstructing airport operations from the event log…",
  "飞机正在按恢复方案进近、落地并滑行至机位": "Aircraft are approaching, landing, and taxiing under the recovery plan",
  "讲解停顿：故障范围已隔离 · 确认后继续观察局部修复": "Briefing pause: disruption isolated · resume to inspect local repair",
  "讲解停顿：局部重规划已通过安全闸门 · 确认后继续执行": "Briefing pause: local replan passed the safety gate · resume execution",
  "二次故障：TUG-3 在 Taxiway C 失效": "Secondary failure: TUG-3 failed on Taxiway C",
  "Safety Critic 已验证新方案 · 动态航迹已自动切换": "Safety Critic verified the revised plan · trajectories switched automatically",
  "数字孪生回放完成": "Digital-twin replay complete",
  "机场运行稳定 · 恢复完成": "Airport operations stable · recovery complete",
  "已从事件日志复现完整运行链路": "Complete operating sequence reproduced from the event log",
  "完整恢复链路已写入可回放事件日志": "Complete recovery sequence saved to the replayable event log",
  "暴雨关闭 07R · A2 机位冲突": "Storm closed 07R · gate A2 conflict",
  "指挥意图已转换为可视化约束，请确认": "Operator intent converted into reviewable constraints · please confirm",
  "Objective Copilot 正在深度评估新目标…": "Objective Copilot is deeply evaluating the revised objective…",
  "Objective Copilot 正在按新目标重算…": "Objective Copilot is solving for the revised objective…",
  "深度模型评估边界条件…": "Deep model is evaluating boundary conditions…",
  "专业 Agent 并行分析…": "Specialist Agents are analyzing in parallel…",
  "目标已重算 · 新候选通过安全约束": "Objective re-solved · revised candidates passed safety constraints",
  "三个安全候选已就绪 · 等待操作员审批": "Three safe candidates ready · awaiting operator approval",
  "主动观测完成：输入已更新，CP-SAT 已生成新候选": "Active observation complete · inputs updated and CP-SAT generated new candidates",
  "恢复方案已批准 · 正在同步执行事件…": "Recovery plan approved · synchronizing execution events…",
  "无法启动演练": "Unable to start the exercise",
  "意图解析失败": "Intent parsing failed",
  "规划失败": "Planning failed",
  "主动观测失败": "Active observation failed",
  "偏好学习失败": "Preference learning failed",
  "执行失败": "Execution failed",
  "回放读取失败": "Unable to load replay",
};

export function scenarioLabel(
  id: string,
  fallback: string,
  language: UiLanguage,
): string {
  return language === "en" ? scenarioEnglish[id] ?? fallback : fallback;
}

export function weatherLabel(
  raw: string,
  severity: number,
  language: UiLanguage,
): string {
  if (language === "zh") return raw;
  const sandbox = raw.match(/^沙盘天气等级\s*(\d)$/);
  if (sandbox) return `Sandbox weather level ${sandbox[1]}`;
  return weatherEnglish[raw] ?? `Weather level ${severity}`;
}

export function dataBadgeLabel(
  mode: "synthetic" | "live" | "imported",
  raw: string,
  language: UiLanguage,
): string {
  if (language === "zh") return raw;
  if (mode === "synthetic") return "Synthetic data · fixed seed";
  if (mode === "imported") return "Imported replay data";
  if (raw === "实时位置 + 合成运营数据") return "Live positions + synthetic operations";
  if (raw === "实时数据不可用 · 已降级为合成数据") return "Live data unavailable · synthetic fallback";
  if (raw === "正在获取实时辅助数据") return "Loading live assist";
  return raw.includes("实时") ? "Live assist" : raw;
}

export function planTitleLabel(
  raw: string,
  profile: string | undefined,
  language: UiLanguage,
): string {
  if (language === "zh") return planChinese[raw] ?? raw;
  return planEnglish[raw] ?? (profile ? planProfileEnglish[profile] : undefined) ?? raw;
}

export function runtimeText(value: string, language: UiLanguage): string {
  if (language === "zh" || !value) return value;
  if (runtimeEnglish[value]) return runtimeEnglish[value];

  const localRepair = value.match(/^因果图锁定 (\d+) 架未受影响航班，仅开放 (\d+) 架进入局部重规划$/);
  if (localRepair) return `Keep ${localRepair[1]} unaffected flights unchanged; reschedule only the ${localRepair[2]} affected flights.`;

  const fuelRisk = value.match(/^燃油包络可执行；(\d+) 名转机旅客处于尾部风险，执行期间保持事件触发监控。$/);
  if (fuelRisk) return `Fuel limits are satisfied. ${fuelRisk[1]} connecting passengers are at risk in high-delay simulations; keep monitoring new disruptions.`;


  const inspected = value.match(/^只读历史现场 · #(\d+) · (T\+\d+:\d+)$/);
  if (inspected) return `Read-only historical world · #${inspected[1]} · ${inspected[2]}`;

  const paused = value.match(/^回放暂停于 T\+(.+) · 可继续选择后续事件$/);
  if (paused) return `Replay paused at T+${paused[1]} · select a later event to continue`;

  const acquiring = value.match(/^主动获取(.+) · 新证据到达后由 CP-SAT 重新求解$/);
  if (acquiring) {
    const target = acquiring[1] === "跑道开放时间"
      ? "runway reopening time"
      : "critical gate service chain";
    return `Acquiring ${target} · CP-SAT will re-solve after evidence arrives`;
  }

  const observed = value.match(/^(.+)已写入数据快照 · 候选方案已重算$/);
  if (observed) {
    const target = observed[1] === "跑道开放时间"
      ? "Runway reopening evidence"
      : "Critical gate evidence";
    return `${target} saved to the snapshot · candidates re-solved`;
  }

  return value;
}

const executionEventNames: Record<string, Record<UiLanguage, string>> = {
    scheduled_event: { zh: "开始监测运行变化", en: "Monitor operational changes" },
    plan_certificate_verified: { zh: "执行前方案核验通过", en: "Pre-execution plan verified" },
    gate_recovered: { zh: "机位恢复可用", en: "Stand available again" },
    plan_approved: { zh: "方案获批", en: "Plan approved" },
    execution_tick: { zh: "航班执行", en: "Flight execution" },
    runway_reopened: { zh: "跑道恢复开放", en: "Runway reopened" },
    ground_vehicle_failure: { zh: "地面车辆故障", en: "Ground vehicle failure" },
    trusted_state_reconciled: { zh: "可信事实与约束重建", en: "Trusted state reconciled" },
    automatic_replan: { zh: "自动重规划", en: "Automatic replan" },
    gate_outage: { zh: "动态机位故障", en: "Dynamic gate outage" },
    storm: { zh: "动态天气事件", en: "Dynamic weather event" },
    medical_diversion: { zh: "医疗备降", en: "Medical diversion" },
    automatic_policy_replan: { zh: "策略触发滚动重规划", en: "Policy-triggered rolling replan" },
    automatic_policy_replan_failed: { zh: "策略安全停机", en: "Policy safety stop" },
    session_created: { zh: "演练创建", en: "Exercise created" },
    clock_advanced: { zh: "时钟推进", en: "Clock advanced" },
    runway_released: { zh: "跑道占用结束", en: "Runway released" },
    gate_arrived: { zh: "飞机到达机位", en: "Gate arrived" },
    gate_released: { zh: "已完成本轮地面服务", en: "Gate released" },
    automatic_policy_replan_requires_approval: { zh: "重规划等待人工审批", en: "Replan awaits human approval" },
    execution_blocked: { zh: "执行安全阻断", en: "Execution blocked" },
    connection_disembarking: { zh: "转机旅客下机", en: "Connecting passengers deplane" },
    connection_transferring: { zh: "旅客步行转机", en: "Passengers transfer" },
    connection_waiting: { zh: "旅客在转机等候区", en: "Passengers at transfer lounge" },
    connection_aircraft_ready: { zh: "下一班飞机已到位，准备出发", en: "Connecting airframe assigned" },
    connection_boarding: { zh: "旅客开始登上下一班飞机", en: "Connecting flight boarding" },
    departure_pushback: { zh: "拖车将飞机移出停机位", en: "Departure pushback" },
    departure_taxi: { zh: "飞机沿地面通道驶向跑道", en: "Departure taxi" },
    departure_holding: { zh: "跑道外等待起飞", en: "Holding short" },
    departure_takeoff: { zh: "飞机加速起飞", en: "Takeoff roll" },
    departure_airborne: { zh: "飞机离地爬升", en: "Airborne" },
    connection_completed: { zh: "转机行程完成", en: "Connection completed" },
    connection_blocked: { zh: "出发暂停，先处理安全问题", en: "Departure safety hold" },
    connection_cancelled: { zh: "转机演示取消", en: "Connection cancelled" },
    recovered: { zh: "运行恢复", en: "Operations recovered" },
  };

export function executionEventLabel(type: string, language: UiLanguage): string {
  return executionEventNames[type]?.[language] ?? type.replaceAll("_", " ");
}

const transferSummary = {zh: "转机旅客", en: "CONNECTIONS"} as const;
export function terminalTransferLabel(count: number, language: UiLanguage): string {
  return `${transferSummary[language]} ${count}`;
}
