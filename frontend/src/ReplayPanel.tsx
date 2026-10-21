import { useEffect, useMemo, useState } from "react";
import { Play, RotateCcw, X } from "lucide-react";
import { copy } from "./copy";
import { executionEventLabel } from "./localization";
import { hasHistoricalWorlds } from "./historicalWorld";
import { compareExecutionEvents } from "./playback";
import { historicalSelection, replayInitialCursor, replayLandmarks, type ReplayFocus } from "./replayNavigation";
import type { ExecutionEvent } from "./types";
import "./replayNavigation.css";

type Language = keyof typeof copy;
type CopyKey = keyof typeof copy.zh;

export function ReplayPanel({
  events,
  onClose,
  onPlay,
  language,
  initialFocus = "latest",
  onSeek,
}: {
  events: ExecutionEvent[];
  onClose: () => void;
  onPlay: (events: ExecutionEvent[]) => void;
  language: Language;
  initialFocus?: ReplayFocus;
  onSeek: (sequence: number) => void;
}) {
  const t = (key: CopyKey) => copy[language][key];
  const replayable = useMemo(() => [...events].sort(compareExecutionEvents), [events]);
  const completeWorldEvidence = hasHistoricalWorlds(replayable);
  const [cursor, setCursor] = useState(() => replayInitialCursor(replayable, initialFocus));
  useEffect(() => setCursor(replayInitialCursor(replayable, initialFocus)), [replayable, initialFocus]);
  const landmarks = replayLandmarks(replayable);
  const current = replayable[cursor];
  const maxMinute = Math.max(1, ...replayable.map((event) => event.sim_minute));
  const labelFor = (event: ExecutionEvent) => executionEventLabel(event.type, language);

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section className="replay-panel glass" role="dialog" aria-modal="true" aria-label={t("replayConsole")} onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <div>
            <RotateCcw size={17} />
            <span><strong>{t("replayConsole")}</strong><small>{t("replayHint")}</small></span>
          </div>
          <button onClick={onClose} aria-label={t("close")}><X size={18} /></button>
        </header>
        {current ? (
          <>
            <div className="replay-overview">
              <span><small>EVENTS</small><b>{replayable.length}</b></span>
              <span><small>WINDOW</small><b>T+{replayable[0]?.sim_minute ?? 0} — T+{maxMinute}</b></span>
              <span><small>SELECTED</small><b>#{current.sequence}</b></span>
            </div>
            <nav className="replay-landmarks" aria-label={t("replayLandmarks")}>
              {landmarks.map(mark => <button key={mark.kind} aria-pressed={current.sequence === mark.sequence}
                onClick={() => setCursor(replayable.findIndex(event => event.sequence === mark.sequence))}>
                {t(mark.kind === "approved" ? "replayApproved" : mark.kind === "beforeFault" ? "replayBeforeFault"
                  : mark.kind === "fault" ? "replayFault" : mark.kind === "decision" ? "replayDecision" : "replayRecovered")}
              </button>)}
            </nav>
            <p className="replay-seek-hint">{t("replaySeekHint")}</p>
            <div className="replay-scrubber">
              <div className="replay-track" aria-hidden="true">
                {replayable.map((event, index) => (
                  <span
                    key={event.sequence}
                    className={`${index <= cursor ? "passed" : ""} ${event.type.includes("failure") ? "danger" : ""}`}
                    style={{ left: `${index / Math.max(1, replayable.length - 1) * 100}%` }}
                  />
                ))}
              </div>
              <input
                aria-label="Replay event cursor"
                type="range"
                min="0"
                max={Math.max(0, replayable.length - 1)}
                value={cursor}
                onChange={(event) => setCursor(Number(event.target.value))}
              />
              <div className="replay-scale"><span>{language === "zh" ? "按事件顺序拖动 · #1" : "Drag by event sequence · #1"}</span><span>#{replayable.length}</span></div>
            </div>
            <article className={`replay-focus ${current.type.includes("failure") ? "danger" : ""}`}>
              <time>T+{current.sim_minute}</time>
              <div>
                <small>EVENT #{current.sequence}</small>
                <strong>{labelFor(current)}</strong>
                <p>
                  {current.type === "execution_tick"
                    ? `${String(current.payload.flight_id ?? "Flight")} → ${String(current.payload.runway ?? "Runway")} / ${String(current.payload.gate ?? "Gate")}`
                    : String(current.payload.summary ?? current.payload.resource_id ?? current.payload.status ?? "Digital twin state transition")}
                </p>
              </div>
            </article>
            <div className="replay-list compact">
              {replayable.slice(Math.max(0, cursor - 3), cursor + 1).map((event) => (
                <button
                  key={event.sequence}
                  className={event.sequence === current.sequence ? "active" : ""}
                  onClick={() => setCursor(replayable.indexOf(event))}
                >
                  <time>T+{event.sim_minute}</time>
                  <i className={event.type.includes("failure") ? "danger" : ""} />
                  <span><strong>{labelFor(event)}</strong><small>Sequence #{event.sequence}</small></span>
                </button>
              ))}
            </div>
            {!completeWorldEvidence && <p role="status">{language === "zh"
              ? "旧日志缺少完整历史现场快照：可查看事件，但不能用当前状态伪造地图回放。"
              : "Historical world snapshots are incomplete. Events remain readable; map replay is unavailable."}</p>}
            <footer className="replay-actions">
              <button disabled={cursor === 0} onClick={() => setCursor((value) => Math.max(0, value - 1))}>
                ← {t("previousEvent")}
              </button>
              <button disabled={cursor === replayable.length - 1} onClick={() => setCursor((value) => Math.min(replayable.length - 1, value + 1))}>
                {t("nextEvent")} →
              </button>
              <button className="replay-primary replay-seek" disabled={!historicalSelection(replayable, current.session_id, current.sequence)}
                onClick={() => onSeek(current.sequence)}>{t("replaySeek")}</button>
              <button className="replay-primary" disabled={!hasHistoricalWorlds(replayable.slice(0, cursor + 1))} onClick={() => onPlay(replayable.slice(0, cursor + 1))}>
                <Play size={14} /> {t("replayUntil")}
              </button>
              <button className="replay-primary full" disabled={!completeWorldEvidence} onClick={() => onPlay(replayable)}>
                <RotateCcw size={14} /> {t("fullReplay")}
              </button>
            </footer>
          </>
        ) : <div className="replay-empty">{t("noReplayEvents")}</div>}
      </section>
    </div>
  );
}
