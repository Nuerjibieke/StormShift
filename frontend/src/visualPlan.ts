import type { Plan } from "./types";

export function selectVisualPlan<T extends Pick<Plan, "id">>(
  plans: T[],
  selectedPlanId?: string,
  serverPlanId?: string | null,
): T | undefined {
  return plans.find((plan) => plan.id === selectedPlanId)
    ?? plans.find((plan) => plan.id === serverPlanId)
    ?? plans[0];
}
