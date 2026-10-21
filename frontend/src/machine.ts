import { setup } from "xstate";

export const recoveryMachine = setup({
  types: {
    events: {} as
      | { type: "INCIDENT" }
      | { type: "PLAN" }
      | { type: "PLANS_READY" }
      | { type: "INFEASIBLE" }
      | { type: "APPROVE" }
      | { type: "ADVANCE" }
      | { type: "SECONDARY_FAILURE" }
      | { type: "MONITOR" }
      | { type: "AUTO_REPLAN" }
      | { type: "HUMAN_REVIEW" }
      | { type: "RECOVERED" }
      | { type: "RESET" },
  },
}).createMachine({
  id: "airport-recovery",
  initial: "normal",
  on: { RESET: ".normal" },
  states: {
    normal: { on: { INCIDENT: "incident" } },
    incident: { on: { PLAN: "analyzing", RESET: "normal" } },
    analyzing: {
      on: {
        PLANS_READY: "awaitingApproval",
        INFEASIBLE: "infeasible",
      },
    },
    awaitingApproval: {
      on: {
        APPROVE: "executing",
        PLAN: "analyzing",
      },
    },
    executing: {
      on: {
        ADVANCE: "executing",
        SECONDARY_FAILURE: "replanning",
        MONITOR: "monitoring",
        HUMAN_REVIEW: "awaitingApproval",
        RECOVERED: "recovered",
        INFEASIBLE: "infeasible",
      },
    },
    monitoring: {
      on: {
        ADVANCE: "monitoring",
        SECONDARY_FAILURE: "replanning",
        HUMAN_REVIEW: "awaitingApproval",
        RECOVERED: "recovered",
        INFEASIBLE: "infeasible",
      },
    },
    replanning: {
      on: {
        AUTO_REPLAN: "monitoring",
        HUMAN_REVIEW: "awaitingApproval",
        INFEASIBLE: "infeasible",
      },
    },
    recovered: { on: { INCIDENT: "incident", RESET: "normal" } },
    infeasible: { on: { PLAN: "analyzing", RESET: "normal" } },
  },
});
