export type Mode = "guided" | "scenarios" | "sandbox";
export type DataMode = "synthetic" | "live" | "imported";
export type Phase =
  | "normal" | "incident" | "analyzing" | "candidates"
  | "awaiting_approval" | "executing" | "monitoring"
  | "replanning" | "recovered" | "infeasible";

export interface Runway {
  id: string;
  status: string;
  separation_min: number;
  capacity_per_hour: number;
  reopen_minute: number | null;
  reason?: string;
}

export interface Gate {
  id: string;
  status: string;
  occupied_by?: string;
  available_minute: number;
  remote: boolean;
  taxi_minutes: number;
}

export interface Flight {
  id: string;
  aircraft_type: string;
  state: string;
  scheduled_arrival: number;
  planned_gate: string;
  connections: number;
  fuel_margin_min: number;
  medical_priority: boolean;
  emissions_factor: number;
  origin: string;
}

export interface AirportEvent {
  id: string;
  type: string;
  minute: number;
  resource_id?: string;
  severity: number;
  duration_min: number;
  payload: Record<string, unknown>;
}

export interface AirportLayout {
  source_type: "synthetic_schematic" | "public_aip_simplified";
  source_label: string;
  source_url?: string;
  effective_date?: string;
  geometry_fidelity: "schematic" | "public-layout-simplified";
  terminal: { id: string; label: string; x: number; y: number; width: number; height: number };
  runways: Array<{ id: string; x: number; y: number; width: number; height: number }>;
  gates: Array<{ id: string; x: number; y: number }>;
  taxiways: Array<Array<{ x: number; y: number }>>;
  disclaimer: string;
}

export interface Scenario {
  id: string;
  name: string;
  airport: string;
  airport_icao?: string;
  mode: Mode;
  seed: number;
  current_minute: number;
  runways: Runway[];
  gates: Gate[];
  flights: Flight[];
  weather: {
    severity: number;
    label: string;
    visibility_km: number;
    closure_mean_min: number;
    closure_std_min: number;
    source: string;
    raw_report?: string;
  };
  events: AirportEvent[];
  layout?: AirportLayout;
  disclaimer: string;
}

export interface Intent {
  base_minute?: number;
  specified_fields?: string[];
  cleared_fields?: string[];
  inherited_fields?: string[];
  evidence?: Array<{field: string; excerpt: string}>;
  issues?: Array<{code: string; excerpt: string; message: string}>;
  notes?: string[];
  version?: number;
  confirmed_minute?: number | null;
  raw_text: string;
  original_text?: string;
  clarification_questions?: Array<{
    issue_index: number;
    target: "average_delay_cap" | "minimum_connection_rate" | "maximum_missed_connections" | "maximum_tail_exposure_rate" | "clause";
    question: string;
    placeholder: string;
  }>;
  clarification_history?: Array<{
    version: number;
    issue_index: number;
    question: string;
    excerpt: string;
    answer: string;
    replacement: string;
  }>;
  minimum_connection_rate?: number;
  maximum_missed_connections?: number;
  maximum_tail_exposure_rate?: number;
  weights: Record<string, number>;
  average_delay_cap?: number;
  scheduled_constraints: Array<{
    kind: string;
    resource_id?: string;
    minute?: number;
    value?: number | string;
    hard: boolean;
  }>;
  summary: string;
  needs_confirmation: boolean;
  source: string;
}

export interface Assignment {
  flight_id: string;
  runway: string;
  landing_minute: number;
  gate: string;
  hold_minutes: number;
  taxi_minutes: number;
  remote_stand: boolean;
}

export interface PlanEvidenceBasis {
  status: string;
  trusted_state_digest: string;
  snapshot_minute: number;
  confirmed_facts: number;
  resolved_conflicts: number;
  stale_claims_excluded: number;
  minimum_confidence: number;
  compiled_constraints: number;
  unresolved_critical: number;
}

export interface EvidenceFact {
  id: string;
  key: string;
  value: unknown;
  source: string;
  observed_minute: number;
  observed_at: string;
  age_min: number;
  confidence: number;
  status: string;
  solver_eligible: boolean;
  critical: boolean;
  note: string;
}

export interface TrustedOperationalState {
  method: string;
  status: string;
  snapshot_minute: number;
  data_mode: string;
  summary: string;
  quality: {
    raw_claims: number;
    solver_facts: number;
    confirmed: number;
    detected_conflicts: number;
    resolved_conflicts: number;
    stale_excluded: number;
    unresolved_critical: number;
    minimum_confidence: number;
    solver_coverage: number;
  };
  claims: EvidenceFact[];
  argumentation?: ArgumentationAnalysis;
  resolution_log: Array<Record<string, unknown>>;
  solver_input_values: Record<string, unknown>;
  compiled_constraints: Array<Record<string, unknown>>;
  changed_facts: Array<{ key: string; before: unknown; after: unknown; cause: string }>;
  invalidated_constraints: Array<{
    id: string;
    reason: string;
    changed_fact_keys: string[];
    status: string;
  }>;
  blocking_issues: Array<Record<string, unknown>>;
  digest: string;
  pipeline: Array<{ stage: string; status: string; output: string }>;
  caveat: string;
}
export interface RiskShieldFlight {
  flight_id: string;
  fuel_level: "clear" | "watch" | "escalate";
  eldt_point: number;
  eldt_upper_90: number;
  fuel_reserve_deadline: number;
  fuel_slack_min: number;
  fuel_risk_probability: number;
  transfer_passengers: number;
  transfer_buffer_min: number;
  upper_gate_delay_min: number;
  transfer_exposed: boolean;
}

export interface RunwayClearanceRow {
  flight_id: string;
  runway: string;
  landing_minute: number;
  point_ready_minute: number | null;
  p90_ready_minute: number | null;
  status: "clear" | "conditional" | "blocked";
  condition: string;
}

export interface RunwayClearanceWindow {
  runway: string;
  point_ready_minute: number | null;
  p90_ready_minute: number | null;
  first_planned_use_minute: number | null;
  status: "unused" | "clear" | "conditional" | "blocked";
}

export interface RunwayClearance {
  method: string;
  coverage: number;
  status: "clear" | "conditional" | "blocked";
  conditional_actions: number;
  blocked_actions: number;
  rows: RunwayClearanceRow[];
  windows?: RunwayClearanceWindow[];
  summary: string;
  meaning: string;
}

export interface AdaptiveSafetyEnvelope {
  method: string;
  status: "not_required" | "ready" | "applied" | "applied_equivalent" | "infeasible" | "abstain" | "evidence_required";
  trigger_status: string;
  base_plan_id: string;
  base_coverage: number;
  adaptive_coverage: number;
  landing_coverage?: number;
  solver_allowed: boolean;
  engineering_reserve_min: number;
  landing_limits: Record<string, number>;
  turnaround_buffer_by_flight?: Record<string, number>;
  protected_milestones?: string[];
  strongest_stream?: string;
  selected_plan_id?: string;
  changed_assignments?: number;
  post_shield_status?: string;
  applies_to_candidate?: boolean;
  action_source: string;
  summary: string;
  caveat: string;
}

export interface RiskShield {
  method: string;
  status: "clear" | "watch" | "escalate";
  automation_mode: "guarded_autonomy" | "guarded" | "human_required";
  blocks_unattended_execution: boolean;
  target_coverage: number;
  calibration_samples: number;
  evaluation_samples?: number;
  empirical_coverage?: number;
  calibration_health?: "healthy" | "degraded" | "not_applicable";
  split_independent?: boolean;
  fuel_operational_reserve_min: number;
  fuel_escalations: number;
  watch_items: number;
  connection_passengers_exposed: number;
  summary: string;
  rows: RiskShieldFlight[];
  guarantee_scope: string;
  caveat: string;
  calibration_plan_id?: string;
  landing_limits?: Record<string, number>;
  two_stage_solver?: string;
  runway_clearance?: RunwayClearance;
  adaptive_envelope?: AdaptiveSafetyEnvelope;
}

export interface Plan {
  id: string;
  profile: string;
  title: string;
  assignments: Assignment[];
  actions: string[];
  report: {
    method_version?: string;
    raw_metrics?: Record<string, number>;
    runs: number;
    mean_delay_min: number;
    p90_delay_min: number;
    cvar_delay_min: number;
    missed_connections: number;
    fuel_risk_probability: number;
    remaining_conflicts: number;
    emissions_index: number;
    recovery_minute: number;
    confidence_low: number;
    confidence_high: number;
    per_flight: Record<string, Record<string, number>>;
  };
  score: number;
  pareto_optimal: boolean;
  ideal_distance: number;
  feasible: boolean;
  constraint_proof: string[];
  explanation: string;
  why_not: string[];
  rank: number;
  risk_shield: RiskShield;
  evidence_basis?: PlanEvidenceBasis;
}

export interface AgentTrace {
  agent: string;
  role: string;
  status: string;
  summary: string;
  tool?: string;
  confidence: number;
  timestamp: string;
}

export interface RecourseAudit {
  source_plan_id?: string;
  replacement_plan_id?: string;
  strategy: "causal_local_repair";
  trigger_resource: string;
  zone: string;
  affected_flight_ids: string[];
  preserved_flight_ids: string[];
  changed_flight_ids: string[];
  affected_count: number;
  frozen_count: number;
  pending_count: number;
  changed_count: number;
  preservation_rate: number;
  preserved_verified: boolean;
  dependency_paths: string[][];
  dependency_edges: Array<{ from: string; to: string; relation: string }>;
  boundary_proof: string;
  summary: string;
}

export interface OutcomeContractRow {
  status?: "passed" | "failed" | "unverified";
  id: string;
  label: string;
  layer: "safety" | "service" | "risk_budget";
  actual: number | null;
  target: number;
  comparison: "=" | "<=" | ">=";
  unit: string;
  source: "system_safety" | "operator" | "demo_default";
  passed: boolean | null;
}

export interface OutcomeContract {
  method: string;
  status: "met" | "met_with_watch" | "target_missed" | "failed" | "unverified";
  source: "operator_plus_defaults" | "demo_default";
  passed: number;
  total: number;
  rows: OutcomeContractRow[];
  summary: string;
  semantics: string;
}

export interface ContractRepairItem {
  target_id: string;
  label: string;
  layer: "service" | "risk_budget";
  source: "operator" | "demo_default";
  comparison: "=" | "<=" | ">=";
  from_target: number;
  to_target: number;
  delta: number;
  unit: string;
  authority: "operator_confirmation" | "demo_policy";
  fragment: string;
}

export interface ContractRepairAlternative {
  plan_id: string;
  title: string;
  profile: string;
  selected: boolean;
  hard_safe: boolean;
  contract_status: string;
  passed: number;
  evidence_complete?: boolean;
  comparable_scope?: boolean;
  unknown_target_ids?: string[];
  total: number;
  failed_targets: number;
  failed_labels: string[];
  normalized_violation: number;
  schedule_changes: number;
  mean_delay_min: number;
  missed_connections: number;
  tail_exposed: number;
  score: number;
  frontier: boolean;
}

export interface ContractRepairAnalysis {
  method: string;
  status: "contract_met" | "switch_plan" | "minimal_amendment" | "safety_block" | "evidence_required";
  selected_plan_id: string;
  recommended_plan_id: string;
  hard_safety_relaxations: 0;
  unmet_target_ids: string[];
  unknown_target_ids?: string[];
  repairs: ContractRepairItem[];
  alternatives: ContractRepairAlternative[];
  suggested_command: string;
  recommendation: string;
  tradeoff: string;
  proof: string;
}

export interface OutcomeAudit {
  method: string;
  stage: "approved" | "automatic_replan" | "monitoring" | "human_review" | "recovered";
  status: "passed" | "passed_with_watch" | "human_required" | "failed";
  plan_id: string;
  plan_title: string;
  hard_constraints: {
    passed: number;
    total: number;
    all_passed: boolean | null;
    rows: Array<{ label: string; passed: boolean | null; kind: string }>;
  };
  metric_kind?: "forecast";
  simulation_method_version?: string;
  automatic_execution_eligible?: boolean;
  evidence?: { precision: string; passenger_scope: string; hard_proof: string; intent_binding: string };
  operator_checks: Array<{
    status?: "passed" | "failed" | "unverified" | "pending" | "rejected" | "superseded";
    reason?: string;
    intent_version?: number;
    evidence_sequences?: number[];
    id: string;
    label: string;
    actual?: number | null;
    limit?: number;
    comparison?: "=" | "<=" | ">=";
    unit?: string;
    passed: boolean | null;
    kind: string;
  }>;
  outcome_contract?: OutcomeContract;
  passengers: {
    scenario_total: number;
    decision_scope: number;
    out_of_scope: number;
    predicted_on_time: number;
    predicted_missed: number;
    expected_on_time_unrounded?: number;
    expected_missed_unrounded?: number;
    predicted_on_time_rate: number | null;
    tail_risk_exposed: number;
    tail_risk_exposure_rate: number | null;
  };
  operations: {
    flights_in_scope: number;
    fuel_breaches: number | null;
    remaining_queue_conflicts: number;
    mean_delay_min: number;
    p90_delay_min: number;
    recovery_minute: number;
    risk_shield: string;
  };
  comparison: null | {
    comparable: boolean;
    baseline_plan_id: string;
    final_plan_id: string;
    mean_delay_delta_min: number | null;
    p90_delay_delta_min: number | null;
    missed_connections_delta: number | null;
    remaining_conflicts_delta: number | null;
    scope_changed: { baseline_flights: number; final_flights: number };
  };
  scope_note: string;
  runtime_contract?: RuntimeContract;
}

export interface RuntimeContractRule {
  id: string;
  label: string;
  formula: string;
  passed: boolean;
  evidence: string;
}

export interface RuntimeContract {
  method: string;
  status: "passed" | "safe_stop" | "failed";
  target_phase: string;
  passed: number;
  total: number;
  maximum_response_min: number;
  observed_worst_response_min: number | null;
  rules: RuntimeContractRule[];
  responses: Array<{
    incident_type: string;
    incident_minute: number;
    response_type: string;
    response_minute: number | null;
    latency_min: number | null;
    passed: boolean;
  }>;
  safety_gates: Array<{
    event_type: string;
    minute: number;
    safety_gate: string;
    safe_stop: boolean;
    passed: boolean;
  }>;
  execution_envelope?: null | {
    method: string;
    passed: boolean;
    checked_actions: number;
    runway_windows: Record<string, Array<{ closed_from: number; reopened_at: number | null }>>;
    checks: Array<{
      flight_id: string;
      minute: number;
      runway: string;
      gate: string;
      plan_id: string | null;
      schedule_revision: string | null;
      passed: boolean;
      reasons: string[];
    }>;
    violations: string[];
    summary: string;
  };
  fuel_clock?: {
    method: string;
    observed_events: number;
    checked_flights: number;
    passed: boolean;
    events: Array<{
      event_type: string;
      minute: number;
      elapsed_min?: number;
      checked_flights: number;
      expired_flight_ids?: string[];
      passed: boolean;
      reasons: string[];
    }>;
    violations: string[];
    summary: string;
  };
  timeline: Array<{
    type: string;
    label: string;
    minute: number;
    sequence: number;
  }>;
  summary: string;
  caveat: string;
}

export interface ShadowTwinPlanSummary {
  plan_id: string;
  title: string;
  mean_loss_points: number;
  mean_delay_min: number;
  mean_missed_connections: number;
  fuel_risk_world_rate: number;
  mean_queue_triggers: number;
  mean_emissions_index: number;
}

export interface ShadowTwinAnalysis {
  method: string;
  status: "mandatory_switch" | "evidence_switch" | "keep_incumbent" | "safe_stop";
  decision: "switch" | "keep" | "safe_stop";
  runs: number;
  switch_cost_points?: number;
  scope?: {
    common_flight_ids: string[];
    common_flights: number;
    passengers: number;
    challenger_only_ids: string[];
    incumbent_only_ids: string[];
  };
  hard_gate?: {
    incumbent_operationally_valid: boolean;
    incumbent_failures: string[];
    incumbent_fuel_unsafe: boolean;
    challenger_valid: boolean;
    challenger_failures: string[];
  };
  incumbent?: ShadowTwinPlanSummary;
  challenger?: ShadowTwinPlanSummary;
  paired_gain?: {
    mean_points: number;
    ci95_low: number;
    ci95_high: number;
    probability_beats_cost: number;
  };
  weights?: Record<string, number>;
  reason?: string;
  summary: string;
  caveat: string;
}

export interface TwinTrustStream {
  id: string;
  flight_id: string;
  milestone: "ELDT" | "EIBT" | "TOBT";
  label: string;
  samples: number;
  covered: number;
  exceedances: number;
  empirical_coverage: number;
  e_value: number;
  max_e_value: number;
  familywise_e_value: number;
  status: "trusted" | "watch" | "drift";
}

export interface TwinTrustAnalysis {
  method: string;
  status: "trusted" | "watch" | "drift" | "unavailable";
  automation_mode: "guarded_autonomy" | "guarded" | "human_required";
  blocks_unattended_execution: boolean;
  target_coverage: number;
  alternative_coverage: number;
  familywise_streams: number;
  familywise_e_value: number;
  local_max_e_value: number;
  local_watch_threshold: number;
  stop_threshold: number;
  overall_empirical_coverage: number | null;
  samples_per_stream: number;
  strongest_stream: string | null;
  rows: TwinTrustStream[];
  summary: string;
  source: string;
  live_telemetry: boolean;
  guarantee?: string;
  caveat: string;
}

export interface StressMetrics {
  mean_delay: number;
  p90_delay: number;
  cvar_delay: number;
  missed: number;
  fuel_risk: number;
}

export interface StressWorld {
  id: string;
  label: string;
  label_en: string;
  change: string;
  change_en: string;
  status: "stable" | "watch" | "replan" | "manual";
  fixed_safe: boolean;
  adaptive_available: boolean;
  fixed: StressMetrics;
  adaptive: StressMetrics;
  saved_p90: number;
  p90_delta: number;
  saved_missed: number;
}

export interface RobustnessHorizon {
  method: string;
  runs_per_stress_world: number;
  stable_worlds: number;
  watch_worlds: number;
  direct_worlds: number;
  replan_worlds: number;
  total_worlds: number;
  adaptive_value_min: number;
  adaptive_p90_delta: number;
  summary: string;
  summary_en: string;
  worlds: StressWorld[];
}

export interface RegretMetrics {
  p90_delay: number;
  cvar_delay: number;
  missed: number;
  fuel_risk: number;
  conflicts: number;
  emissions: number;
}

export interface CandidateWorldRegret {
  world_id: string;
  world_label: string;
  fixed_safe: boolean;
  loss: number;
  regret: number;
  metrics: RegretMetrics;
}

export interface RegretCandidate {
  id: string;
  title: string;
  profile: string;
  max_regret: number;
  mean_regret: number;
  worst_world_id: string;
  worst_world_label: string;
  safe_worlds: number;
  total_worlds: number;
  robust_rank: number;
  by_world: CandidateWorldRegret[];
}

export interface RegretAnalysis {
  method: string;
  runs_per_world: number;
  world_count: number;
  nominal_id: string | null;
  recommended_id: string | null;
  recommendation_changed: boolean;
  summary: string;
  summary_en: string;
  worlds: Array<{
    id: string;
    label: string;
    label_en: string;
    change: string;
    change_en: string;
    best_plan_ids: string[];
  }>;
  candidates: RegretCandidate[];
}


export interface BargainingStakeholder {
  id: "airport" | "passenger" | "safety" | "climate";
  agent: string;
  label: string;
  label_en: string;
  objective: string;
  objective_en: string;
  weight: number;
  best_loss: number;
  disagreement_loss: number;
}

export interface BargainingCandidate {
  id: string;
  title: string;
  profile: string;
  utilities: Record<BargainingStakeholder["id"], number>;
  losses: Record<BargainingStakeholder["id"], number>;
  nash_welfare: number;
  minimum_utility: number;
  autonomy_blocked: boolean;
  hard_constraints_passed: boolean;
  bargaining_rank: number;
}

export interface BargainingAnalysis {
  method: string;
  status: "compromise_found" | "unavailable";
  nominal_id?: string;
  recommended_id: string | null;
  recommendation_changed?: boolean;
  reservation_utility?: number;
  stakeholders: BargainingStakeholder[];
  candidates: BargainingCandidate[];
  summary?: string;
  summary_en?: string;
  caveat?: string;
}

export interface InformationQuery {
  id: "weather_reopen_nowcast" | "critical_gate_readiness";
  world_id: "weather_tail" | "gate_shock";
  label: string;
  label_en: string;
  target: string;
  source: string;
  reveals: string;
  reveals_en: string;
  action_label: string;
  action_label_en: string;
  prior: number;
  regret_if_unobserved: number;
  expected_regret_reduction: number;
  priority_score: number;
  stress_status: "stable" | "watch" | "replan" | "manual" | "unknown";
  safety_relevant: boolean;
  decision_sensitive: boolean;
  resolved: boolean;
  mandatory_for_selected_plan?: boolean;
}

export interface InformationValue {
  method: string;
  status: "observe_then_optimize" | "sufficient" | "observation_acquired";
  source_plan_id: string;
  recommended_query_id: InformationQuery["id"] | null;
  baseline_expected_regret: number;
  assumed_world_priors: Record<string, number>;
  summary: string;
  summary_en: string;
  queries: InformationQuery[];
  resolved_query_ids: string[];
  caveat: string;
  last_observation?: Record<string, unknown>;
  recommendation_basis?: string;
}

export type AgentContractStatus =
  | "aligned"
  | "needs_evidence"
  | "resolved_watch"
  | "human_required"
  | "blocked";

export interface AgentContractCheck {
  id: string;
  producer: string;
  consumer: string;
  contract: string;
  status: AgentContractStatus;
  evidence: string;
  consequence: string;
  query_id: InformationQuery["id"] | null;
  hard_block: boolean;
}

export interface AgentArbitration {
  method: string;
  status: "aligned" | "needs_evidence" | "human_required" | "blocked";
  next_action: "proceed_to_approval" | "observe_then_optimize" | "human_approval" | "stop_and_repair";
  proposal_count: number;
  checks_total: number;
  aligned_count: number;
  open_issue_count: number;
  recommended_query_id: InformationQuery["id"] | null;
  checks: AgentContractCheck[];
  summary: string;
  policy: string;
  hidden_reasoning: boolean;
  source: string;
  caveat: string;
}

export interface ApprovalRequiredQuery {
  query_id: InformationQuery["id"];
  producer: string;
  consumer: string;
  contract: string;
  evidence: string;
  consequence: string;
}

export interface CandidateApprovalGate {
  schema_version: number;
  plan_id: string;
  status: "ready" | "needs_evidence" | "human_required" | "blocked";
  can_human_approve: boolean;
  next_action: "human_approve" | "observe_then_replan" | "human_acknowledge_risk" | "stop_and_repair";
  required_query_ids: InformationQuery["id"][];
  required_queries: ApprovalRequiredQuery[];
  hard_block_reasons: string[];
  resolved_query_ids: string[];
  risk_disclosure: string;
  evidence_binding: {
    trusted_state_digest: string;
    plan_evidence_digest: string;
    valid: boolean;
  };
  arbitration_status: AgentArbitration["status"];
  arbitration_summary: string;
  summary: string;
  human_approved: boolean;
  approved_minute?: number;
  risk_acknowledged?: boolean;
}

export interface PreparedPolicyResponse {
  status: "ready" | "solve_on_trigger" | "unavailable";
  world_id: string | null;
  plan_id?: string | null;
  plan_title?: string | null;
  profile?: string | null;
  assignment_count: number;
  assignments: Assignment[];
  expected_p90_delay_min?: number;
  expected_missed_connections?: number;
  hard_constraints_passed: boolean;
  proof?: string[];
  source: string;
}

export interface ContingencyBranch {
  id: string;
  label: string;
  label_en: string;
  trigger_types: string[];
  resource_ids: string[];
  condition: string;
  condition_en: string;
  action: "global_replan" | "causal_local_repair" | "priority_replan";
  scope: string;
  scope_en: string;
  solver: string;
  reason: string;
  prepared_response: PreparedPolicyResponse;
}

export interface ContingencyPolicy {
  id: string;
  method: string;
  source_plan_id: string;
  source_plan_title: string;
  robust_plan_id: string | null;
  robust_plan_title: string | null;
  approved: boolean;
  authorization: string;
  default_action: string;
  branch_count: number;
  branches: ContingencyBranch[];
  evidence: {
    stress_worlds: number;
    prepared_branches: number;
    regret_method: string;
    hard_safety_gate: string;
  };
  summary: string;
  summary_en: string;
}

export interface PolicyRuntimeDecision {
  event_id: string;
  matched: boolean;
  policy_id?: string;
  branch_id: string | null;
  branch_label?: string;
  action: string;
  scope: string;
  solver?: string;
  matched_resource?: string | null;
  reason: string;
  safety_gate: string;
  approved_envelope?: boolean;
  prepared_response?: PreparedPolicyResponse;
  warm_start_flights?: number;
}

export interface ParetoPoint {
  id: string;
  title: string;
  profile: string;
  pareto_optimal: boolean;
  ideal_distance: number;
  score: number;
  delay: number;
  connections: number;
  fuel_risk: number;
  conflicts: number;
  emissions: number;
  risk_shield_status: "clear" | "watch" | "escalate";
}

export interface ParetoAnalysis {
  method: string;
  recommended_id: string | null;
  frontier_ids: string[];
  points: ParetoPoint[];
}
export interface AcdmMilestone {
  code: "STA" | "ELDT" | "EIBT" | "TOBT" | "TSAT";
  label: string;
  label_en: string;
  minute: number | null;
  status: "published" | "predicted" | "committed_target" | "exercise_target" | "out_of_scope";
  owner: string;
  source: string;
  role: "solver_input" | "solver_output" | "derived" | "external_missing";
  confidence: number;
  note: string;
  uncertainty: {
    lower_minute: number;
    upper_minute: number;
    radius_min: number;
    coverage: number;
    calibration_samples: number;
    method: string;
  } | null;
}

export interface AcdmFlightRecord {
  flight_id: string;
  aircraft_type: string;
  current_state: string;
  planned_gate: string;
  assigned_gate: string;
  assigned_runway: string;
  gate_changed: boolean;
  delay_min: number;
  turnaround_assumption_min: number;
  milestones: AcdmMilestone[];
}

export interface AcdmLedger {
  method: string;
  scope: string;
  plan_id: string;
  plan_title: string;
  status: "candidate" | "committed";
  generated_minute: number;
  flight_count: number;
  full_acdm: boolean;
  departure_sequencer_connected: boolean;
  uncertainty_calibration: {
    method: string;
    target_coverage: number;
    calibration_samples: number;
    evaluation_samples?: number;
    empirical_coverage?: number;
    calibration_health?: "healthy" | "degraded";
    average_radius_min: number;
    source: string;
    guarantee_scope: string;
    upgrade_path: string;
  };
  data_quality: {
    weather_source: string;
    lineage_coverage: number;
    authoritative_milestones: number;
    solver_outputs: number;
    derived_milestones: number;
    missing_external_milestones: number;
  };
  solver_contract: {
    reads: string[];
    writes: string[];
    derives: string[];
    never_writes: string[];
  };
  caveat: string;
  records: AcdmFlightRecord[];
}
export interface CausalAttributionMetrics {
  risk_points: number;
  mean_delay_min: number;
  p90_delay_min: number;
  missed_passengers: number;
  queue_triggers: number;
  fuel_risk_pct: number;
  emissions_index: number;
}

export interface CausalAttributionFactor extends CausalAttributionMetrics {
  id: "weather_runway" | "gate_pressure" | "dynamic_incident";
  label: string;
  label_en: string;
  detail: string;
  share_percent: number;
}

export interface CausalAttributionAnalysis {
  method: string;
  status: "completed" | "partial";
  profile: string;
  runs_per_coalition: number;
  common_seed_schedule: boolean;
  factor_count: number;
  coalition_count: number;
  passenger_denominator: number;
  flight_denominator: number;
  baseline: CausalAttributionMetrics;
  full: CausalAttributionMetrics;
  total_burden: CausalAttributionMetrics;
  factors: CausalAttributionFactor[];
  coalitions: Array<{
    id: string;
    active_factors: string[];
    feasible: boolean;
    reason: string | null;
    metrics: CausalAttributionMetrics;
  }>;
  additivity: {
    risk_delta_points: number;
    attributed_sum_points: number;
    absolute_error: number;
    passed: boolean;
  };
  top_factor_id: CausalAttributionFactor["id"] | null;
  summary: string;
  interpretation: string;
  caveat: string;
}


export interface PassengerRescueAnalysis {
  method: string;
  status: "ready" | "capacity_shortfall" | "not_needed";
  source_plan_id: string;
  at_risk_passengers: number;
  reaccommodated_passengers: number;
  remaining_stranded: number;
  same_day_recovery_rate: number;
  tail_watch_only: number;
  mean_extra_wait_min: number;
  p90_extra_wait_min: number;
  estimated_intervention_cost: number;
  partner_airline_passengers: number;
  groups: Array<{
    id: string;
    flight_id: string;
    destination: string;
    at_risk_passengers: number;
    original_connections: number;
    miss_probability: number;
    upper_gate_in_90: number;
    minimum_rebook_departure: number;
    reaccommodated: number;
    remaining_stranded: number;
    service_rate: number;
  }>;
  options: Array<{
    id: string;
    destination: string;
    route: string;
    departure_minute: number;
    capacity: number;
    allocated: number;
    remaining_seats: number;
    kind: "same_airline" | "alliance_partner" | "late_wave";
    label: string;
    synthetic: boolean;
  }>;
  flows: Array<{
    from_flight: string;
    to_option: string;
    destination: string;
    passengers: number;
    departure_minute: number;
    robust_wait_min: number;
    kind: "same_airline" | "alliance_partner" | "late_wave";
    label: string;
  }>;
  actions?: string[];
  constraints: {
    flow_conservation: boolean;
    seat_capacity: boolean;
    robust_connection_window: boolean;
  };
  counterfactual?: {
    without_rescue_stranded: number;
    with_rescue_stranded: number;
    stranding_avoided: number;
  };
  summary: string;
  source?: string;
  caveat: string;
}


export interface ConflictCoreItem {
  id: string;
  category: string;
  label: string;
  rule: string;
  affected: string[];
  safety_class: "hard_safety" | "operator_soft" | "operational";
  relaxable: boolean;
  remedy: string;
}

export interface InfeasibilityAnalysis {
  reasons?: string[];
  relaxations?: string[];
  method?: string;
  solver_status?: string;
  core_irreducible?: boolean;
  solver_calls?: number;
  hard_safety_relaxations_allowed?: number;
  summary?: string;
  conflict_core?: ConflictCoreItem[];
  options?: Array<{
    id: string;
    label: string;
    effect: string;
  }>;
}

export interface EventIntegrity {
  method: string;
  algorithm: string;
  status: "empty" | "verified" | "broken";
  valid: boolean;
  tamper_evident: boolean;
  event_count: number;
  first_invalid_sequence: number | null;
  head_hash: string;
  genesis_hash: string;
  links: Array<{
    sequence: number;
    sim_minute: number;
    type: string;
    hash_prefix: string;
    valid: boolean;
    count: number;
  }>;
  coverage: {
    first_sequence: number | null;
    last_sequence: number | null;
  };
  caveat: string;
}


export interface PreferenceWeightPosterior {
  id: "delay" | "connections" | "fuel_risk" | "conflicts" | "emissions";
  label: string;
  label_en: string;
  prior: number;
  mean: number;
  low90: number;
  high90: number;
}

export interface PreferenceLearningAnalysis {
  method: string;
  status: "learning" | "complete" | "unavailable";
  particle_count?: number;
  prior_strength?: number;
  comparison_count: number;
  session_comparison_count?: number;
  historical_comparison_count?: number;
  memory_scope?: string;
  latest_observation_id?: number;
  comparisons: Array<{
    left_plan_id: string;
    right_plan_id: string;
    preferred_plan_id: string;
  }>;
  prior_weights?: Record<string, number>;
  posterior: {
    weights: PreferenceWeightPosterior[];
    effective_particles: number;
  };
  candidates: Array<{
    id: string;
    title: string;
    profile: string;
    posterior_expected_loss: number;
    optimal_probability: number;
    preference_rank: number;
    raw_metrics: Record<string, number>;
  }>;
  nominal_id?: string;
  recommended_id: string | null;
  recommendation_changed?: boolean;
  approved_plan_id?: string;
  human_override?: boolean;
  approval_note?: string;
  query: null | {
    left_plan_id: string;
    left_title: string;
    right_plan_id: string;
    right_title: string;
    expected_information_bits: number;
    predicted_left_probability: number;
    objective_distance: number;
  };
  summary: string;
  summary_en: string;
  caveat: string;
}


export interface MitigationMetricRow {
  passenger_scope: number;
  predicted_on_time: number;
  predicted_missed: number;
  predicted_on_time_rate: number;
  tail_risk_exposed: number;
  tail_risk_exposure_rate: number;
}

export interface MitigationAnalysis {
  method: string;
  status: "not_needed" | "target_met" | "partial";
  source_plan_id: string;
  scope: {
    flights_considered: number;
    passengers: number;
    aircraft_assignments_frozen: boolean;
  };
  targets: {
    source: "operator" | "proactive_guardrail";
    minimum_on_time_rate: number;
    maximum_missed_connections: number;
    maximum_tail_exposure_rate: number;
    maximum_tail_exposed: number;
  };
  baseline: MitigationMetricRow;
  mitigated: MitigationMetricRow;
  deltas: {
    predicted_missed_saved: number;
    tail_exposure_reduced: number;
    on_time_rate_gain_points: number;
  };
  selected_actions: Array<{
    id: string;
    kind: "connection_assist" | "airside_shuttle" | "protected_hold";
    flight_id: string;
    label: string;
    label_en: string;
    bonus_min: number;
    estimated_saved_passengers: number;
    estimated_tail_removed: number;
    cost_points: number;
    resources: Record<string, number>;
  }>;
  candidate_action_count: number;
  approved?: boolean;
  approval_note?: string;
  resources: Array<{
    id: string;
    label: string;
    label_en: string;
    used: number;
    budget: number;
  }>;
  proof: {
    solver_status: string;
    validation_runs: number;
    screening_runs: number;
    paired_common_random_numbers: boolean;
    assignment_fingerprint_before: string;
    assignment_fingerprint_after: string;
    assignments_unchanged: boolean;
    operational_metrics_unchanged: boolean;
    safety_constraints_unchanged: boolean;
  };
  summary: string;
  summary_en: string;
  caveat: string;
}


export interface FlightContrastiveWitness {
  id: string;
  label: string;
  status: "pass" | "context";
  hard: boolean;
  detail: string;
}

export interface FlightContrastiveAlternative {
  id: string;
  label: string;
  field: "runway" | "gate" | "landing_minute";
  from: string | number;
  to: string | number;
  status: "feasible" | "infeasible";
  verdict: string;
  changed_flights: number | null;
  delta: {
    objective: number;
    mean_delay_min: number;
    p90_delay_min: number;
    missed_connections: number;
    emissions_index: number;
  } | null;
  evidence: string[];
}

export interface FlightContrastiveExplanation {
  method: string;
  flight_id: string;
  plan_id: string;
  scope_flights: number;
  simulation_runs: number;
  baseline: {
    runway: string;
    gate: string;
    landing_minute: number;
    mean_delay_min: number;
    p90_delay_min: number;
    missed_connections: number;
    emissions_index: number;
  };
  witnesses: FlightContrastiveWitness[];
  alternatives: FlightContrastiveAlternative[];
  summary: string;
  safety_statement: string;
}


export interface OperationalBaselineEvaluation {
  status: "safe" | "unsafe_against_truth" | "safe_abstention";
  hard_constraints_passed: boolean;
  unsafe_execution: boolean;
  safe_abstention: boolean;
  changed_flights: number;
  mean_delay_min: number | null;
  p90_delay_min: number | null;
  missed_connections: number | null;
  violations: string[];
}

export interface OperationalBaselineRow {
  id: "rules" | "solo_cp_sat" | "updated_cp_sat" | "stormshift";
  label: string;
  label_en: string;
  nominal: OperationalBaselineEvaluation;
  fault_trials: {
    worlds: number;
    hard_passed_worlds: number;
    unsafe_execution_worlds: number;
    safe_abstentions: number;
    hard_pass_rate: number;
    safe_decision_rate: number;
    metric_eligible_worlds: number;
    mean_changed_flights: number;
    mean_delay_min: number | null;
    mean_p90_delay_min: number | null;
    mean_missed_connections: number | null;
  };
  capabilities: Record<string, boolean>;
}

export interface OperationalBaselineAnalysis {
  simulation_method_version?: string;
  dynamic_comparison?: { eligible_worlds: number; mean_delay_delta_min: number | null; same_input_and_budget: boolean };
  method: string;
  status: string;
  scope: {
    fault_worlds: number;
    simulation_runs_per_plan: number;
    active_flights: number;
    commercial_products_compared: number;
  };
  rows: OperationalBaselineRow[];
  worlds: Array<{
    id: string;
    label: string;
    label_en: string;
    changed_fact: string;
    stale_value: unknown;
    truth_value: unknown;
    outcomes: Record<string, {
      status: string;
      hard_constraints_passed: boolean;
      safe_abstention: boolean;
      changed_flights: number;
      violations: string[];
    }>;
  }>;
  nominal_solver_gap_min: number;
  finding: string;
  finding_en: string;
  disclaimer: string;
  disclaimer_en: string;
}



export interface ProofObligation {
  id: string;
  label: string;
  mandatory: boolean;
  status: "pass" | "fail" | "human_review";
  satisfied: boolean;
  evidence: string;
}

export interface ProofPlanCertificate {
  version: string;
  plan_id: string;
  profile: string;
  issued_sequence: number;
  input_digest: string;
  executable_fingerprint: string;
  evidence_fingerprint: string;
  status: "blocked" | "human_review_required" | "ready_for_approval";
  execution_mode: "human_approval_required" | "guarded_execution";
  obligations: ProofObligation[];
  obligation_count: number;
  passed_obligations: number;
  failed_obligations: number;
  scope: string;
  caveat: string;
  certificate_id: string;
  certificate_hash: string;
}

export interface ProofPlanVerification {
  method: string;
  status: "verified" | "rejected";
  valid: boolean;
  certificate_id: string | null;
  certificate_hash: string;
  plan_id: string;
  checks: Array<{
    id: string;
    passed: boolean;
    evidence: string;
  }>;
  failed_checks: string[];
  summary: string;
  caveat: string;
}

export interface CommittedOperation {
  flight_id: string;
  plan_id: string;
  revision: number;
  landing_minute: number;
  runway: string;
  runway_release_minute: number;
  gate: string;
  gate_start_minute: number;
  gate_release_minute: number;
  stage: "taxiing" | "at_gate" | "released";
  runway_released: boolean;
}

export interface AdvanceReceipt {
  request_id: string;
  target_minute: number;
  expected_revision: number;
  resulting_revision: number;
  resulting_minute: number;
  event_sequences: number[];
  next_event_minute: number | null;
  stopped_reason: string | null;
}

export interface ConnectingDeparture {
  flight_id: string; source_flight_id: string; passenger_count: number;
  passenger_stage: "onboard" | "disembarking" | "transferring" | "waiting" | "boarding" | "boarded";
  stage: "waiting_aircraft" | "turnaround" | "boarding" | "pushback" | "taxiing" | "holding_short" | "takeoff" | "airborne" | "completed" | "blocked" | "cancelled";
  source_gate: string | null; source_arrival_minute: number | null;
  transfer_start_minute: number | null; transfer_ready_minute: number | null;
  carrier_flight_id: string | null; gate: string | null; runway: string | null;
  boarding_start_minute: number | null; pushback_minute: number | null;
  taxi_start_minute: number | null; holding_short_minute: number | null;
  takeoff_minute: number | null; airborne_minute: number | null; completed_minute: number | null;
  next_minute: number | null; boarded_count: number; safety_checks: string[]; blocked_reason: string | null;
}

export interface ExecutionCursor {
  version: number;
  revision: number;
  active_plan_id: string;
  connection?: ConnectingDeparture | null;
  minute: number;
  status: "ready" | "running" | "paused" | "blocked" | "completed";
  scope_flight_ids: string[];
  pending_assignments: Assignment[];
  committed_operations: CommittedOperation[];
  scheduled_events: AirportEvent[];
  processed_event_ids: string[];
  request_receipts: Record<string, AdvanceReceipt>;
  last_stopped_reason: string | null;
}

export interface ActionDiffRow {
  flight_id: string;
  status: "changed" | "unchanged" | "added" | "removed";
  changed_fields: string[];
  before: Assignment | null;
  after: Assignment | null;
}

export interface ActionDiff {
  committed_count_at_replan?: number | null;
  replan_minute?: number;
  source_plan_id: string;
  replacement_plan_id: string;
  changed: number;
  unchanged: number;
  added: number;
  removed: number;
  rows: ActionDiffRow[];
  scope: string;
}

export interface SessionState {
  id: string;
  mode: Mode;
  data_mode: DataMode;
  scenario: Scenario;
  phase: Phase;
  intent: Intent | null;
  plans: Plan[];
  selected_plan_id: string | null;
  traces: AgentTrace[];
  sequence: number;
  created_at: string;
  updated_at: string;
  llm_tier: "deep" | "fast" | "fallback";
  data_badge: string;
  data_snapshot: Record<string, unknown>;
  execution: ExecutionCursor | null;
  decision_lab: {
    action_diff?: ActionDiff;
    action_diff_by_plan?: Record<string, ActionDiff>;
    counterfactuals?: Array<Record<string, number | string>>;
    operational_baseline?: OperationalBaselineAnalysis;
    sensitivity?: Array<Record<string, number | string | null>>;
    robustness?: RobustnessHorizon;
    regret?: RegretAnalysis;
    bargaining?: BargainingAnalysis;
    preference_learning?: PreferenceLearningAnalysis;
    agent_arbitration?: AgentArbitration;
    agent_arbitration_by_plan?: Record<string, AgentArbitration>;
    approval_gate?: CandidateApprovalGate;
    approval_gates?: Record<string, CandidateApprovalGate>;
    agent_proposals?: Array<Record<string, unknown>>;
    information_value?: InformationValue;
    policy?: ContingencyPolicy;
    policy_runtime?: PolicyRuntimeDecision;
    pareto?: ParetoAnalysis;
    explanation?: Record<string, unknown>;
    infeasible?: InfeasibilityAnalysis;
    acdm?: AcdmLedger;
    acdm_by_plan?: Record<string, AcdmLedger>;
    recourse?: RecourseAudit;
    recourse_by_plan?: Record<string, RecourseAudit>;
    outcome_audit?: OutcomeAudit;
    runtime_contract?: RuntimeContract;
    twin_trust?: TwinTrustAnalysis;
    twin_trust_by_plan?: Record<string, TwinTrustAnalysis>;
    shadow_twin?: ShadowTwinAnalysis;
    causal_attribution?: CausalAttributionAnalysis;
    passenger_rescue?: PassengerRescueAnalysis;
    passenger_rescue_by_plan?: Record<string, PassengerRescueAnalysis>;
    contract_repair?: ContractRepairAnalysis;
    contract_repair_by_plan?: Record<string, ContractRepairAnalysis>;
    mitigation?: MitigationAnalysis;
    mitigation_by_plan?: Record<string, MitigationAnalysis>;
    event_integrity?: EventIntegrity;
    trusted_state?: TrustedOperationalState;
    plan_certificate?: ProofPlanCertificate;
    plan_certificates?: Record<string, ProofPlanCertificate>;
    proof_carrying_verification?: ProofPlanVerification;
    falsification?: FalsificationAnalysis;
    falsification_by_plan?: Record<string, FalsificationAnalysis>;
    flight_contrasts?: Record<string, FlightContrastiveExplanation>;
  };
}

export interface ExecutionEvent {
  sequence: number;
  session_id: string;
  sim_minute: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
  prev_hash?: string;
  event_hash?: string;
}

export interface FalsificationRepair {
  status: "not_required" | "not_evaluated" | "safe_repair" | "unsafe_repair" | "manual_required";
  available: boolean | null;
  plan_id?: string | null;
  changed_flights: string[];
  changed_count: number;
  hard_constraints_passed: boolean | null;
  proof: string[];
}

export interface FalsificationAxis {
  id: "runway_closure" | "gate_outage" | "separation_increase" | "fuel_margin_loss";
  label: string;
  label_en: string;
  status: "witness" | "lower_bound";
  target_id: string | null;
  delta: number | null;
  unit: string;
  distance_ratio: number | null;
  description: string;
  description_en: string;
  failures: string[];
  minimality: {
    baseline_passed: boolean;
    predecessor_delta: number;
    predecessor_passed: boolean;
    witness_failed: boolean;
  };
  searched_targets: number;
  evaluations: number;
  search_limit: number;
  target_search_limits?: Record<string, number>;
  repair: FalsificationRepair;
}

export interface FalsificationAnalysis {
  method: string;
  status: "completed" | "not_applicable" | "baseline_invalid";
  plan_id: string;
  categories_tested: number;
  witness_count: number;
  evaluations: number;
  nearest_witness_id: FalsificationAxis["id"] | null;
  margin_tier: "thin" | "watch" | "buffered" | "invalid" | "not_applicable";
  summary: string;
  summary_en: string;
  axes: FalsificationAxis[];
  caveat: string;
}

export interface ArgumentationNode {
  id: string;
  fact_key: string;
  value: unknown;
  source: string;
  age_min: number;
  authority: number;
  freshness: number;
  confidence: number;
  strength: number;
  verdict: "accepted" | "rejected" | "undecided";
}

export interface ArgumentationEdge {
  from: string;
  to: string;
  fact_key: string;
  basis: string;
  score_gap: number;
}

export interface ArgumentationAnalysis {
  method: string;
  status: "resolved" | "blocked";
  claim_count: number;
  conflict_keys: string[];
  attack_count: number;
  accepted_ids: string[];
  rejected_ids: string[];
  undecided_ids: string[];
  unresolved_keys: string[];
  conflict_free: boolean;
  nodes: ArgumentationNode[];
  edges: ArgumentationEdge[];
  summary: string;
  caveat: string;
}
