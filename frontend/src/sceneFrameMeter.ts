/** Count actual scene update intervals over a stable, foreground window. */
export class SceneFrameMeter {
  private start?: number;
  private last?: number;
  private key = "";
  private frames = 0;
  reset() { this.start = undefined; this.last = undefined; this.frames = 0; this.key = ""; }
  push(now: number, key: string, visible: boolean) {
    if (!visible || !Number.isFinite(now)) { this.reset(); return; }
    if (this.start === undefined || this.key !== key || (this.last !== undefined && now < this.last)) {
      this.start = now; this.last = now; this.key = key; this.frames = 0; return;
    }
    if (now === this.last) return;
    this.last = now;
    this.frames += 1;
    const elapsed = now - this.start;
    if (elapsed < 3000) return;
    const report = { fps: this.frames * 1000 / elapsed, elapsedMs: elapsed, frames: this.frames, key };
    this.start = now; this.frames = 0;
    return report;
  }
}
