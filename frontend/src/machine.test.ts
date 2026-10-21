import { describe, expect, it } from "vitest";
import { createActor } from "xstate";
import { recoveryMachine } from "./machine";

describe("recovery state machine", () => {
  it("stops online execution at a human safety gate and resumes after approval", () => {
    const actor = createActor(recoveryMachine).start();
    actor.send({ type: "INCIDENT" });
    actor.send({ type: "PLAN" });
    actor.send({ type: "PLANS_READY" });
    actor.send({ type: "APPROVE" });
    actor.send({ type: "ADVANCE" });
    actor.send({ type: "SECONDARY_FAILURE" });
    actor.send({ type: "HUMAN_REVIEW" });
    expect(actor.getSnapshot().value).toBe("awaitingApproval");
    actor.send({ type: "APPROVE" });
    actor.send({ type: "MONITOR" });
    actor.send({ type: "ADVANCE" });
    actor.send({ type: "RECOVERED" });
    expect(actor.getSnapshot().value).toBe("recovered");
  });
});
