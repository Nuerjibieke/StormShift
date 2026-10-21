import type { ExecutionEvent, Phase, SessionState } from "./types";
import { storyFocusFlight } from "./holdingPresentation";

export type StoryStage = 1 | 2 | 3 | 4 | 5 | 6;
export type StoryAction = "intent" | "plan" | "evidence" | "preview" | "approve" | "continue" | "lab" | "replay";
export interface StoryState {
  stage: StoryStage;
  faultVisible: boolean;
  needsConfirmation: boolean;
  intentConfirmed: boolean;
  hasPlans: boolean;
  canApprove: boolean;
  gateStatus: string;
  previewSelected: boolean;
  focusFlight: string;
}
export function storyReviewKey(session: SessionState, planId: string): string {
  return [session.id, session.sequence, session.updated_at, planId].join("|");
}

/** Navigation derives from live facts, never from chapters the user has opened.
 * During playback only the revealed event prefix may announce a fault.
 * Callers hide this live guide in historical playback.
 */
export function deriveGuidedStory(input: {
  session: SessionState; phase: Phase; minute: number; playing: boolean;
  revealedEvents: ExecutionEvent[]; introAcknowledged: boolean;
  selectedPlanId: string; reviewKey: string;
}): StoryState {
  const { session, phase, minute, playing, revealedEvents, introAcknowledged, selectedPlanId, reviewKey } = input;
  const selected = session.plans.find(p => p.id === selectedPlanId);
  const gate = selected ? session.decision_lab.approval_gates?.[selected.id] : undefined;
  const needsConfirmation = Boolean(session.intent?.needs_confirmation);
  const intentConfirmed = Boolean(session.intent?.raw_text.trim()) && !needsConfirmation && !session.intent?.issues?.length;
  const previewSelected = Boolean(selected) && reviewKey === storyReviewKey(session, selectedPlanId);
  const faultVisible = revealedEvents.some(e => e.session_id === session.id && e.sim_minute <= minute && e.type === "ground_vehicle_failure");
  const canApprove = Boolean(selected && gate?.can_human_approve && !needsConfirmation && !session.intent?.issues?.length);
  const executionStarted = playing || ["executing", "monitoring", "replanning"].includes(phase)
    || Boolean(session.execution);
  const stage: StoryStage = phase === "recovered" ? 6 : executionStarted ? 5
    : needsConfirmation ? 2 : phase === "analyzing" || phase === "infeasible" ? 3
    : session.plans.length ? (previewSelected && canApprove ? 4 : 3)
    : intentConfirmed || introAcknowledged ? 2 : 1;
  const focusFlight = storyFocusFlight(session, revealedEvents);
  return { stage, faultVisible, needsConfirmation, intentConfirmed, hasPlans: session.plans.length > 0,
    canApprove, gateStatus: gate?.status ?? "unknown", previewSelected, focusFlight };
}

export function storyActions(state: StoryState, input: { playing: boolean; paused: boolean; canResume: boolean; needsApproval: boolean }): StoryAction[] {
  switch (state.stage) {
    case 1: return ["intent"];
    case 2: return state.intentConfirmed ? ["plan", "intent"] : ["intent"];
    case 3: return state.hasPlans ? ["evidence", "preview"] : ["lab"];
    case 4: return ["approve", "evidence", "preview"];
    case 5: return input.playing ? (input.paused ? ["continue", "lab"] : ["lab"])
      : input.needsApproval ? ["approve", "lab"] : input.canResume ? ["continue", "lab"] : ["lab"];
    case 6: return ["lab", "replay"];
  }
}
