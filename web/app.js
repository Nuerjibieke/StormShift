(() => {
  "use strict";

  const PHASE_ORDER = ["normal", "disruption", "negotiating", "executing", "recovered"];
  const $ = (id) => document.getElementById(id);
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  const state = {
    phase: "normal",
    paused: false,
    speed: 1,
    missionSeconds: 0,
    preference: "balanced",
    planData: null,
    execution: 0,
    selected: null,
  };

  let world = null;

  function panelNormal() {
    $("decisionPanel").innerHTML = `
      <span class="panel-kicker">MISSION BRIEF</span>
      <h1 class="panel-title">晚高峰运行正常</h1>
      <p class="panel-copy">两条跑道开放，五架航班正在按计划运行。你是今晚的机场恢复指挥官。</p>
      <div class="panel-illustration"><div class="panel-plane"></div></div>
      <div class="panel-prompt">点击地图中的飞机、跑道或登机口查看状态。准备好后，触发预设暴雨事件。</div>
      <button class="primary-action full-button" data-action="storm">开始演示</button>`;
  }

  function panelDisruption() {
    $("decisionPanel").innerHTML = `
      <span class="panel-kicker" style="color:var(--red)">DISRUPTION</span>
      <h2 class="panel-title">单跑道危机</h2>
      <p class="panel-copy">07R 因雷击检查关闭。SK218 即将抵达，但原定 A2 仍被延误航班占用。</p>
      <div class="issue-list">
        <div class="issue-row"><i>!</i><span><strong>登机口冲突</strong><br>A2 同时关联 AX106 与 SK218</span></div>
        <div class="issue-row"><i>!</i><span><strong>转机风险</strong><br>62 名 SK218 旅客时间紧张</span></div>
        <div class="issue-row"><i>!</i><span><strong>跑道拥堵</strong><br>三架进港航班共享 07L</span></div>
      </div>
      <button class="primary-action full-button" data-action="coordinate">启动 Agent 协调</button>`;
  }

  function panelNegotiating(proposals = []) {
    const agents = [["RW", "跑道 Agent"], ["GT", "登机口 Agent"], ["AL", "航司 Agent"]];
    const cards = agents.map((agent, index) => {
      const proposal = proposals[index];
      return `<div class="agent-card ${proposal ? "" : "agent-loading"}">
        <div class="agent-avatar">${agent[0]}</div>
        <div><strong>${agent[1]}</strong><p>${proposal ? proposal.recommendation : "正在评估约束与影响…"}</p></div>
        <span class="agent-state">${proposal ? "完成" : "分析中"}</span>
      </div>`;
    }).join("");
    $("decisionPanel").innerHTML = `
      <span class="panel-kicker">MULTI-AGENT</span>
      <h2 class="panel-title">正在协商恢复方案</h2>
      <p class="panel-copy">每个 Agent 独立优化自己的运行目标，再由协调器验证组合方案。</p>
      <div class="agent-list">${cards}</div>`;
  }

  function panelProposal(data) {
    const actions = data.plan.actions.slice(0, 3).map((action, index) =>
      `<div class="plan-row"><i>${index + 1}</i><span>${action}</span></div>`).join("");
    $("decisionPanel").innerHTML = `
      <span class="panel-kicker" style="color:var(--amber)">RECOVERY PLAN</span>
      <h2 class="panel-title">方案已生成</h2>
      <p class="panel-copy">黄色路径是执行前预览。方案已通过跑道、登机口和滑行冲突检查。</p>
      <div class="plan-list">${actions}</div>
      <div class="validation">✓ 约束验证通过 · 预计清除全部冲突</div>
      <div class="preference-tabs">
        <button data-pref="balanced" class="${state.preference === "balanced" ? "active" : ""}">均衡</button>
        <button data-pref="connections" class="${state.preference === "connections" ? "active" : ""}">保护转机</button>
        <button data-pref="capacity" class="${state.preference === "capacity" ? "active" : ""}">保障容量</button>
      </div>
      <div class="panel-actions">
        <button class="primary-action" data-action="approve">批准执行</button>
        <button class="secondary-button" data-action="modify">调整</button>
        <button class="secondary-button" data-action="reject">拒绝</button>
      </div>`;
  }

  function panelExecuting() {
    $("decisionPanel").innerHTML = `
      <span class="panel-kicker">PLAN EXECUTION</span>
      <h2 class="panel-title">机场正在恢复</h2>
      <p class="panel-copy">青色路径表示已下发并正在执行的调度指令。</p>
      <div class="plan-list">
        <div class="plan-row"><i>1</i><span>SK218 优先进入 07L</span></div>
        <div class="plan-row"><i>2</i><span>释放 A2 冲突并启用 A4</span></div>
        <div class="plan-row"><i>3</i><span>其他航班按新序列移动</span></div>
      </div>
      <div class="execution-meter"><i id="executionBar"></i></div>
      <div class="execution-label"><span id="executionLabel">执行中</span><strong id="executionPercent">0%</strong></div>`;
  }

  function panelRecovered() {
    const data = state.planData;
    $("decisionPanel").innerHTML = `
      <span class="panel-kicker" style="color:var(--green)">RECOVERED</span>
      <h2 class="panel-title">恢复方案已完成</h2>
      <p class="panel-copy">所有冲突已经解除，机场进入稳定的单跑道运行状态。</p>
      <div class="metric-grid">
        <div class="metric"><span>平均延误</span><strong>${data.outcome.average_delay_min} min</strong><small>基线 ${data.baseline.average_delay_min} min</small></div>
        <div class="metric"><span>错过转机</span><strong>${data.outcome.missed_connections} 人</strong><small>基线 ${data.baseline.missed_connections} 人</small></div>
        <div class="metric wide"><span>剩余冲突</span><strong>0</strong></div>
      </div>
      <p class="compare-note">与无 Agent 协调的基线相比，本方案保护了 ${data.outcome.connections_protected} 名转机旅客。</p>
      <button class="primary-action full-button" data-action="reset">重新演示</button>`;
  }

  function setPhase(phase) {
    state.phase = phase;
    updateChrome();
    if (phase === "normal") panelNormal();
    if (phase === "disruption") panelDisruption();
    if (phase === "negotiating") panelNegotiating();
    if (phase === "proposal") panelProposal(state.planData);
    if (phase === "executing") panelExecuting();
    if (phase === "recovered") {
      panelRecovered();
      showToast("恢复完成：所有运行冲突已清除");
    }
    if (world) world.syncPhase();
  }

  function updateChrome() {
    const disrupted = state.phase !== "normal";
    $("weatherValue").textContent = disrupted ? "强雷暴" : "正常";
    $("weatherValue").className = disrupted ? "status-alert" : "status-good";
    $("runwayValue").textContent = disrupted ? "1 / 2" : "2 / 2";
    const conflicts = state.phase === "normal" || state.phase === "recovered" ? 0 :
      state.phase === "executing" ? Math.max(0, 3 - Math.floor(state.execution * 4)) : 3;
    $("conflictValue").textContent = conflicts;
    $("conflictValue").className = conflicts ? "status-alert" : "";
    $("eventBanner").classList.toggle("hidden", !disrupted || state.phase === "recovered");
    $("stormButton").disabled = disrupted;
    $("stormButton").innerHTML = disrupted ? "暴雨事件进行中" : '<span class="button-dot"></span>触发暴雨';

    const timelinePhase = state.phase === "proposal" ? "negotiating" : state.phase;
    const index = PHASE_ORDER.indexOf(timelinePhase);
    document.querySelectorAll(".timeline-step").forEach((step, stepIndex) => {
      step.classList.toggle("active", stepIndex === index);
      step.classList.toggle("done", stepIndex < index);
    });
    $("timelineProgress").style.width = `${index * 25}%`;
  }

  async function startNegotiation(preference = state.preference) {
    state.preference = preference;
    setPhase("negotiating");
    let data;
    try {
      const response = await fetch("/api/negotiate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preference }),
      });
      if (!response.ok) throw new Error("API 请求失败");
      data = await response.json();
    } catch (_error) {
      showToast("协调服务暂时不可用，请重试");
      setPhase("disruption");
      return;
    }
    const visible = [];
    for (const proposal of data.proposals) {
      await wait(430);
      visible.push(proposal);
      panelNegotiating(visible);
    }
    await wait(520);
    state.planData = data;
    setPhase("proposal");
    showToast("协调器已生成并验证恢复方案");
  }

  function triggerStorm() {
    if (state.phase !== "normal") return;
    setPhase("disruption");
    showToast("强雷暴到达：07R 已关闭");
  }

  function approvePlan() {
    if (!state.planData || !state.planData.validation.valid) return;
    state.execution = 0;
    setPhase("executing");
    showToast("恢复计划已下发");
  }

  function resetDemo() {
    state.phase = "normal";
    state.missionSeconds = 0;
    state.execution = 0;
    state.planData = null;
    state.preference = "balanced";
    state.paused = false;
    selectObject(null);
    world.reset();
    setPhase("normal");
    updatePlayButton();
  }

  function updateExecutionUI() {
    if (state.phase !== "executing") return;
    const percent = Math.round(state.execution * 100);
    const bar = $("executionBar");
    if (bar) bar.style.setProperty("--progress", `${percent}%`);
    if ($("executionPercent")) $("executionPercent").textContent = `${percent}%`;
    if ($("executionLabel")) {
      $("executionLabel").textContent = percent < 34 ? "重新分配登机口" : percent < 70 ? "调整着陆序列" : "清除剩余冲突";
    }
    updateChrome();
  }

  function selectObject(item) {
    state.selected = item;
    const card = $("selectionCard");
    if (!item) {
      card.classList.add("hidden");
      return;
    }
    $("selectionKind").textContent = item.kind;
    $("selectionTitle").textContent = item.title;
    $("selectionDetail").textContent = item.detail;
    card.classList.remove("hidden");
  }

  let toastTimer;
  function showToast(message) {
    const toast = $("toast");
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("show"), 2200);
  }

  function updatePlayButton() {
    $("playIcon").textContent = state.paused ? "▶" : "Ⅱ";
    $("playButton").setAttribute("aria-label", state.paused ? "播放" : "暂停");
  }

  world = new window.StormShiftWorld("airportCanvas", {
    getState: () => state,
    onSelect: selectObject,
    onProgress: updateExecutionUI,
    onRecovered: () => {
      if (state.phase === "executing") setPhase("recovered");
    },
  });

  $("stormButton").addEventListener("click", triggerStorm);
  $("closeSelection").addEventListener("click", () => selectObject(null));
  $("playButton").addEventListener("click", () => {
    state.paused = !state.paused;
    updatePlayButton();
    showToast(state.paused ? "模拟已暂停" : "模拟继续");
  });
  document.querySelectorAll(".speed-button").forEach((button) => {
    button.addEventListener("click", () => {
      state.speed = Number(button.dataset.speed);
      document.querySelectorAll(".speed-button").forEach((item) => item.classList.toggle("active", item === button));
    });
  });
  $("decisionPanel").addEventListener("click", (event) => {
    const action = event.target.closest("[data-action]")?.dataset.action;
    const preference = event.target.closest("[data-pref]")?.dataset.pref;
    if (preference) startNegotiation(preference);
    if (action === "storm") triggerStorm();
    if (action === "coordinate") startNegotiation();
    if (action === "approve") approvePlan();
    if (action === "modify") {
      const next = state.preference === "balanced" ? "connections" : state.preference === "connections" ? "capacity" : "balanced";
      startNegotiation(next);
    }
    if (action === "reject") {
      state.planData = null;
      setPhase("disruption");
      showToast("方案已拒绝，可重新启动协调");
    }
    if (action === "reset") resetDemo();
  });

  let lastClock = performance.now();
  function updateClock(now) {
    const dt = Math.min((now - lastClock) / 1000, .2);
    lastClock = now;
    if (!state.paused && state.phase !== "normal") state.missionSeconds += dt * state.speed;
    const displayMinutes = 27 + Math.floor(state.missionSeconds / 18);
    $("clockValue").textContent = `18:${String(displayMinutes).padStart(2, "0")}`;
    $("missionTime").textContent = `${String(Math.floor(state.missionSeconds / 60)).padStart(2, "0")}:${String(Math.floor(state.missionSeconds % 60)).padStart(2, "0")}`;
    requestAnimationFrame(updateClock);
  }

  panelNormal();
  updateChrome();
  updatePlayButton();
  requestAnimationFrame(updateClock);
})();
