import { useEffect, useRef } from "react";
import Phaser from "phaser";
import { SceneFrameMeter } from "./sceneFrameMeter";
import { passengerGroups } from "./passengerView";
import { PassengerFlow, passengerWalkFrame } from "./passengerFlow";
import { PassengerTimeline } from "./passengerTimeline";
import { passengerHall, gatePassengerAccess, arrivingPassengerRoute } from "./passengerRoutes";
import { pointOnPath, type Point } from "./aircraftProjection";
import { standExitFrame, standEntryFrame, STAND_EXIT_MS, STAND_ENTRY_MS } from "./standHandover";
import { connectingPassenger, departurePosition, journeyStatus, type ConnectionGeometry } from "./connectionProjection";
import type { ConnectingDeparture } from "./types";
import { terminalTransferLabel } from "./localization";
import { allocateHoldingSlots, holdingLayout, holdingCaption, hiddenHoldingCaption, holdingStageLabel, completedFlightsLabel } from "./holdingPresentation";
import { executionFlightLedgerAt, runwayPlanDisclosureAt, runwayStateAt, runwayTimelineLabel } from "./playback";
import type { ExecutionEvent, Phase } from "./types";
import type { AirportMapState } from "./historicalWorld";
import { selectVisualPlan } from "./visualPlan";
import { aircraftStageAt, aircraftPositionAt, aircraftStageLabel, executionMilestones, apronSpineX } from "./aircraftProjection";

export type MapLayer = "conflicts" | "routes" | "passengers" | "risk";
type VisualAssignment = {
  flight_id: string;
  runway: string;
  gate: string;
  landing_minute: number;
};
type RunwayGeometry = { id: string; x: number; y: number; width: number; height: number };


class AirportScene extends Phaser.Scene {
  private state?: AirportMapState;
  private activeLayer: MapLayer = "conflicts";
  private selectedPlanId?: string;
  private focusFlightId?: string;
  private visiblePhase: Phase = "normal";
  private worldGraphics?: Phaser.GameObjects.Graphics;
  private runwayGraphics?: Phaser.GameObjects.Graphics;
  private overlayGraphics?: Phaser.GameObjects.Graphics;
  private executionGraphics?: Phaser.GameObjects.Graphics;
  private passengerGraphics?: Phaser.GameObjects.Graphics;
  private passengerLabels?: Phaser.GameObjects.Container;
  private weatherShade?: Phaser.GameObjects.Graphics;
  private rainGraphics?: Phaser.GameObjects.Graphics;
  private labels?: Phaser.GameObjects.Container;
  private runwayLabels?: Phaser.GameObjects.Container;
  private aircraft?: Phaser.GameObjects.Container;
  private aircraftLabels?: Phaser.GameObjects.Container;
  private language: "zh" | "en" = "zh";
  private playbackMinute = 0;
  private passengerFlow = new PassengerFlow(1);
  private passengerTimeline = new PassengerTimeline();
  private historicalPassengers = false;
  private arrivingPassengerLabels?: Phaser.GameObjects.Container;
  private arrivingLabelKey = "";
  private passengerRate = .5;
  private passengersPaused = false;
  private passengerLabelKey = "";
  private frameMeter = new SceneFrameMeter();
  private runwayRenderKey = "";
  private planeByFlight = new Map<string, Phaser.GameObjects.Container>();
  private holdingSlotByFlight = new Map<string, number>();
  private gateOccupants = new Map<string, string>();
  private standExits = new Map<string, {gate:string; progress:number}>();
  private standEntries = new Map<string, {gate:string; progress:number; fromOffset:Point; running:boolean}>();
  private aircraftPaused = false;
  private executionLedger: ExecutionEvent[] = [];
  private ledgerSessionId = "";
  private pulseSequence = -1;
  private pulseToken = 0;
  private renderKey = "";
  private rain: Array<{ x: number; y: number; speed: number; length: number }> = [];
  private onFlightSelect?: (flightId: string) => void;
  private onPassengerStageChange?: (sessionId:string,sourceFlightId:string,stage:ConnectingDeparture["passenger_stage"])=>void;
  private notifiedPassengerStage="";

  private pixelRatio = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
  private get viewWidth() { return this.scale.width / this.pixelRatio; }
  private get viewHeight() { return this.scale.height / this.pixelRatio; }
  private updateCamera() {
    this.cameras.main.setViewport(0, 0, this.scale.width, this.scale.height)
      .setZoom(this.pixelRatio).centerOn(this.viewWidth / 2, this.viewHeight / 2);
  }
  resizeViewport(width:number,height:number) {
    if (!this.sys.isActive()) return;
    const ratio = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
    const pixelsW = Math.round(width*ratio), pixelsH = Math.round(height*ratio);
    if (this.pixelRatio===ratio && this.scale.width===pixelsW && this.scale.height===pixelsH) return;
    this.pixelRatio = ratio;
    this.scale.setZoom(1 / ratio);
    this.scale.resize(pixelsW,pixelsH);
    this.game.canvas.style.width = width+"px";
    this.game.canvas.style.height = height+"px";
    Object.assign(this.game.canvas.dataset,{viewWidth:String(width),viewHeight:String(height),pixelRatio:String(ratio)});
    this.updateCamera();
    this.draw();
  }
  private readableText(x:number,y:number,text:string|string[],style:Phaser.Types.GameObjects.Text.TextStyle) {
    // A thin light outline keeps labels readable over storm shading and taxiways.
    // Labels with an existing background already have their own contrast.
    const outline = style.backgroundColor ? {} : {stroke: "#f7fbfa", strokeThickness: 3};
    return this.add.text(x,y,text,{...outline,...style,resolution:Math.max(2,this.pixelRatio)});
  }
  constructor() {
    super("airport");
  }

  setFlightSelectionHandler(handler?: (flightId: string) => void) {
    this.onFlightSelect = handler;
  }

  setPassengerStageHandler(handler?: (sessionId:string,sourceFlightId:string,stage:ConnectingDeparture["passenger_stage"])=>void) {
    this.onPassengerStageChange=handler;
  }

  private flightNear(x: number, y: number) {
    let nearest: { id: string; distance: number } | undefined;
    this.planeByFlight.forEach((plane, id) => {
      if (!plane.visible || plane.alpha < .2) return;
      const distance = Phaser.Math.Distance.Between(x, y, plane.x, plane.y);
      if (!nearest || distance < nearest.distance) nearest = { id, distance };
    });
    return nearest && nearest.distance <= 56 ? nearest.id : undefined;
  }

  selectFlightAtRatio(xRatio: number, yRatio: number) {
    const flightId = this.flightNear(xRatio * this.viewWidth, yRatio * this.viewHeight);
    if (flightId) this.onFlightSelect?.(flightId);
  }

  create() {
    this.cameras.main.setBackgroundColor("#e8f3ef");
    this.worldGraphics = this.add.graphics();
    this.runwayGraphics = this.add.graphics();
    this.overlayGraphics = this.add.graphics();
    this.executionGraphics = this.add.graphics();
    this.weatherShade = this.add.graphics();
    this.rainGraphics = this.add.graphics();
    this.passengerGraphics = this.add.graphics();
    this.passengerLabels = this.add.container(0, 0);
    this.arrivingPassengerLabels = this.add.container(0, 0);
    this.labels = this.add.container();
    this.runwayLabels = this.add.container();
    this.aircraft = this.add.container();
    this.aircraftLabels = this.add.container();
    this.scale.on("resize", () => { this.updateCamera(); this.draw(); });
    this.updateCamera();
    this.draw();
  }

  sync(
    state: AirportMapState,
    layer: MapLayer,
    executionEvents: ExecutionEvent[] = [],
    selectedPlanId?: string,
    playbackEvents: ExecutionEvent[] = [],
    playbackToken = 0,
    phase: Phase = state.phase,
    playbackMinute = state.scenario.current_minute,
    playbackRate = .5,
    playbackPaused = false,
    language: "zh" | "en" = "zh",
    focusFlightId?: string,
    pedestriansPaused = playbackPaused,
    historicalPlayback = false,
  ) {
    if (this.ledgerSessionId !== state.id || playbackMinute < this.playbackMinute - .001
      || (playbackToken !== this.pulseToken && !playbackEvents.length)) {
      this.clearStandHandovers();
    }
    this.aircraftPaused = playbackPaused;
    if (this.ledgerSessionId !== state.id) {
      this.ledgerSessionId = state.id;
      this.executionLedger = [];
      this.pulseSequence = executionEvents.at(-1)?.sequence ?? -1;
      this.aircraft?.removeAll(true);
      this.planeByFlight.clear();
      this.holdingSlotByFlight.clear();
      this.gateOccupants.clear();
      this.passengerFlow.reset();
      this.passengerTimeline.reset();
      this.notifiedPassengerStage="";
      this.arrivingLabelKey = "";
    }
    this.executionLedger = [...executionEvents];
    this.language = language;
    this.focusFlightId = focusFlightId;
    if (playbackToken !== this.pulseToken && !executionEvents.length) this.pulseSequence = -1;
    this.pulseToken = playbackToken;
    const nextRenderKey = JSON.stringify({
      scenario: [state.scenario.id, state.scenario.current_minute, state.scenario.weather.severity],
      runways: state.scenario.runways.map((item) => [item.id, item.status, item.reopen_minute]),
      gates: state.scenario.gates.map((item) => [item.id, item.status, item.occupied_by]),
      flights: state.scenario.flights.map((item) => [item.id, item.state]),
      plans: state.plans.map((plan) => [plan.id, plan.assignments]),
      selectedPlanId: selectedPlanId ?? state.selected_plan_id,
      focusFlightId, language,
      executionLedger: playbackEvents.length
        ? "playback-active"
        : this.executionLedger.map((event) => [event.sequence, event.type]),
      layer,
      phase: playbackEvents.length ? (phase === "recovered" ? "playback-recovered" : "playback") : phase,
    });
    const nextRunwayRenderKey = JSON.stringify(
      state.scenario.runways.map((runway) => [runway.id, runwayTimelineLabel(runway, playbackMinute, true)]),
    );
    const shouldDrawRunways = this.runwayRenderKey !== nextRunwayRenderKey;
    const shouldDraw = this.renderKey !== nextRenderKey;
    this.selectedPlanId = selectedPlanId ?? state.selected_plan_id ?? undefined;
    this.state = state;
    this.activeLayer = layer;
    this.visiblePhase = phase;
    this.playbackMinute = playbackMinute;
    this.passengerRate = playbackRate;
    this.passengersPaused = pedestriansPaused;
    this.historicalPassengers = historicalPlayback;
    const connection = state.execution?.connection;
    if(connection) this.passengerTimeline.sync(connection,playbackMinute);
    this.passengerFlow.sync(passengerGroups(state,this.executionLedger,playbackMinute)
      .filter(group=>group.flightId!==connection?.source_flight_id),playbackMinute);
    this.runwayRenderKey = nextRunwayRenderKey;
    if (!this.sys.isActive()) return;
    if (shouldDraw) {
      this.renderKey = nextRenderKey;
      this.draw();
    } else {
      if (shouldDrawRunways) this.drawRunwayTimeline();
      this.drawAircraft(this.viewWidth, this.viewHeight, this.runwayLayout(this.viewWidth, this.viewHeight), true);
    }
    this.time.timeScale = playbackRate;
    this.tweens.timeScale = playbackRate;
    if (playbackPaused) {
      this.time.paused = true;
      this.tweens.pauseAll();
    } else {
      this.time.paused = false;
      this.tweens.resumeAll();
    }
    const fresh = executionEvents.filter((event) =>
      event.sequence > this.pulseSequence && event.sim_minute <= playbackMinute);
    for (const event of fresh) {
      if (playbackEvents.length && (event.type === "ground_vehicle_failure" || event.type === "trusted_state_reconciled"
        || event.type.includes("replan") || event.type === "runway_reopened" || event.type === "recovered")) {
        this.showEventPulse(event);
      }
      this.pulseSequence = Math.max(this.pulseSequence, event.sequence);
    }
  }

  private runwayLayout(width: number, height: number): RunwayGeometry[] {
    const runways = this.state?.scenario.runways ?? [];
    const source = this.state?.scenario.layout?.runways ?? [];
    const fallbackYs = runways.length === 2
      ? [.28, .52]
      : runways.map((_, index) => .16 + index * (.64 / Math.max(1, runways.length - 1)));
    return runways.map((runway, index) => {
      const layout = source.find((item) => item.id === runway.id);
      return {
        id: runway.id,
        x: width * (layout?.x ?? .47),
        y: height * (layout?.y ?? fallbackYs[index] ?? .28),
        width: width * (layout?.width ?? .46),
        height: Math.max(30, height * (layout?.height ?? .065)),
      };
    });
  }

  private runwayGeometry(runwayId: string, width: number, height: number) {
    const runways = this.runwayLayout(width, height);
    return runways.find((item) => item.id === runwayId) ?? runways[0];
  }

  private terminalLayout(width: number, height: number) {
    const source = this.state?.scenario.layout?.terminal;
    const terminalW = source ? width * source.width : Math.max(210, Math.min(width * .25, 360));
    const terminalH = source ? height * source.height : Math.min(height * .56, terminalW * 1.35);
    const terminalX = source ? width * source.x : Math.max(28, width * .05);
    const terminalY = source ? height * source.y : Math.max(height * .19, Math.min(height * .28, (height - terminalH) / 2));
    const gateSpacing = Math.max(34, Math.min(42, width * .035));
    return {
      terminalX,
      terminalY,
      terminalW,
      terminalH,
      upperGateY: terminalY + terminalH * .18,
      lowerGateY: terminalY + terminalH * .82,
      gateXBase: terminalX + terminalW + 30,
      gateSpacing,
    };
  }

  private drawRunwayTimeline() {
    const g = this.runwayGraphics;
    const labels = this.runwayLabels;
    if (!g || !labels) return;
    g.clear();
    labels.removeAll(true);
    const width = this.viewWidth;
    const height = this.viewHeight;
    const runwayGeometries = this.runwayLayout(width, height);
    const runways = this.state?.scenario.runways ?? [];
    const activePlan = selectVisualPlan(this.state?.plans ?? [], this.selectedPlanId, this.state?.selected_plan_id);
    runwayGeometries.forEach(({ id, x: runwayX, y, width: runwayW, height: runwayH }) => {
      const runway = runways.find((item) => item.id === id);
      const timelineState = runway ? runwayStateAt(runway, this.playbackMinute, true) : "open";
      const closed = timelineState === "closed";
      const disclosure = runway
        ? runwayPlanDisclosureAt(
          runway,
          (activePlan?.assignments ?? [])
            .filter((assignment) => assignment.runway === runway.id)
            .map((assignment) => assignment.landing_minute),
          this.playbackMinute,
          true,
        )
        : { firstLandingMinute: null, state: "none" as const };
      if (closed) {
        g.fillStyle(0xf7dcda, .98).fillRoundedRect(runwayX, y, runwayW, runwayH, 8);
        g.lineStyle(2, 0xcd5060, .62).strokeRoundedRect(runwayX, y, runwayW, runwayH, 8);
        g.lineStyle(3, 0xcd5060, .65);
        for (let x = runwayX + 42; x < runwayX + runwayW - 20; x += 76) {
          g.lineBetween(x - 7, y + runwayH / 2 - 7, x + 7, y + runwayH / 2 + 7);
          g.lineBetween(x + 7, y + runwayH / 2 - 7, x - 7, y + runwayH / 2 + 7);
        }
      } else if (timelineState === "reopened") {
        g.lineStyle(2, 0x148b76, .78).strokeRoundedRect(runwayX, y, runwayW, runwayH, 8);
      }
      const label = id + (this.language==="zh"?" 跑道":"") + (closed ? (this.language==="zh"?" · 暂时关闭":" · CLOSED") : (this.language==="zh"?" · 可以使用":" · OPEN"));
      const text = this.readableText(runwayX + 12, y - 23, label, {
        color: closed ? "#ad4050" : timelineState === "reopened" ? "#117969" : "#385e6e",
        fontFamily: "Inter, system-ui",
        fontSize: "15px",
        fontStyle: "600",
      });
      const plannedEntry = disclosure.firstLandingMinute == null
        ? "NO PLANNED ENTRY"
        : `FIRST PLANNED T+${disclosure.firstLandingMinute} · AFTER CLEARANCE`;
      const badgeText = closed ? (this.language==="zh"?"等待开放确认":"Awaiting reopening confirmation")
        + (runway?.reopen_minute != null ? (this.language==="zh"?" · 预计 T+":" · ETA T+") + runway.reopen_minute : "") : "";
      const badge = this.readableText(runwayX + runwayW - 10, y + runwayH / 2, badgeText, {
        color: closed ? "#9c3c4b" : "#106956",
        fontFamily: "Inter, system-ui",
        fontSize: "12px",
        fontStyle: "bold",
        backgroundColor: closed ? "#fff0eedd" : "#e4f5eddd",
        padding: { x: 7, y: 4 },
      }).setOrigin(1, .5).setVisible(Boolean(badgeText));
      labels.add([text, badge]);
    });
  }

  private draw() {
    const width = this.viewWidth;
    const height = this.viewHeight;
    const g = this.worldGraphics;
    const o = this.overlayGraphics;
    if (!g || !o || !this.labels || !this.aircraft) return;
    g.clear();
    o.clear();
    this.executionGraphics?.clear();
    this.labels.removeAll(true);
    this.gateOccupants.clear();

    g.fillStyle(0xe8f3ef, 1).fillRect(0, 0, width, height);
    g.fillStyle(0xe0eee7, 1).fillEllipse(width * .58, height * .54, width * 1.25, height * .9);
    g.fillStyle(0xd4e7dd, .72).fillEllipse(width * .34, height * .64, width * .72, height * .6);

    // A faint spatial grid provides scale without competing with operational routes.
    g.lineStyle(1, 0x2d5051, .05);
    for (let x = 0; x < width; x += 110) g.lineBetween(x, 0, x, height);
    for (let y = 0; y < height; y += 110) g.lineBetween(0, y, width, y);

    const runwayGeometries = this.runwayLayout(width, height);
    const runwayYs = runwayGeometries.map((item) => item.y);
    const runwayH = Math.max(...runwayGeometries.map((item) => item.height), 30);
    runwayGeometries.forEach(({ x, y, width: runwayW, height: runwayHeight }) => {
      g.fillStyle(0x657e88, .98);
      g.fillRoundedRect(x, y, runwayW, runwayHeight, 8);
      g.lineStyle(2, 0xb1c7c4, .34);
      g.strokeRoundedRect(x, y, runwayW, runwayHeight, 8);
      g.lineStyle(2, 0xe8f4e9, .48);
      for (let markX = x + 24; markX < x + runwayW - 18; markX += 44) {
        g.lineBetween(markX, y + runwayHeight / 2, markX + 21, y + runwayHeight / 2);
      }
    });
    this.drawRunwayTimeline();

    // Terminal campus: aspect-ratio bounded so tall windows never create an empty slab.
    const {
      terminalX, terminalY, terminalW, terminalH, upperGateY, lowerGateY, gateXBase,
    } = this.terminalLayout(width, height);

    g.lineStyle(1, 0x76cfc1, .1)
      .strokeRoundedRect(terminalX - 12, terminalY - 12, terminalW + 24, terminalH + 24, 23);
    g.fillStyle(0xfffefa, .98).fillRoundedRect(terminalX, terminalY, terminalW, terminalH, 18);
    g.lineStyle(2, 0x5ca5a1, .32).strokeRoundedRect(terminalX, terminalY, terminalW, terminalH, 18);
    g.fillStyle(0xe9f1f4, .96)
      .fillRoundedRect(terminalX + 14, terminalY + 54, terminalW - 28, terminalH - 74, 10);

    const gates = this.state?.scenario.gates ?? [];
    const activePlan = selectVisualPlan(this.state?.plans ?? [], this.selectedPlanId, this.state?.selected_plan_id);
    const hall=passengerHall({terminalX,terminalY,terminalW,terminalH},this.arrivalCohortCount());
    const boardX = terminalX + 18;
    const boardY = terminalY + 58;
    const boardW = terminalW - 72;
    const compactBoard = terminalH < 330;
    const columns = compactBoard ? 2 : 1;
    const rows = compactBoard ? 3 : 6;
    const rowW = boardW / columns - (compactBoard ? 4 : 0);
    const rowH = Math.max(13, Math.min(29, (hall.waitingTop - 19 - boardY) / rows));
    gates.slice(0, 6).forEach((gate, index) => {
      const committedIds = new Set((this.state?.execution?.committed_operations ?? []).map((item) => item.flight_id));
      const next = activePlan?.assignments
        .filter((item) => item.gate === gate.id && item.landing_minute >= this.playbackMinute && !committedIds.has(item.flight_id))
        .sort((a, b) => a.landing_minute - b.landing_minute)[0];
      const unavailable = ["blocked", "occupied"].includes(gate.status);
      const rowY = boardY + (index % rows) * rowH;
      const rowX = boardX + Math.floor(index / rows) * (boardW / columns);
      const accent = unavailable ? 0xcd5060 : 0x148b76;
      g.fillStyle(0xffffff, .9).fillRoundedRect(rowX, rowY, rowW, rowH - 3, 5);
      g.fillStyle(accent, .62).fillRoundedRect(rowX, rowY, 3, rowH - 3, 2);
      const gateLabel = this.readableText(rowX + 6, rowY + 4, gate.id, {
        color: unavailable ? "#b54453" : "#317c70",
        fontFamily: "Inter, system-ui", fontSize: "12px", fontStyle: "bold",
      });
      const c = this.state?.execution?.connection;
      const outLabel = c?.gate===gate.id && ["turnaround","boarding","pushback"].includes(c.stage)
        ? c.flight_id+(compactBoard?"":" · "+(this.language==="zh"?(c.stage==="boarding"?"旅客登机":c.stage==="pushback"?"离开停机位":"准备下一班"):"DEPARTURE")) : undefined;
      const flightLabel = this.readableText(rowX + rowW - 4, rowY + 4,
        outLabel ?? (next ? (compactBoard ? next.flight_id : `${next.flight_id} · ${this.language==="zh"?"预计":"ETA"} T+${next.landing_minute}`) : gate.occupied_by ?? (this.language==="zh"?({available:"可停靠",reserved:"已预留",blocked:"暂不可用",occupied:"已停靠",open:"可用",closed:"关闭"}[gate.status]??gate.status):gate.status.toUpperCase())), {
          color: next ? "#34586a" : "#385562",
          fontFamily: "Inter, system-ui", fontSize: "11px",
        }).setOrigin(1, 0);
      this.labels?.add([gateLabel, flightLabel]);
    });

    const terminalTitle = this.readableText(terminalX + 22, terminalY + 18, this.state?.scenario.layout?.terminal.label ?? (this.language==="zh"?"候机大厅 A":"TERMINAL A"), {
      color: "#385f71",
      fontFamily: "Inter, system-ui",
      fontSize: "14px",
      fontStyle: "bold",
      letterSpacing: 2,
    });
    const terminalCaption = this.readableText(terminalX + 22, terminalY + 36, this.language === "zh" ? "停机位置与航班" : "GATES & FLIGHTS", {
      color: "#3f5b68",
      fontFamily: "Inter, system-ui",
      fontSize: "11px",
    });
    const transferPassengerCount = this.state?.scenario.flights
      .reduce((sum, flight) => sum + flight.connections, 0) ?? 0;
    const terminalSummary = this.readableText(
      terminalX + terminalW - 22,
      terminalY + 36,
      terminalTransferLabel(transferPassengerCount, this.language),
      { color: "#966620", fontFamily: "Inter, system-ui", fontSize: "11px", fontStyle: "bold" },
    ).setOrigin(1, 0);
    this.labels?.add([terminalTitle, terminalCaption, terminalSummary]);

    gates.slice(0, 6).forEach((gate, index) => {
      const point = this.gatePoint(gate.id);
      const x = point.x;
      const y = point.y;
      const upper = y < terminalY + terminalH / 2;
      const unavailable = ["blocked", "occupied"].includes(gate.status);
      g.fillStyle(0x78c7bb, .28).fillCircle(x - 7, y, 2);
      g.fillStyle(unavailable ? 0xcd5060 : 0x148b76, unavailable ? .74 : .55).fillCircle(x, y, 5);
      const label = this.readableText(x - 11, y + (upper ? -61 : 48), gate.id, {
        color: unavailable ? "#b54453" : "#235e55",
        fontFamily: "Inter, system-ui",
        fontSize: "13px", fontStyle: "bold",
      });
      this.labels?.add(label);
      if (unavailable && this.activeLayer === "conflicts") {
        o.fillStyle(0xff4554, .07).fillCircle(x, y, 33);
        o.lineStyle(2, 0xcd5060, .45).strokeCircle(x, y, 20);
      }
    });

    const remoteGates = gates.filter((gate) => gate.remote).slice(0, 2);
    remoteGates.forEach((gate) => {
      const point = this.gatePoint(gate.id);
      g.fillStyle(0xf9fcfb, .96).fillRoundedRect(point.x - 18, point.y - 12, 36, 24, 7);
      g.lineStyle(1, 0x69cfbd, .32).strokeRoundedRect(point.x - 18, point.y - 12, 36, 24, 7);
      const remoteLabel = this.readableText(point.x, point.y + 17, `${gate.id} · ${this.language === "zh" ? "远处停机区" : "REMOTE STAND"}`, {
        color: "#235e55",
        fontFamily: "Inter, system-ui",
        fontSize: "11px",
        fontStyle: "bold",
      }).setOrigin(.5, 0);
      this.labels?.add(remoteLabel);
    });

    // Taxiways are broad, muted infrastructure bands. Thin connector lines were
    // visually indistinguishable from live flight paths on wide screens.
    const taxiSpineX = this.taxiSpineX();
    const standLanes = gates.map(gate=>({gate, point:this.gatePoint(gate.id), y:this.apronLaneY(gate.id)}));
    for (const {point,y} of standLanes) {
      g.lineStyle(12,0xd5b96c,.12).beginPath().moveTo(point.x,y)
        .lineTo(taxiSpineX,y).strokePath();
      g.lineStyle(5,0xd5b96c,.16).beginPath().moveTo(point.x,point.y)
        .lineTo(point.x,y).strokePath();
    }
    const taxiWidth = Math.max(28, taxiSpineX - gateXBase);
    const taxiSpineTop = Math.min(...standLanes.map(l=>l.y), (runwayYs[0] ?? upperGateY) + runwayH / 2);
    const taxiSpineBottom = Math.max(...standLanes.map(l=>l.y), (runwayYs[runwayYs.length - 1] ?? lowerGateY) + runwayH / 2);
    g.fillStyle(0xd5b96c, .075)
      .fillRoundedRect(taxiSpineX - 4, taxiSpineTop, 8, taxiSpineBottom - taxiSpineTop, 4);
    for (let y = taxiSpineTop + 18; y < taxiSpineBottom; y += 34) {
      g.fillStyle(0xe4cb7a, .26).fillCircle(taxiSpineX, y, 1.5);
    }
    const apronTag = this.readableText(gateXBase + taxiWidth * .48, upperGateY + 64 - 18, this.language === "zh" ? "飞机地面通道" : "APRON TAXI", {
      color: "#665323",
      fontFamily: "Inter, system-ui",
      fontSize: "12px",
      letterSpacing: 1,
    }).setOrigin(.5);
    const taxiSpineTag = this.readableText(taxiSpineX - 9, (taxiSpineTop + taxiSpineBottom) / 2, this.language==="zh"?"地面通道 C":"TAXI C", {
      color: "#665323",
      fontFamily: "Inter, system-ui",
      fontSize: "12px",
      letterSpacing: 1,
    }).setOrigin(.5).setAngle(-90);
    this.labels?.add([apronTag, taxiSpineTag]);

    const layout = this.state?.scenario.layout;
    if (layout?.source_type === "public_aip_simplified") {
      const sourceTag = this.readableText(
        width - 18,
        height - 38,
        `PUBLIC AIP LAYOUT · ${this.state?.scenario.airport_icao ?? "AIRPORT"} · EFFECTIVE ${layout.effective_date ?? "—"}`,
        { color: "#39796f", fontFamily: "Inter, system-ui", fontSize: "12px", fontStyle: "bold" },
      ).setOrigin(1, 0);
      const syntheticTag = this.readableText(
        width - 18,
        height - 22,
        "SIMPLIFIED GEOMETRY · SYNTHETIC FLIGHTS / GATES / WEATHER / CAPACITY",
        { color: "#506f7d", fontFamily: "Inter, system-ui", fontSize: "11px" },
      ).setOrigin(1, 0);
      this.labels.add([sourceTag, syntheticTag]);
    }

    this.drawPassengerConcourse();
    this.drawLayer(o, width, height, runwayGeometries);
    this.drawRecourse(o, width, height);
    this.drawAircraft(width, height, runwayGeometries);
    this.resetRain(width, height);
  }

  private drawLayer(
    g: Phaser.GameObjects.Graphics,
    width: number,
    height: number,
    runwayGeometries: RunwayGeometry[],
  ) {
    const plan = selectVisualPlan(this.state?.plans ?? [], this.selectedPlanId, this.state?.selected_plan_id);
    const inMotion = ["executing", "replanning", "monitoring"].includes(this.visiblePhase);
    if (this.activeLayer === "routes" && plan && plan.assignments.length && !inMotion && this.visiblePhase === "awaiting_approval") {
      // Preview one assignment as a runway highlight. The moving aircraft itself
      // carries the route during execution, so a second polyline is unnecessary.
      const assignment = plan.assignments[0];
      const runwayGeometry = runwayGeometries.find((item) => item.id === assignment.runway)
        ?? runwayGeometries[0];
      if (!runwayGeometry) return;
      const runwayX = runwayGeometry.x;
      const runwayY = runwayGeometry.y + runwayGeometry.height / 2;
      const destination = this.gatePoint(assignment.gate);
      const touchdownX = runwayX + runwayGeometry.width * .76;

      g.fillStyle(0x53f2d0, .10)
        .fillRoundedRect(runwayX + 8, runwayY - 7, touchdownX - runwayX - 8, 14, 7);
      for (let x = runwayX + 34; x < touchdownX; x += 54) {
        g.fillStyle(0x7cf6dc, .66)
          .fillTriangle(x - 5, runwayY - 4, x + 4, runwayY, x - 5, runwayY + 4);
      }
      g.fillStyle(0x53f2d0, .12).fillCircle(destination.x, destination.y, 15);
      g.lineStyle(2, 0x53f2d0, .62).strokeCircle(destination.x, destination.y, 9);

      const destinationTag = this.readableText(
        destination.x,
        destination.y + (destination.y < height * .5 ? 19 : -19),
        `DEST · ${assignment.gate}`,
        {
          color: "#227e6f",
          fontFamily: "Inter, system-ui",
          fontSize: "11px",
          fontStyle: "bold",
          backgroundColor: "#fffffff2",
          padding: { x: 5, y: 3 },
        },
      ).setOrigin(.5);
      const routeTag = this.readableText(
        runwayX + (touchdownX - runwayX) * .52,
        runwayY + 4,
        `${assignment.flight_id} · T+${assignment.landing_minute} · ${assignment.runway} → ${assignment.gate}`,
        {
          color: "#227e6f",
          fontFamily: "Inter, system-ui",
          fontSize: "13px",
          fontStyle: "bold",
          backgroundColor: "#fffffff2",
          padding: { x: 8, y: 5 },
        },
      ).setOrigin(.5);
      const queueTag = this.readableText(
        runwayX + (touchdownX - runwayX) * .52,
        runwayY + 20,
        `+${Math.max(0, plan.assignments.length - 1)} QUEUED · HIDDEN FOR CLARITY`,
        {
          color: "#506f7d",
          fontFamily: "Inter, system-ui",
          fontSize: "11px",
        },
      ).setOrigin(.5);
      queueTag.setVisible(false);
      this.labels?.add([destinationTag, routeTag, queueTag]);
    }
    if (this.activeLayer === "risk") {
      g.fillStyle(0xffb45d, .09).fillEllipse(width * .5, height * .48, width * .28, height * .34);
      g.lineStyle(2, 0xffc46f, .3).strokeEllipse(width * .5, height * .48, width * .28, height * .34);
    }
  }

  private drawRecourse(
    g: Phaser.GameObjects.Graphics,
    width: number,
    height: number,
  ) {
    const recourse = this.state?.decision_lab.recourse;
    if (!recourse || !["replanning", "monitoring"].includes(this.visiblePhase)) return;
    const plan = selectVisualPlan(this.state?.plans ?? [], this.selectedPlanId, this.state?.selected_plan_id);
    if (!plan) return;

    const source = { x: width * .47, y: height * .62 };
    const active = this.visiblePhase === "replanning";
    const color = active ? 0xff6974 : 0x148b76;
    g.fillStyle(color, .12).fillCircle(source.x, source.y, 25);
    g.lineStyle(2, color, .68).strokeCircle(source.x, source.y, 15);

    const gates = new Set<string>();
    for (const flightId of recourse.affected_flight_ids) {
      const assignment = plan.assignments.find((item) => item.flight_id === flightId);
      if (!assignment || gates.has(assignment.gate)) continue;
      gates.add(assignment.gate);
      const destination = this.gatePoint(assignment.gate);
      g.fillStyle(color, .16).fillCircle(destination.x, destination.y, 19);
      g.lineStyle(2, color, .64).strokeCircle(destination.x, destination.y, 10);
    }

    const caption = this.readableText(
      source.x + 22,
      source.y + 15,
      active
        ? (this.language==="zh"?`需调整 ${recourse.affected_count} 架航班`:`ADJUST ${recourse.affected_count} FLIGHTS`)
        : (this.language==="zh"?`保留 ${recourse.frozen_count} 架原安排`:`KEEP ${recourse.frozen_count} FLIGHTS`),
      {
        color: active ? "#b34254" : "#227e6f",
        fontFamily: "Inter, system-ui",
        fontSize: "13px",
        fontStyle: "bold",
        backgroundColor: "#fffffff2",
        padding: { x: 7, y: 4 },
      },
    );
    this.labels?.add(caption);
  }


  private drawPassengers() {
    const g=this.passengerGraphics,labels=this.passengerLabels,metrics=this.game.canvas.dataset;
    if(!g||!labels)return;
    g.clear();
    const connection=this.state?.execution?.connection;
    if(connection) this.drawConnectingPassengers(connection);
    else {
      labels.removeAll(true); this.passengerLabelKey="";
      metrics.passengerCohort="";metrics.passengerPositions="[]";metrics.passengerTokens="0";
    }
    this.drawArrivingPassengers();
  }

  private arrivalCohortCount() {
    const source=this.state?.execution?.connection?.source_flight_id;
    return this.state?.scenario.flights.filter(f=>f.connections>0 && f.id!==source).length??0;
  }

  private drawPassengerConcourse() {
    const g=this.worldGraphics;
    if(!g||!this.labels||!this.state)return;
    const t=this.terminalLayout(this.viewWidth,this.viewHeight);
    const hall=passengerHall(t,this.arrivalCohortCount());
    const gates=this.state.scenario.gates.map(gate=>this.gatePoint(gate.id));
    const access=gates.map(gate=>gatePassengerAccess(gate,hall.corridorX,t.terminalY+t.terminalH/2));
    const top=Math.min(t.terminalY+12,...access.map(path=>path.at(-1)!.y));
    const bottom=Math.max(hall.lounge.y,...access.map(path=>path.at(-1)!.y));
    // One continuous floor instead of a separate outlined rail for every passenger.
    g.fillStyle(0xf5f9f7,1).fillRoundedRect(hall.arrivalsX-10,top,38,bottom-top+10,10);
    g.fillStyle(0xf0f5f7,1).fillRoundedRect(t.terminalX+12,hall.waitingTop-12,
      t.terminalW-62,hall.lounge.y-hall.waitingTop+26,12);
    const bridges=new Map<number,number>();
    for(const path of access){
      const door=path[0],bend=path[1];
      bridges.set(bend.y,Math.max(bridges.get(bend.y)??hall.arrivalsX,bend.x));
      g.fillStyle(0xf7faf8,1).fillRoundedRect(door.x-8,Math.min(door.y,bend.y)-8,
        16,Math.abs(door.y-bend.y)+16,7);
    }
    for(const [y,right] of bridges){
      g.fillStyle(0xf7faf8,1).fillRoundedRect(hall.arrivalsX-10,y-10,
        right-hall.arrivalsX+20,20,8);
      g.lineStyle(1,0xcbdad3,.65).lineBetween(hall.arrivalsX,y-10,right,y-10);
    }
    // A visible doorway leads the other cohorts to the rest of the terminal.
    g.fillStyle(0xf5f9f7,1).fillRoundedRect(hall.otherExit.x-4,hall.otherExit.y-12,
      hall.arrivalsX-hall.otherExit.x+14,26,8);
    g.fillStyle(0xe8f3ef,1).fillRect(t.terminalX-3,hall.otherExit.y-13,7,28);
    this.labels.add(this.readableText(t.terminalX+90,hall.otherExit.y-28,
      this.language==="zh"?"← 其他登机区":"← OTHER GATES",
      {color:"#415a65",fontSize:"12px"}));
    this.labels.add(this.readableText(t.terminalX+20,hall.waitingTop-20,
      this.language==="zh"?"候机区":"WAITING AREA",
      {color:"#367665",fontSize:"11px",fontStyle:"bold"}));
    this.labels.add(this.readableText(hall.arrivalsX+9,t.terminalY-16,
      this.language==="zh"?"旅客通道":"WALKWAY",
      {color:"#27614f",fontSize:"12px",fontStyle:"bold"}).setOrigin(.5));
    this.game.canvas.dataset.passengerConcourse=JSON.stringify({
      gates:access,spine:{left:hall.arrivalsX-9,right:hall.corridorX+9,top,bottom},
      lounge:hall.lounge,otherExit:hall.otherExit,board:{x:t.terminalX+18,y:t.terminalY+58,width:t.terminalW-72,
        bottom:hall.waitingTop-19}});
  }

  private drawArrivingPassengers() {
    if(!this.state||!this.passengerGraphics||!this.arrivingPassengerLabels)return;
    const metrics=this.game.canvas.dataset,t=this.terminalLayout(this.viewWidth,this.viewHeight);
    const count=this.arrivalCohortCount(), hall=passengerHall(t,count),points=[];
    this.arrivingPassengerLabels.removeAll(true);
    for(const walk of this.passengerFlow.walkers){
      const path=arrivingPassengerRoute(this.gatePoint(walk.group.gate),t,walk.slot,count);
      const frame=passengerWalkFrame(walk.elapsedMs,0),point=pointOnPath(path,frame.progress);
      const doorFade=Math.max(0,Math.min(1,(point.x-hall.otherExit.x)/14));
      const alpha=frame.alpha*doorFade;
      points.push({...point,alpha,progress:frame.progress,flightId:walk.group.flightId,gate:walk.group.gate,
        stage:frame.progress>.96?"leaving":"walking"});
      this.drawWalkingPerson(point.x,point.y,alpha,frame.stride,0x577c96);
    }
    metrics.arrivingPassengerPositions=JSON.stringify(points);
    metrics.arrivingPassengerFlights=JSON.stringify(points.map(p=>p.flightId));
    metrics.passengerQueue=String(this.passengerFlow.queued);
    metrics.completedPassengerFlights=JSON.stringify(this.passengerFlow.settled.map(p=>p.group.flightId));
  }

  private drawWalkingPerson(x:number,y:number,alpha:number,stride:number,color:number) {
    const g=this.passengerGraphics!,py=y-Math.abs(stride)*.6;
    g.fillStyle(0x35596a,alpha*.12).fillEllipse(x,y+10,10,4);
    g.fillStyle(color,alpha).fillCircle(x,py-5,3).fillRoundedRect(x-3.5,py,7,8,2);
    g.lineStyle(1.8,color,alpha).lineBetween(x-1.5,py+6,x-2-stride*1.5,py+12)
      .lineBetween(x+1.5,py+6,x+2+stride*1.5,py+12);
  }

  private connectionGeometry(c:ConnectingDeparture):ConnectionGeometry {
    const t=this.terminalLayout(this.viewWidth,this.viewHeight),hall=passengerHall(t,this.arrivalCohortCount());
    const sourceGate=this.gatePoint(c.source_gate??"A4"),gate=this.gatePoint(c.gate??c.source_gate??"A4");
    return {sourceGate,gate,sourceAccess:gatePassengerAccess(sourceGate,hall.corridorX,t.terminalY+t.terminalH/2),
      gateAccess:gatePassengerAccess(gate,hall.corridorX,t.terminalY+t.terminalH/2),
      corridorX:hall.corridorX,loungeY:hall.lounge.y,lounge:hall.lounge,terminalX:t.terminalX,
      runway:this.runwayGeometry(c.runway ?? "07L",this.viewWidth,this.viewHeight)
        ?? this.runwayLayout(this.viewWidth,this.viewHeight)[0],
      spineX:this.taxiSpineX(),width:this.viewWidth,
      apronLaneY:this.apronLaneY(c.gate ?? c.source_gate ?? "A4")};
  }

  private drawConnectingPassengers(c:ConnectingDeparture) {
    const g=this.passengerGraphics!, labels=this.passengerLabels!, geom=this.connectionGeometry(c);
    const metrics=this.game.canvas.dataset;
    const frame=this.historicalPassengers?{connection:c,minute:this.playbackMinute}:this.passengerTimeline.frame(c);
    const walking=frame?.connection??c;
    const stageKey=this.state!.id+":"+c.source_flight_id+":"+walking.passenger_stage;
    if(stageKey!==this.notifiedPassengerStage){
      this.notifiedPassengerStage=stageKey;
      this.onPassengerStageChange?.(this.state!.id,c.source_flight_id,walking.passenger_stage);
    }
    const points=[0].map(slot=>connectingPassenger(walking,frame?.minute??this.playbackMinute,slot,geom));
    metrics.passengerCohort=c.source_flight_id; metrics.passengerQueue="0";
    metrics.passengerTokens=String(points.filter(p=>p&&p.alpha>.01).length);
    metrics.passengerPositions=JSON.stringify(points.filter(Boolean));
    metrics.passengerStage=walking.passenger_stage; metrics.departureStage=c.stage;
    metrics.passengerAircraftStage=c.passenger_stage;
    const {terminalX:x,terminalY:y,terminalW:w,terminalH:h}=this.terminalLayout(this.viewWidth,this.viewHeight);
    const key=[x,y,w,h,this.language,c.source_flight_id,c.flight_id,c.passenger_stage].join(":");
    if(key!==this.passengerLabelKey){
      this.passengerLabelKey=key;labels.removeAll(true);
      labels.add(this.readableText(x+22,geom.loungeY+15,c.source_flight_id+" → "+c.flight_id,
        {color:"#35596a",fontFamily:"Inter, system-ui",fontSize:"12px",fontStyle:"bold"}));
    }
    points.forEach((p,slot)=>{
      if(!p)return;
      const color=[0x795da5,0x357a9a,0x27816c][slot];
      const stride=walking.passenger_stage==="waiting"?0:p.stride, py=p.y-Math.abs(stride)*.6;
      g.fillStyle(0x35596a,p.alpha*.12).fillEllipse(p.x,p.y+10,10,4);
      g.fillStyle(color,p.alpha).fillCircle(p.x,py-5,3).fillRoundedRect(p.x-3.5,py,7,8,2);
      g.lineStyle(1.8,color,p.alpha).lineBetween(p.x-1.5,py+6,p.x-2-stride*1.5,py+12)
        .lineBetween(p.x+1.5,py+6,p.x+2+stride*1.5,py+12);
    });
  }

  private drawDepartureAircraft(c:ConnectingDeparture) {
    if(!c.carrier_flight_id||!c.gate||!c.runway)return;
    if (this.standEntries.has(c.carrier_flight_id)) {
      this.drawStandEntry(c.carrier_flight_id);
      this.startStandEntry(c.carrier_flight_id);
      return;
    }
    const g=this.connectionGeometry(c),p=departurePosition(c,this.playbackMinute,g);
    let plane=this.planeByFlight.get(c.carrier_flight_id);
    if(!plane){
      plane=this.add.container(p.x,p.y);
      const body=this.add.graphics().fillStyle(0x267b7a,1).fillRoundedRect(-15,-3,30,6,3);
      body.fillTriangle(-2,-3,5,-12,7,-3).fillTriangle(-2,3,5,12,7,3)
        .fillTriangle(-13,-2,-10,-7,-7,-2).fillTriangle(-13,2,-10,7,-7,2);
      const label=this.readableText(0,-24,"",{color:"#176e66",fontSize:"13px",fontStyle:"bold"}).setOrigin(.5,0);
      const stage=this.readableText(0,17,"",{color:"#456a78",fontSize:"11px"}).setOrigin(.5,0);
      plane.add([body,label,stage]);plane.setData("body",body);plane.setData("label",label);plane.setData("stage",stage);
      plane.setData("focusRing",this.add.graphics().setVisible(false));
      this.aircraft?.add(plane);this.planeByFlight.set(c.carrier_flight_id,plane);
    }
    this.tweens.killTweensOf(plane);
    plane.setPosition(p.x,p.y).setScale(.9).setVisible(true).setAlpha(1);
    const body=plane.getData("body") as Phaser.GameObjects.Graphics;
    body.setAngle(p.angle);
    const label=plane.getData("label") as Phaser.GameObjects.Text,stage=plane.getData("stage") as Phaser.GameObjects.Text;
    const parked = ["turnaround","boarding","pushback"].includes(c.stage);
    label.setText(c.stage==="turnaround"?c.carrier_flight_id+" → "+c.flight_id:c.flight_id+" · "+(this.language==="zh"?"出港":"DEPARTURE"))
      .setPosition(0,parked?-43:-25).setOrigin(.5,0).setVisible(true).setColor("#176e66").setScale(1/plane.scaleX);
    const captions=this.language==="zh"?{turnaround:"准备下一班出发",boarding:"旅客正在上飞机",pushback:"拖车移出停机位",taxiing:"驶向 "+c.runway+" 跑道",holding_short:"等待允许起飞",takeoff:"加速起飞",airborne:"正在飞离机场",completed:"已起飞",blocked:"暂停处理问题",cancelled:"已取消",waiting_aircraft:"等待下一班飞机"}:
      {turnaround:"SAME AIRFRAME",boarding:"BOARDING",pushback:"PUSHBACK · DOORS CLOSED",taxiing:"TAXI TO "+c.runway,holding_short:"HOLD SHORT",takeoff:"TAKEOFF ROLL",airborne:"CLIMB OUT",completed:"DEPARTED",blocked:"SAFETY HOLD",cancelled:"CANCELLED",waiting_aircraft:"WAITING"};
    stage.setText(captions[c.stage]).setPosition(0,parked?-26:17).setOrigin(.5,0).setVisible(true).setScale(1/plane.scaleX);
    (plane.getData("focusRing") as Phaser.GameObjects.Graphics)?.setVisible(false);
    if(["turnaround","boarding","pushback"].includes(c.stage))this.gateOccupants.set(c.gate,c.flight_id);
    // The actor leaves beyond the viewport before being hidden, never vanishes on the runway.
    if(c.stage==="completed")plane.setVisible(false);
    this.game.canvas.dataset.departurePosition=JSON.stringify(p);
    this.game.canvas.dataset.departureFlight=c.flight_id;
    this.game.canvas.dataset.departureCarrier=c.carrier_flight_id;
  }


  private clearStandHandovers() {
    for (const transfer of [...this.standExits.values(), ...this.standEntries.values()]) {
      this.tweens.killTweensOf(transfer);
    }
    this.standExits.clear();
    this.standEntries.clear();
    this.planeByFlight.forEach(plane => {
      plane.setData("standGate", undefined).setData("renderStage", undefined);
    });
  }

  private refreshStandHandovers() {
    if (this.sys.isActive()) this.drawAircraft(
      this.viewWidth, this.viewHeight, this.runwayLayout(this.viewWidth, this.viewHeight),
    );
  }

  private exitFrame(gateId: string, progress: number) {
    return standExitFrame(progress, this.gatePoint(gateId), this.apronLaneY(gateId),
      (this.state?.scenario.gates ?? []).map(gate => this.gatePoint(gate.id)), this.taxiSpineX());
  }

  private drawStandExit(flightId: string) {
    const transfer = this.standExits.get(flightId), plane = this.planeByFlight.get(flightId);
    if (!transfer || !plane) return;
    const frame = this.exitFrame(transfer.gate, transfer.progress);
    plane.setPosition(frame.x, frame.y).setVisible(true).setAlpha(frame.alpha);
    (plane.getData("body") as Phaser.GameObjects.Graphics).setAngle(frame.angle);
  }

  private startStandExit(flightId: string, gate: string) {
    const transfer = {gate, progress:0};
    this.standExits.set(flightId, transfer);
    const plane = this.planeByFlight.get(flightId);
    if (plane) this.tweens.killTweensOf(plane);
    this.tweens.add({
      targets:transfer, progress:1, duration:STAND_EXIT_MS, ease:"Linear", paused:this.aircraftPaused,
      onUpdate:() => this.drawStandExit(flightId),
      onComplete:() => {
        this.standExits.delete(flightId);
        this.planeByFlight.get(flightId)?.setVisible(false);
        this.refreshStandHandovers();
      },
    });
  }

  private exitingFlight(gate: string) {
    return [...this.standExits].find(([,transfer]) => transfer.gate === gate)?.[0];
  }

  private drawStandEntry(flightId: string) {
    const transfer = this.standEntries.get(flightId), plane = this.planeByFlight.get(flightId);
    if (!transfer || !plane) return;
    const point = standEntryFrame(transfer.progress, this.gatePoint(transfer.gate), transfer.fromOffset);
    plane.setPosition(point.x,point.y).setVisible(true).setAlpha(1);
    (plane.getData("body") as Phaser.GameObjects.Graphics)
      .setAngle(Math.atan2(-transfer.fromOffset.y,-transfer.fromOffset.x)*180/Math.PI);
    const blocker = this.exitingFlight(transfer.gate);
    (plane.getData("stage") as Phaser.GameObjects.Text).setText(this.language === "zh"
      ? blocker ? `等 ${blocker} 移出 ${transfer.gate}` : `正在进入 ${transfer.gate}`
      : blocker ? `WAIT FOR ${blocker} TO LEAVE ${transfer.gate}` : `ENTERING ${transfer.gate}`)
      .setColor("#916a2a").setVisible(true);
  }

  private startStandEntry(flightId: string) {
    const transfer = this.standEntries.get(flightId);
    if (!transfer || transfer.running || this.exitingFlight(transfer.gate)) return;
    transfer.running = true;
    this.tweens.add({
      targets:transfer, progress:1, duration:STAND_ENTRY_MS, ease:"Sine.easeInOut", paused:this.aircraftPaused,
      onUpdate:() => this.drawStandEntry(flightId),
      onComplete:() => {
        this.standEntries.delete(flightId);
        this.refreshStandHandovers();
      },
    });
  }

  private drawAircraft(width: number, height: number, runwayGeometries: RunwayGeometry[], interpolate = false) {
    this.drawPassengers();
    const holding = holdingLayout(width, runwayGeometries[0]?.y ?? height * .28);
    const plan = selectVisualPlan(this.state?.plans ?? [], this.selectedPlanId, this.state?.selected_plan_id);
    const assignmentByFlight = new Map<string, VisualAssignment>(
      (plan?.assignments ?? []).map((assignment) => [assignment.flight_id, assignment]),
    );
    const executionAssignments = executionFlightLedgerAt(this.executionLedger, this.playbackMinute);
    executionAssignments.forEach((assignment) => {
      assignmentByFlight.set(assignment.flight_id, assignment);
    });
    const assignments = [...assignmentByFlight.values()];
    const landingByFlight = new Map(
      assignments.map((assignment) => [assignment.flight_id, assignment.landing_minute]),
    );
    const executedByFlight = new Map(
      executionAssignments
        .filter((assignment) => assignment.landing_minute <= this.playbackMinute)
        .map((assignment) => [assignment.flight_id, assignment]),
    );
    this.aircraftLabels?.removeAll(true);
    const operations = executionMilestones(
      this.state?.execution?.committed_operations ?? [], this.executionLedger, this.playbackMinute, this.state?.scenario.current_minute,
    );
    this.gateOccupants.clear();
    const flights = [...(this.state?.scenario.flights ?? [])].sort((left, right) =>
      (landingByFlight.get(left.id) ?? Number.MAX_SAFE_INTEGER)
        - (landingByFlight.get(right.id) ?? Number.MAX_SAFE_INTEGER) || left.id.localeCompare(right.id));
    const initialStandByFlight = new Map((this.state?.scenario.gates ?? [])
      .filter((gate) => gate.occupied_by)
      .map((gate) => [gate.occupied_by!, gate.id]));
    const executionScope = this.state?.execution?.scope_flight_ids;
    const finalSnapshot = this.visiblePhase === "recovered";
    // Waiting icons use reserved schematic slots, not queue positions.
    const dispositionByFlight = new Map(flights.map((flight) => {
      const operation = operations.get(flight.id);
      const stage = operation ? aircraftStageAt(operation, this.playbackMinute)
        : initialStandByFlight.has(flight.id) ? "at_gate" : "waiting";
      return [flight.id, { stage, parked: stage === "at_gate",
        moving: stage === "runway" || stage === "taxiing",
        departed: stage === "released" || flight.state === "diverted"
          || Boolean(executionScope && !executionScope.includes(flight.id) && !initialStandByFlight.has(flight.id)) }];
    }));
    const pendingFlightIds = flights
      .filter((flight) => {
        const disposition = dispositionByFlight.get(flight.id);
        return !disposition?.parked && !disposition?.departed && !disposition?.moving;
      })
      .map((flight) => flight.id);
    const visualSlots = allocateHoldingSlots(pendingFlightIds, this.holdingSlotByFlight, holding.capacity, this.focusFlightId);
    this.holdingSlotByFlight = visualSlots;

    const connection=this.state?.execution?.connection;
    // Observe every old occupant before placing any new arrival, including the
    // initial parked aircraft that are outside the inbound execution scope.
    for (const [flightId, plane] of this.planeByFlight) {
      const gate = plane.getData("standGate") as string | undefined;
      if (gate && dispositionByFlight.get(flightId)?.departed
        && flightId !== connection?.carrier_flight_id && !this.standExits.has(flightId)) {
        this.startStandExit(flightId, gate);
      }
    }
    const occupiedStands = new Map<string, string>();
    for (const flight of flights) {
      if (!dispositionByFlight.get(flight.id)?.parked) continue;
      const gate = operations.get(flight.id)?.gate ?? initialStandByFlight.get(flight.id);
      if (gate && !occupiedStands.has(gate)) occupiedStands.set(gate, flight.id);
    }
    if (connection?.gate && ["turnaround", "boarding", "pushback"].includes(connection.stage)) {
      occupiedStands.set(connection.gate, connection.carrier_flight_id ?? connection.flight_id);
    }
    for (const [flightId, transfer] of this.standExits) occupiedStands.set(transfer.gate, flightId);
    flights.slice(0, 20).forEach((flight) => {
      if(connection?.carrier_flight_id===flight.id && connection.gate) return;
      const operation = operations.get(flight.id);
      const assigned = operation ?? assignmentByFlight.get(flight.id);
      const runway = assigned?.runway
        ?? this.state?.scenario.runways.find((item) => item.status === "open")?.id
        ?? "07L";
      const disposition = dispositionByFlight.get(flight.id)
        ?? { stage: "waiting" as const, parked: false, moving: false, departed: false };
      const parkedGate = operation?.gate ?? initialStandByFlight.get(flight.id) ?? assigned?.gate ?? flight.planned_gate;
      const previousPlane = this.planeByFlight.get(flight.id);
      const previousStage = previousPlane?.getData("renderStage") as string | undefined;
      const gate = this.gatePoint(parkedGate);
      if (disposition.parked && previousPlane && previousStage && previousStage !== "at_gate"
        && Phaser.Math.Distance.Between(previousPlane.x,previousPlane.y,gate.x,gate.y)>.5
        && !this.standEntries.has(flight.id)) {
        const laneY = this.apronLaneY(parkedGate);
        const offsetY = previousPlane.y-gate.y;
        const onLeadIn = Math.abs(previousPlane.x-gate.x)<1
          && offsetY*(laneY-gate.y)>0 && Math.abs(offsetY)<=Math.abs(laneY-gate.y);
        this.standEntries.set(flight.id, {gate:parkedGate, progress:0, running:false,
          fromOffset:{x:0,y:onLeadIn?offsetY:laneY-gate.y}});
      }
      const exit = this.standExits.get(flight.id), entry = this.standEntries.get(flight.id);
      const parked = disposition.parked && !exit && !entry;
      const departedAfterTurnaround = disposition.departed && !exit;
      const parkedPoint = parked ? this.gatePoint(parkedGate) : undefined;
      const queueIndex = pendingFlightIds.indexOf(flight.id);
      const visualSlot = visualSlots.get(flight.id);
      const holdingVisible = visualSlot !== undefined;
      const isFocus = flight.id === this.focusFlightId;
      const holdingPoint = holding.point(visualSlot ?? 0);
      const geometry = this.runwayGeometry(runway, width, height);
      const projected = operation && geometry && disposition.moving
        ? aircraftPositionAt(operation, this.playbackMinute, geometry, this.gatePoint(operation.gate),
          this.taxiSpineX(), this.apronLaneY(operation.gate),
          true) // The final lead-in animates after the gate-arrival acknowledgement.
        : undefined;
      const transferPoint = exit ? this.exitFrame(exit.gate,exit.progress)
        : entry ? standEntryFrame(entry.progress,this.gatePoint(entry.gate),entry.fromOffset) : undefined;
      const x = transferPoint?.x ?? projected?.x ?? parkedPoint?.x ?? holdingPoint.x;
      const y = transferPoint?.y ?? projected?.y ?? parkedPoint?.y ?? holdingPoint.y;
      const color = flight.medical_priority ? 0xb34c4c : isFocus ? 0xca8b2c : queueIndex === 0 ? 0x167c69 : 0x376782;
      let plane = this.planeByFlight.get(flight.id);
      let body: Phaser.GameObjects.Graphics;
      let label: Phaser.GameObjects.Text;
      let stage: Phaser.GameObjects.Text;
      let focusRing: Phaser.GameObjects.Graphics;
      if (!plane) {
        plane = this.add.container(x, y);
        focusRing = this.add.graphics().lineStyle(1.5, 0xca8b2c, .85).strokeCircle(0, 0, 22);
        body = this.add.graphics();
        body.fillStyle(color, .92);
        body.fillRoundedRect(-15, -3, 30, 6, 3);
        body.fillTriangle(-2, -3, 5, -12, 7, -3);
        body.fillTriangle(-2, 3, 5, 12, 7, 3);
        body.fillTriangle(-13, -2, -10, -7, -7, -2);
        body.fillTriangle(-13, 2, -10, 7, -7, 2);
        label = this.readableText(20, -10, flight.id, {
          color: flight.medical_priority ? "#b34254" : "#31586e",
          fontFamily: "Inter, system-ui",
          fontSize: "13px",
          fontStyle: "bold",
        });
        stage = this.readableText(20, 3, "", {
          color: "#385562",
          fontFamily: "Inter, system-ui",
          fontSize: "11px",
          letterSpacing: .5,
        });
        plane.add([focusRing, body, label, stage]);
        plane.setData("focusRing", focusRing);
        plane.setData("color", color);
        plane.setData("flightId", flight.id);
        plane.setData("body", body);
        plane.setData("label", label);
        plane.setData("stage", stage);
        plane.setSize(112, 34).setInteractive({
          hitArea: new Phaser.Geom.Rectangle(-44, -17, 112, 34),
          hitAreaCallback: Phaser.Geom.Rectangle.Contains,
          useHandCursor: true,
        });
        this.aircraft?.add(plane);
        this.planeByFlight.set(flight.id, plane);
      } else {
        body = plane.getData("body") as Phaser.GameObjects.Graphics;
        label = plane.getData("label") as Phaser.GameObjects.Text;
        stage = plane.getData("stage") as Phaser.GameObjects.Text;
        focusRing = plane.getData("focusRing") as Phaser.GameObjects.Graphics;
        if (plane.getData("color") !== color) {
          body.clear().fillStyle(color, .92).fillRoundedRect(-15, -3, 30, 6, 3);
          body.fillTriangle(-2, -3, 5, -12, 7, -3).fillTriangle(-2, 3, 5, 12, 7, 3);
          body.fillTriangle(-13, -2, -10, -7, -7, -2).fillTriangle(-13, 2, -10, 7, -7, 2);
          plane.setData("color", color);
        }
      }

      this.tweens.killTweensOf(plane);
      // Interpolation is visual only: no onComplete callback changes operational facts.
      // Large changes (replay/resize/refresh) reconstruct the same ledger position.
      const distance = Phaser.Math.Distance.Between(plane.x, plane.y, x, y);
      if (interpolate && !exit && !entry && disposition.moving && distance < width * .15 && distance > .01) {
        this.tweens.add({ targets: plane, x, y, duration: 80, ease: "Linear" });
      } else {
        plane.setPosition(x, y);
      }
      if (operation && geometry && disposition.moving) {
        const ahead = aircraftPositionAt(operation, this.playbackMinute + .001, geometry,
          this.gatePoint(operation.gate), this.taxiSpineX(), this.apronLaneY(operation.gate),
          true);
        body.setAngle(projected?.waitingForStand ? (this.apronLaneY(parkedGate) > y ? -90 : 90) : disposition.stage === "runway" ? 180 : Phaser.Math.RadToDeg(Math.atan2(ahead.y - y, ahead.x - x)));
      } else body.setAngle(parked ? (this.apronLaneY(parkedGate)>y ? -90 : 90) : 0);
      plane.setAngle(0);
      const visible = !departedAfterTurnaround && (parked || disposition.moving || Boolean(exit || entry) || holdingVisible);
      focusRing.setVisible(isFocus && visible);
      label.setText(flight.id).setColor(isFocus ? "#956523" : parked ? "#167460" : "#31586e").setVisible(visible);
      const upperStand = parked && this.gatePoint(parkedGate).y
        < this.terminalLayout(width, height).terminalY + this.terminalLayout(width, height).terminalH / 2;
      const standIndex = this.state?.scenario.gates.findIndex(gate=>gate.id===parkedGate) ?? 0;
      const labelRow = standIndex % 3 === 1 ? 1 : 0;
      label.setOrigin(parked ? .5 : 0, 0).setPosition(parked ? 0 : 20,
        parked ? (upperStand ? 16+labelRow*20 : -24-labelRow*20) : -10);
      const exitBlocker = entry ? this.exitingFlight(entry.gate) : undefined;
      stage.setText(exit
        ? (this.language === "zh" ? `移出 ${exit.gate}` : `LEAVING ${exit.gate}`)
        : projected?.waitingForStand
        ? (this.language === "zh"
          ? occupiedStands.has(parkedGate) ? `等 ${occupiedStands.get(parkedGate)} 移出 ${parkedGate}` : `等待进入 ${parkedGate}`
          : occupiedStands.has(parkedGate) ? `WAIT FOR ${occupiedStands.get(parkedGate)} TO LEAVE ${parkedGate}` : `WAIT TO ENTER ${parkedGate}`)
        : operation || parked
        ? aircraftStageLabel(disposition.stage, parkedGate, runway, this.language)
        : executedByFlight.has(flight.id)
          ? (this.language === "zh" ? "历史记录缺少里程碑" : "MISSING MILESTONES")
          : holdingStageLabel(queueIndex + 1, runway, isFocus, this.language))
        .setColor(disposition.moving ? "#916a2a" : parked ? "#167460" : "#385562")
        .setVisible(visible && !parked);
      if (parked) this.gateOccupants.set(parkedGate, flight.id);
      plane.setScale(parked || exit ? .8 : disposition.moving || entry ? 1 : .82);
      label.setScale(1 / plane.scaleX); stage.setScale(1 / plane.scaleX);
      // Dim only the waiting airframe; its flight number and status stay legible.
      body.setAlpha(parked || disposition.moving || exit || entry || isFocus ? 1 : .75);
      plane.setAlpha(1).setVisible(visible);
      plane.setData("waitingForStand", Boolean(exitBlocker) || (projected?.waitingForStand ?? false));
      plane.setData("standGate", parked ? parkedGate : undefined);
      plane.setData("renderStage", disposition.stage);
      if (exit) this.drawStandExit(flight.id);
      if (entry) {
        this.drawStandEntry(flight.id);
        this.startStandEntry(flight.id);
      }

    });
    if(connection) this.drawDepartureAircraft(connection);
    const visibleHoldingIds = pendingFlightIds
      .filter((flightId) => visualSlots.has(flightId))
      .sort((left, right) => (visualSlots.get(left) ?? 0) - (visualSlots.get(right) ?? 0));
    const hiddenHoldingIds = pendingFlightIds.filter((flightId) => !visualSlots.has(flightId));
    if (!finalSnapshot && pendingFlightIds.length && holding.showCaptions) {
      this.aircraftLabels?.add(this.readableText(holding.left, holding.captionY,
        holdingCaption(visibleHoldingIds.length, pendingFlightIds.length, this.language),
        {color: "#385562", fontFamily: "Inter, system-ui", fontSize: "12px", fontStyle: "bold",
          backgroundColor: "#fffffff2", padding: {x: 4, y: 3}}));
      if (hiddenHoldingIds.length) {
        this.aircraftLabels?.add(this.readableText(holding.left, holding.captionY + 20,
          hiddenHoldingCaption(hiddenHoldingIds, holding.availableWidth, this.language),
          {color: "#385562", fontFamily: "Inter, system-ui", fontSize: "11px",
            backgroundColor: "#fffffff2", padding: {x: 4, y: 2}}));
      }
    }
    const completed = flights.filter((flight) => dispositionByFlight.get(flight.id)?.stage === "released"
      && !this.standExits.has(flight.id) && flight.id!==connection?.carrier_flight_id);
    if (completed.length) {
      this.aircraftLabels?.add(this.readableText(width * .65, height * .77,
        completedFlightsLabel(completed, this.focusFlightId, this.language),
        { color: "#385562", fontSize: "12px", wordWrap: { width: width * .32 } }));
    }
  }

  private taxiSpineX() {
    return apronSpineX(
      this.viewWidth * (this.state?.scenario.layout?.taxiways[0]?.[0]?.x ?? .43),
      (this.state?.scenario.gates ?? []).map(gate => this.gatePoint(gate.id)),
    );
  }

  private apronLaneY(gateId:string) {
    const point=this.gatePoint(gateId), t=this.terminalLayout(this.viewWidth,this.viewHeight);
    if(gateId.startsWith("R"))return point.y+52;
    return point.y+(point.y<t.terminalY+t.terminalH/2?64:-64);
  }

  private gatePoint(gate: string) {
    const width = this.viewWidth;
    const height = this.viewHeight;
    const source = this.state?.scenario.layout?.gates.find((item) => item.id === gate);
    if (source) {
      return { x: width * source.x, y: height * source.y };
    }
    const terminal = this.terminalLayout(width, height);
    if (gate.startsWith("R")) {
      const remoteIndex = Math.max(0, Number(gate.replace(/\D/g, "")) - 1);
      return {
        x: terminal.gateXBase + terminal.gateSpacing * 2.8 + remoteIndex * 28,
        y: terminal.lowerGateY + 52,
      };
    }
    const gateIndex = Math.max(0, Number(gate.replace(/\D/g, "")) - 1);
    return {
      x: terminal.gateXBase + (gateIndex % 3) * terminal.gateSpacing,
      y: gateIndex < 3 ? terminal.upperGateY : terminal.lowerGateY,
    };
  }


  private showEventPulse(event: ExecutionEvent) {
    const width = this.viewWidth;
    const height = this.viewHeight;
    const failure = event.type === "ground_vehicle_failure";
    const recovered = event.type === "recovered";
    const runwayReopened = event.type === "runway_reopened";
    const runwayBlocked = event.type === "runway_safety_blocked";
    const trustedStateRebuilt = event.type === "trusted_state_reconciled";
    const runwayId = String(event.payload.resource_id ?? event.payload.runway ?? "RUNWAY");
    const x = failure || trustedStateRebuilt ? width * .47 : runwayReopened || runwayBlocked ? width * .7 : width * .63;
    const y = failure || trustedStateRebuilt ? height * .62 : runwayReopened || runwayBlocked ? height * .52 : height * .43;
    const color = failure || runwayBlocked ? 0xff5360 : recovered ? 0x63e6ad : 0x55dfd0;
    const captionText = failure
      ? "TUG-3 FAILURE"
      : trustedStateRebuilt ? "TRUSTED STATE REBUILT"
      : recovered
        ? "RECOVERY STABLE"
        : runwayReopened
          ? `${runwayId} REOPENED · CLEAR TO USE`
          : runwayBlocked
            ? `${runwayId} LANDING BLOCKED · RUNWAY CLOSED`
            : "AUTO REPLAN";
    const ring = this.add.circle(x, y, 16, color, .12).setStrokeStyle(2, color, .84);
    const caption = this.readableText(x, y - 38, captionText, {
      color: failure || runwayBlocked ? "#b34254" : "#227e6f",
      fontFamily: "Inter, system-ui",
      fontSize: "14px",
      fontStyle: "bold",
      backgroundColor: "#fffffff2",
      padding: { x: 8, y: 5 },
    }).setOrigin(.5);
    if (failure || runwayBlocked) this.cameras.main.shake(260, .004);
    this.tweens.add({
      targets: ring,
      scale: 4,
      alpha: 0,
      duration: 1800,
      onComplete: () => ring.destroy(),
    });
    this.tweens.add({
      targets: caption,
      y: y - 48,
      alpha: 0,
      delay: 1400,
      duration: 900,
      onComplete: () => caption.destroy(),
    });
  }

  private resetRain(width: number, height: number) {
    const severity = Math.max(0, Math.min(5, this.state?.scenario.weather.severity ?? 0));
    // Darken scenery below aircraft, people and labels; never dim the UI text.
    const strength = Math.max(0, severity - 1) / 4;
    this.weatherShade?.clear().fillStyle(0x16304b, strength * .43).fillRect(0, 0, width, height);
    const count = Math.round(strength * 75 * Math.min(1.5, width * height / 650000));
    this.game.canvas.dataset.weatherShade = (strength * .43).toFixed(2);
    this.game.canvas.dataset.rainDropCount = String(count);
    if (this.rain.length !== count) {
      this.rain = Array.from({ length: count }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        speed: 340 + Math.random() * 260,
        length: 10 + Math.random() * 12,
      }));
    }
  }

  update(now: number, delta: number) {
    const activity = ["executing", "monitoring", "replanning"].includes(this.visiblePhase) ? "execution" : this.visiblePhase;
    const key = `${this.viewWidth}x${this.viewHeight}:${this.state?.scenario.flights.length ?? 0}:${activity}`;
    const visible = document.visibilityState === "visible";
    const metrics = this.game.canvas.dataset;
    if (metrics.sampleKey !== key || !visible) {
      delete metrics.renderFps; delete metrics.renderFrameCount; delete metrics.renderFrameWindowMs;
      delete document.documentElement.dataset.stormshiftFps;
      metrics.sampleKey = key;
    }
    const sample = this.frameMeter.push(now, key, visible);
    if (sample) {
      metrics.renderFps = sample.fps.toFixed(1);
      metrics.renderFrameCount = String(sample.frames);
      metrics.renderFrameWindowMs = String(Math.round(sample.elapsedMs));
      metrics.sampledAt = String(Math.round(now));
      document.documentElement.dataset.stormshiftFps = metrics.renderFps;
      document.documentElement.dataset.stormshiftFrameSampleMs = metrics.renderFrameWindowMs;
    }
    const pedestrianPause=this.passengersPaused || !visible || this.historicalPassengers;
    this.passengerFlow.advance(Math.min(delta,250),this.passengerRate,pedestrianPause);
    this.passengerTimeline.advance(Math.min(delta,250),this.passengerRate,pedestrianPause);
    this.drawPassengers();
    const g = this.rainGraphics;
    if (!g) return;
    g.clear();
    if (!this.rain.length) return;
    g.lineStyle(1, 0xf1f8ff, .35);
    const width = this.viewWidth;
    const height = this.viewHeight;
    for (const drop of this.rain) {
      const frameDelta = Math.min(delta, 60);
      drop.x -= frameDelta * .10;
      drop.y += drop.speed * frameDelta / 1000;
      if (drop.y > height || drop.x < -12) {
        drop.y = -drop.length;
        drop.x = Math.random() * width;
      }
      g.lineBetween(drop.x, drop.y, drop.x - 6, drop.y + drop.length);
    }
  }
}

export function AirportWorld({
  session,
  layer,
  selectedPlanId,
  focusFlightId,
  executionEvents,
  playbackEvents,
  playbackToken,
  phase,
  playbackMinute,
  playbackRate,
  playbackPaused,
  pedestriansPaused,
  historicalPlayback,
  onFlightSelect,
  onPassengerStageChange,
  language,
}: {
  session?: AirportMapState;
  layer: MapLayer;
  selectedPlanId?: string;
  focusFlightId?: string;
  executionEvents: ExecutionEvent[];
  playbackEvents: ExecutionEvent[];
  playbackToken: number;
  phase: Phase;
  playbackMinute: number;
  playbackRate: number;
  playbackPaused: boolean;
  pedestriansPaused: boolean;
  historicalPlayback: boolean;
  language: "zh" | "en";
  onFlightSelect?: (flightId: string) => void;
  onPassengerStageChange?: (sessionId:string,sourceFlightId:string,stage:ConnectingDeparture["passenger_stage"])=>void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const scene = useRef<AirportScene | undefined>(undefined);

  useEffect(() => {
    if (!host.current) return;
    const airport = new AirportScene();
    const ratio = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
    scene.current = airport;
    const game = new Phaser.Game({
      type: Phaser.CANVAS,
      parent: host.current,
      backgroundColor: "#e8f3ef",
      transparent: false,
      scale: { mode: Phaser.Scale.NONE, width: Math.round(host.current.clientWidth * ratio),
        height: Math.round(host.current.clientHeight * ratio), zoom: 1 / ratio },
      render: { antialias: true, pixelArt: false, roundPixels: true },
      scene: airport,
      audio: { noAudio: true },
    });
    return () => {
      scene.current = undefined;
      game.destroy(true);
    };
  }, []);


  // Keep operational objects inside the unobscured canvas, not behind the dock.
  useEffect(() => {
    const element = host.current;
    const shell = element?.parentElement;
    if (!element || !shell) return;
    const deck = shell.querySelector<HTMLElement>(".command-deck");
    const status = shell.querySelector<HTMLElement>(".situation-strip");
    const controls = shell.querySelector<HTMLElement>(".playback-controls");
    const story = shell.querySelector<HTMLElement>(".story-beat");
    const layers = shell.querySelector<HTMLElement>(".layer-switch");
    const layout = () => {
      if (!session || !deck) {
        element.style.top = "0px";
        element.style.height = "100%";
        scene.current?.resizeViewport(element.clientWidth, element.clientHeight);
        return;
      }
      const origin = shell.getBoundingClientRect().top;
      const top = Math.max(status?.getBoundingClientRect().bottom ?? origin,
        controls?.getBoundingClientRect().bottom ?? origin,
        story?.getBoundingClientRect().bottom ?? origin,
        layers?.getBoundingClientRect().bottom ?? origin) - origin + 8;
      const bottom = deck.getBoundingClientRect().top - origin - 8;
      element.style.top = `${top}px`;
      element.style.height = `${Math.max(160, bottom - top)}px`;
      element.style.bottom = "auto";
      // A high-DPI backing canvas, with all airport geometry kept in CSS-pixel coordinates.
      scene.current?.resizeViewport(element.clientWidth, element.clientHeight);
    };
    const observer = new ResizeObserver(layout);
    [shell, deck, status, controls, story, layers].forEach((item) => { if (item) observer.observe(item); });
    layout();
    return () => observer.disconnect();
  }, [session?.id, session?.plans.length, phase, Boolean(playbackEvents.length)]);

  useEffect(() => {
    scene.current?.setPassengerStageHandler(onPassengerStageChange);
  }, [onPassengerStageChange]);
  useEffect(() => {
    scene.current?.setFlightSelectionHandler(onFlightSelect);
  }, [onFlightSelect]);
  useEffect(() => {
    if (session) {
      scene.current?.sync(
        session,
        layer,
        executionEvents,
        selectedPlanId,
        playbackEvents,
        playbackToken,
        phase,
        playbackMinute,
        playbackRate,
        playbackPaused,
        language,
        focusFlightId,
        pedestriansPaused,
        historicalPlayback,
      );
    }
  }, [session, layer, selectedPlanId, executionEvents, playbackEvents, playbackToken, phase, playbackMinute, playbackRate, playbackPaused, language, focusFlightId, pedestriansPaused, historicalPlayback]);

  return (
    <div
      ref={host}
      className="airport-world"
      aria-label={language === "zh" ? "交互式机场运行地图" : "Interactive airport operations map"}
      onClick={(event) => {
        const bounds = event.currentTarget.getBoundingClientRect();
        scene.current?.selectFlightAtRatio((event.clientX - bounds.left) / bounds.width, (event.clientY - bounds.top) / bounds.height);
      }}
    />
  );
}
