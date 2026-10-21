(function () {
  "use strict";

  const COLOR = {
    cyan: 0x42d6cf,
    amber: 0xf4b94b,
    red: 0xff626d,
    green: 0x6ee7a2,
    white: 0xeef5f7,
    muted: 0x8fa6b5,
  };

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  class StormShiftWorld {
    constructor(parentId, hooks) {
      if (!window.Phaser) throw new Error("Phaser runtime is not loaded");
      this.parentId = parentId;
      this.hooks = hooks;
      this.scene = null;
      this.time = 0;
      this.dragging = false;
      this.dragMoved = false;
      this.lastPointer = null;
      this.lastPhase = null;
      this.initialPlanes = {
        QN205: { id: "QN205", type: "A319", x: 315, y: 174, angle: 0, color: 0xb8cad3, detail: "A1 登机中 · 预计 18:42 离港" },
        AX106: { id: "AX106", type: "B738", x: 315, y: 238, angle: 0, color: 0xff8d73, detail: "A2 延迟离港 · 已延误 14 分钟" },
        SK218: { id: "SK218", type: "A321", x: 1080, y: 205, angle: Math.PI, color: COLOR.cyan, detail: "进港 · 62 名紧张转机旅客" },
        JR330: { id: "JR330", type: "A320", x: 820, y: 430, angle: Math.PI, color: 0x8fb4c8, detail: "进港 · 原计划 A4" },
        LV412: { id: "LV412", type: "E190", x: 1090, y: 514, angle: Math.PI, color: 0xa7bdc9, detail: "最后进近 · 原计划 A5" },
      };
      const host = document.getElementById(parentId);
      const self = this;
      class AirportScene extends Phaser.Scene {
        constructor() { super("AirportScene"); }
        create() { self.create(this); }
        update(_time, delta) { self.update(delta / 1000); }
      }
      this.game = new Phaser.Game({
        type: Phaser.AUTO,
        parent: parentId,
        backgroundColor: "#0b1b24",
        width: host.clientWidth,
        height: host.clientHeight,
        antialias: true,
        render: { antialias: true, roundPixels: false, powerPreference: "high-performance" },
        scale: { mode: Phaser.Scale.RESIZE, autoCenter: Phaser.Scale.CENTER_BOTH },
        scene: AirportScene,
      });
    }

    state() {
      return this.hooks.getState();
    }

    create(scene) {
      this.scene = scene;
      scene.cameras.main.setBounds(0, 0, 1200, 680);
      scene.cameras.main.centerOn(600, 340);
      scene.cameras.main.setZoom(this.fitZoom());
      scene.scale.on("resize", () => {
        const camera = scene.cameras.main;
        if (camera.zoom < this.fitZoom()) camera.setZoom(this.fitZoom());
      });

      this.staticLayer = scene.add.graphics().setDepth(0);
      this.dynamicLayer = scene.add.graphics().setDepth(12);
      this.drawStatic();
      this.createLabels();
      this.createInteractiveAreas();
      this.createPlanes();
      this.createWeather();
      this.createCurves();
      this.bindCamera();
      this.renderDynamic(true);
    }

    fitZoom() {
      if (!this.scene) return 1;
      return Math.min(this.scene.scale.width / 1200, this.scene.scale.height / 680);
    }

    drawStatic() {
      const g = this.staticLayer;
      g.clear();
      g.fillGradientStyle(0x10222c, 0x0b1b24, 0x10222c, 0x0b1b24, 1);
      g.fillRect(0, 0, 1200, 680);

      g.lineStyle(1, 0x6f97a6, 0.055);
      for (let x = 0; x <= 1200; x += 60) g.lineBetween(x, 0, x, 680);
      for (let y = 0; y <= 680; y += 60) g.lineBetween(0, y, 1200, y);
      g.fillStyle(0x174239, 0.18);
      g.fillRect(355, 80, 800, 52);
      g.fillRect(355, 348, 800, 84);
      g.fillRect(355, 590, 800, 70);

      const taxiways = [
        [[280,174],[350,174],[375,205],[1135,205]],
        [[280,238],[350,238],[410,285],[1040,285]],
        [[280,302],[360,302],[430,360],[1040,360]],
        [[280,366],[350,366],[420,430],[1030,430]],
        [[280,430],[350,430],[405,480],[1135,514]],
        [[280,494],[350,494],[395,514],[1135,514]],
        [[610,205],[610,514]],
        [[920,205],[920,514]],
      ];
      taxiways.forEach((path) => {
        const points = path.map(([x, y]) => new Phaser.Geom.Point(x, y));
        g.lineStyle(24, 0x2a404b, 1);
        g.strokePoints(points, false, false);
        g.lineStyle(1.5, 0xeebe4e, 0.27);
        this.dashedPolyline(g, path, 8, 9);
      });

      this.drawRunway(g, 205, "07L");
      this.drawRunway(g, 514, "07R");

      g.fillGradientStyle(0x1c3442, 0x294656, 0x1c3442, 0x294656, 1);
      g.fillRoundedRect(74, 108, 190, 424, 19);
      g.lineStyle(2, 0x9dc4d3, 0.24);
      g.strokeRoundedRect(74, 108, 190, 424, 19);
      for (let y = 133; y < 520; y += 25) {
        g.fillStyle(0x65cbd8, y % 50 ? 0.09 : 0.15);
        g.fillRect(92, y, 128, 9);
      }

      this.gates = [
        { id: "A1", x: 292, y: 174 }, { id: "A2", x: 292, y: 238 },
        { id: "A3", x: 292, y: 302 }, { id: "A4", x: 292, y: 366 },
        { id: "A5", x: 292, y: 430 }, { id: "A6", x: 292, y: 494 },
      ];
      this.gates.forEach((gate) => {
        g.lineStyle(3, 0xb1cbd5, 0.45);
        g.lineBetween(264, gate.y, 303, gate.y);
        g.fillStyle(0x07111b, 0.78);
        g.fillRoundedRect(264, gate.y - 14, 38, 28, 8);
        g.lineStyle(1, 0xb1cbd5, 0.25);
        g.strokeRoundedRect(264, gate.y - 14, 38, 28, 8);
      });

      g.lineStyle(2, 0xb1cbd5, 0.28);
      this.dashedCircle(g, 420, 608, 34, 24);
    }

    drawRunway(g, y, id) {
      g.fillStyle(0x25343e, 1);
      g.fillRoundedRect(390, y - 36, 760, 72, 6);
      g.lineStyle(2, 0xafc6d2, 0.2);
      g.strokeRoundedRect(390, y - 36, 760, 72, 6);
      g.lineStyle(2, 0xe5eef1, 0.6);
      for (let x = 440; x < 1110; x += 48) g.lineBetween(x, y, x + 27, y);
      g.fillStyle(0x5cdcd5, 0.58);
      for (let x = 450; x < 1130; x += 34) {
        g.fillCircle(x, y - 29, 1.8);
        g.fillCircle(x, y + 29, 1.8);
      }
      const label = this.scene.add.text(407, y - 22, id, {
        fontFamily: "Inter, Arial, sans-serif", fontSize: "14px", fontStyle: "bold", color: "#dce7eb",
      }).setDepth(2);
      const status = this.scene.add.text(407, y + 13, "ACTIVE", {
        fontFamily: "Inter, Arial, sans-serif", fontSize: "9px", color: "#77909e",
      }).setDepth(2);
      if (!this.runwayLabels) this.runwayLabels = {};
      this.runwayLabels[id] = { label, status };
    }

    createLabels() {
      const base = { fontFamily: "Inter, Arial, sans-serif" };
      this.scene.add.text(104, 83, "TERMINAL A", { ...base, fontSize: "13px", fontStyle: "bold", color: "#d5e3e8" }).setDepth(3);
      this.scene.add.text(104, 540, "NORTH HARBOR", { ...base, fontSize: "9px", color: "#77909e" }).setDepth(3);
      this.scene.add.text(420, 608, "R1", { ...base, fontSize: "10px", fontStyle: "bold", color: "#8299a6" }).setOrigin(.5).setDepth(3);
      this.gates.forEach((gate) => {
        this.scene.add.text(283, gate.y, gate.id, { ...base, fontSize: "9px", fontStyle: "bold", color: "#9db2be" }).setOrigin(.5).setDepth(3);
      });
    }

    createInteractiveAreas() {
      this.gates.forEach((gate) => {
        const zone = this.scene.add.zone(gate.x, gate.y, 58, 44).setDepth(30).setInteractive({ useHandCursor: true });
        zone.on("pointerup", () => {
          if (this.dragMoved) return;
          const detail = gate.id === "A2" ? "AX106 延迟占用" : gate.id === "A1" ? "QN205 登机中" : "当前可用";
          this.hooks.onSelect({ kind: "登机口", title: gate.id, detail, key: gate.id });
        });
      });
      [
        { id: "07L", y: 205, detail: "开放 · 雷暴期间单跑道运行" },
        { id: "07R", y: 514, detail: "开放" },
      ].forEach((runway) => {
        const zone = this.scene.add.zone(770, runway.y, 760, 72).setDepth(4).setInteractive({ useHandCursor: true });
        zone.on("pointerup", () => {
          if (this.dragMoved) return;
          const disrupted = this.state().phase !== "normal";
          const detail = runway.id === "07R" && disrupted ? "关闭 · 正在进行雷击检查" : runway.detail;
          this.hooks.onSelect({ kind: "跑道", title: runway.id, detail, key: runway.id });
        });
      });
    }

    createPlanes() {
      this.planeObjects = {};
      Object.values(this.initialPlanes).forEach((plane) => {
        const container = this.scene.add.container(plane.x, plane.y).setDepth(22);
        const ring = this.scene.add.circle(0, 0, 30, plane.color, 0).setStrokeStyle(2, plane.color, 0.9).setVisible(false);
        const body = this.scene.add.polygon(0, 0, [
          25,0, 16,-4, 7,-4, -4,-18, -10,-18, -5,-4, -20,-3, -25,-9,
          -29,-9, -27,0, -29,9, -25,9, -20,3, -5,4, -10,18, -4,18, 7,4, 16,4,
        ], COLOR.white, 1).setStrokeStyle(2, plane.color, 1);
        const nose = this.scene.add.circle(13, 0, 2.4, plane.color, 1);
        const label = this.scene.add.text(12, -31, plane.id, {
          fontFamily: "Inter, Arial, sans-serif", fontSize: "10px", fontStyle: "bold",
          color: "#dce8ed", backgroundColor: "rgba(7,17,27,.86)", padding: { x: 7, y: 4 },
        }).setOrigin(.5).setRotation(-plane.angle);
        container.add([ring, body, nose, label]);
        container.setRotation(plane.angle).setSize(70, 58).setInteractive({ useHandCursor: true });
        container.on("pointerup", () => {
          if (this.dragMoved) return;
          this.hooks.onSelect({ kind: "航班", title: plane.id, detail: plane.detail, key: plane.id });
        });
        this.planeObjects[plane.id] = { ...plane, container, ring, label };
      });
    }

    createWeather() {
      const texture = this.scene.make.graphics({ add: false });
      texture.fillStyle(0xa8d9e7, 0.75);
      texture.fillRoundedRect(0, 0, 2, 16, 1);
      texture.generateTexture("stormshift-rain", 2, 16);
      texture.destroy();
      this.rain = this.scene.add.particles(600, -40, "stormshift-rain", {
        x: { min: -80, max: 680 },
        y: 0,
        lifespan: 1800,
        speedX: { min: -120, max: -70 },
        speedY: { min: 420, max: 600 },
        quantity: 2,
        frequency: 34,
        alpha: { start: 0.32, end: 0.08 },
        scale: { min: 0.55, max: 1.1 },
        emitting: false,
      }).setDepth(60);
      this.flash = this.scene.add.rectangle(600, 340, 1200, 680, 0xc8efff, 0).setDepth(58);
      this.scene.time.addEvent({
        delay: 6200,
        loop: true,
        callback: () => {
          if (this.state().phase === "normal") return;
          this.flash.setAlpha(0.11);
          this.scene.tweens.add({ targets: this.flash, alpha: 0, duration: 180, ease: "Quad.easeOut" });
        },
      });
    }

    createCurves() {
      const make = (points) => {
        const curve = new Phaser.Curves.Path(points[0][0], points[0][1]);
        points.slice(1).forEach(([x, y]) => curve.lineTo(x, y));
        return curve;
      };
      this.curves = {
        SK218: make([[1080,205],[850,205],[620,205],[430,205],[370,246],[350,366],[315,366]]),
        JR330_REMOTE: make([[820,430],[690,430],[560,462],[475,535],[420,608]]),
        JR330_A3: make([[820,430],[690,430],[570,390],[430,332],[350,302],[315,302]]),
        LV412_A5: make([[1090,514],[1050,430],[1050,300],[1030,205],[800,205],[520,205],[390,245],[350,430],[315,430]]),
        LV412_REMOTE: make([[1090,514],[1060,420],[1030,260],[980,205],[760,205],[560,300],[500,470],[420,608]]),
      };
    }

    bindCamera() {
      const camera = this.scene.cameras.main;
      this.scene.input.on("pointerdown", (pointer) => {
        this.dragging = true;
        this.dragMoved = false;
        this.lastPointer = { x: pointer.x, y: pointer.y };
      });
      this.scene.input.on("pointermove", (pointer) => {
        if (!this.dragging || !pointer.isDown) return;
        const dx = pointer.x - this.lastPointer.x;
        const dy = pointer.y - this.lastPointer.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) this.dragMoved = true;
        camera.scrollX -= dx / camera.zoom;
        camera.scrollY -= dy / camera.zoom;
        this.lastPointer = { x: pointer.x, y: pointer.y };
      });
      this.scene.input.on("pointerup", () => { this.dragging = false; });
      this.scene.input.on("wheel", (pointer, _objects, _dx, dy) => {
        const before = camera.getWorldPoint(pointer.x, pointer.y);
        const next = clamp(camera.zoom * (dy > 0 ? 0.9 : 1.1), this.fitZoom() * 0.88, this.fitZoom() * 1.8);
        camera.setZoom(next);
        const after = camera.getWorldPoint(pointer.x, pointer.y);
        camera.scrollX += before.x - after.x;
        camera.scrollY += before.y - after.y;
      });
    }

    dashedPolyline(g, points, dash, gap) {
      for (let i = 1; i < points.length; i += 1) {
        const [x1, y1] = points[i - 1], [x2, y2] = points[i];
        const distance = Phaser.Math.Distance.Between(x1, y1, x2, y2);
        const ux = (x2 - x1) / distance, uy = (y2 - y1) / distance;
        for (let at = 0; at < distance; at += dash + gap) {
          const end = Math.min(at + dash, distance);
          g.lineBetween(x1 + ux * at, y1 + uy * at, x1 + ux * end, y1 + uy * end);
        }
      }
    }

    dashedCircle(g, x, y, radius, segments) {
      for (let i = 0; i < segments; i += 2) {
        g.beginPath();
        g.arc(x, y, radius, i / segments * Math.PI * 2, (i + 1) / segments * Math.PI * 2);
        g.strokePath();
      }
    }

    drawCurve(g, curve, color, executing, offset) {
      const points = curve.getSpacedPoints(86);
      g.lineStyle(9, color, 0.08);
      g.strokePoints(points, false, false);
      g.lineStyle(executing ? 4 : 3.5, color, executing ? 0.95 : 0.9);
      if (executing) {
        g.strokePoints(points, false, false);
      } else {
        const shifted = Math.floor((this.time * 13 + offset) % 10);
        for (let i = shifted; i < points.length - 1; i += 5) {
          if (i + 2 < points.length) g.strokePoints(points.slice(i, i + 3), false, false);
        }
      }
    }

    renderDynamic(force = false) {
      if (!this.scene) return;
      const state = this.state();
      const g = this.dynamicLayer;
      g.clear();
      const disrupted = state.phase !== "normal";

      if (disrupted) {
        g.fillStyle(0x4a1d29, 0.58);
        g.fillRoundedRect(390, 478, 760, 72, 6);
        g.lineStyle(2, COLOR.red, 0.6);
        g.strokeRoundedRect(390, 478, 760, 72, 6);
        g.lineStyle(3, COLOR.red, 0.78);
        for (let x = 500; x < 1080; x += 88) {
          g.lineBetween(x - 8, 506, x + 8, 522);
          g.lineBetween(x + 8, 506, x - 8, 522);
        }
      }
      if (this.runwayLabels) {
        this.runwayLabels["07R"].label.setColor(disrupted ? "#ff626d" : "#dce7eb");
        this.runwayLabels["07R"].status.setText(disrupted ? "CLOSED" : "ACTIVE").setColor(disrupted ? "#d88088" : "#77909e");
      }

      if (["disruption", "negotiating", "proposal"].includes(state.phase)) {
        [[330,238],[790,205],[905,445]].forEach(([x, y], index) => {
          const pulse = 22 + Math.sin(this.time * 3 + index) * 3;
          g.fillStyle(COLOR.red, 0.12); g.fillCircle(x, y, pulse);
          g.fillStyle(COLOR.red, 1); g.fillCircle(x, y, 10);
        });
      }

      if (["proposal", "executing", "recovered"].includes(state.phase)) {
        const executing = state.phase !== "proposal";
        const color = executing ? COLOR.cyan : COLOR.amber;
        this.drawCurve(g, this.curves.SK218, color, executing, 0);
        const capacity = state.planData && state.planData.plan.preference === "capacity";
        this.drawCurve(g, capacity ? this.curves.JR330_A3 : this.curves.JR330_REMOTE, color, executing, 3);
        this.drawCurve(g, capacity ? this.curves.LV412_REMOTE : this.curves.LV412_A5, color, executing, 6);
        g.lineStyle(4, color, 1);
        g.strokeCircle(420, 608, 34);
      }

      if (disrupted) {
        g.lineStyle(3, COLOR.red, 1);
        g.strokeRoundedRect(264, 224, 38, 28, 8);
      }
      if (["proposal", "executing", "recovered"].includes(state.phase)) {
        g.lineStyle(4, state.phase === "proposal" ? COLOR.amber : COLOR.cyan, 1);
        g.strokeRoundedRect(264, 352, 38, 28, 8);
      }

      if (force || this.lastPhase !== state.phase) {
        if (disrupted) this.rain.start(); else this.rain.stop();
        this.lastPhase = state.phase;
      }
    }

    applyCurve(id, curve, progress) {
      const point = curve.getPoint(progress);
      const tangent = curve.getTangent(clamp(progress, 0.001, 0.999));
      const plane = this.planeObjects[id];
      plane.container.setPosition(point.x, point.y).setRotation(tangent.angle());
      plane.label.setRotation(-tangent.angle());
      plane.x = point.x;
      plane.y = point.y;
    }

    update(delta) {
      if (!this.scene) return;
      const state = this.state();
      if (!state.paused) this.time += delta * state.speed;
      this.renderDynamic();
      Object.values(this.planeObjects).forEach((plane) => {
        const selected = state.selected && state.selected.key === plane.id;
        const priority = plane.id === "SK218" && ["disruption", "negotiating", "proposal"].includes(state.phase);
        plane.ring.setVisible(Boolean(selected || priority));
        if (priority) plane.ring.setStrokeStyle(2, COLOR.amber, 1);
        else plane.ring.setStrokeStyle(2, plane.color, 0.9);
      });

      if (state.phase === "executing" && !state.paused) {
        state.execution = clamp(state.execution + delta * state.speed / 12, 0, 1);
        const capacity = state.planData && state.planData.plan.preference === "capacity";
        const timings = { SK218: [0, .72], JR330: [.12, .78], LV412: [.38, 1] };
        this.applyCurve("SK218", this.curves.SK218, clamp((state.execution - timings.SK218[0]) / (timings.SK218[1] - timings.SK218[0]), 0, 1));
        this.applyCurve("JR330", capacity ? this.curves.JR330_A3 : this.curves.JR330_REMOTE, clamp((state.execution - timings.JR330[0]) / (timings.JR330[1] - timings.JR330[0]), 0, 1));
        this.applyCurve("LV412", capacity ? this.curves.LV412_REMOTE : this.curves.LV412_A5, clamp((state.execution - timings.LV412[0]) / (timings.LV412[1] - timings.LV412[0]), 0, 1));
        this.hooks.onProgress();
        if (state.execution >= 1) this.hooks.onRecovered();
      }
    }

    reset() {
      if (!this.scene) return;
      Object.values(this.initialPlanes).forEach((plane) => {
        const object = this.planeObjects[plane.id];
        object.container.setPosition(plane.x, plane.y).setRotation(plane.angle);
        object.label.setRotation(-plane.angle);
        object.x = plane.x;
        object.y = plane.y;
      });
      const camera = this.scene.cameras.main;
      camera.setZoom(this.fitZoom());
      camera.centerOn(600, 340);
      this.lastPhase = null;
      this.renderDynamic(true);
    }

    syncPhase() {
      this.renderDynamic(true);
    }
  }

  window.StormShiftWorld = StormShiftWorld;
})();
