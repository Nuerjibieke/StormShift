"""Pydantic contracts shared by the API, optimizer, agents, and persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Mode(str, Enum):
    guided = "guided"
    scenarios = "scenarios"
    sandbox = "sandbox"


class DataMode(str, Enum):
    synthetic = "synthetic"
    live = "live"
    imported = "imported"


class Phase(str, Enum):
    normal = "normal"
    incident = "incident"
    analyzing = "analyzing"
    candidates = "candidates"
    awaiting_approval = "awaiting_approval"
    executing = "executing"
    monitoring = "monitoring"
    replanning = "replanning"
    recovered = "recovered"
    infeasible = "infeasible"


class ResourceStatus(str, Enum):
    open = "open"
    closed = "closed"
    available = "available"
    occupied = "occupied"
    blocked = "blocked"
    reserved = "reserved"


class FlightState(str, Enum):
    inbound = "inbound"
    approach = "approach"
    gate_delayed = "gate_delayed"
    boarding = "boarding"
    holding = "holding"
    landed = "landed"
    taxiing = "taxiing"
    at_gate = "at_gate"
    turnaround_complete = "turnaround_complete"
    diverted = "diverted"


class EventType(str, Enum):
    storm = "storm"
    runway_closed = "runway_closed"
    runway_reopened = "runway_reopened"
    gate_outage = "gate_outage"
    ground_vehicle_failure = "ground_vehicle_failure"
    medical_diversion = "medical_diversion"
    fuel_alert = "fuel_alert"
    operator_command = "operator_command"
    plan_generated = "plan_generated"
    plan_approved = "plan_approved"
    execution_tick = "execution_tick"
    recovered = "recovered"
    data_snapshot = "data_snapshot"


class PlanProfile(str, Enum):
    balanced = "balanced"
    passenger = "passenger"
    capacity = "capacity"
    resilient = "resilient"
    green = "green"
    operator = "operator"


class RunwaySpec(StrictModel):
    id: str
    status: ResourceStatus = ResourceStatus.open
    separation_min: int = Field(default=3, ge=1, le=10)
    capacity_per_hour: int = Field(default=20, ge=1, le=80)
    reopen_minute: int | None = Field(default=None, ge=0, le=240)
    reason: str | None = None


class GateSpec(StrictModel):
    id: str
    status: ResourceStatus = ResourceStatus.available
    occupied_by: str | None = None
    available_minute: int = Field(default=0, ge=0, le=240)
    remote: bool = False
    taxi_minutes: int = Field(default=5, ge=1, le=30)


class ResourceReservation(StrictModel):
    """A committed, immutable resource interval from an executed operation."""

    id: str = Field(default_factory=lambda: new_id("reservation"))
    kind: Literal["runway", "gate"]
    resource_id: str
    flight_id: str
    start_minute: int = Field(ge=0, le=480)
    end_minute: int = Field(gt=0, le=480)
    source_plan_id: str | None = None
    revision: int = Field(default=0, ge=0)
    committed_minute: int = Field(default=0, ge=0, le=240)

    @model_validator(mode="after")
    def validate_interval(self) -> "ResourceReservation":
        if self.end_minute <= self.start_minute:
            raise ValueError(
                "resource reservation end_minute must be after start_minute"
            )
        return self


class FlightSpec(StrictModel):
    id: str
    aircraft_type: str = "A320"
    state: FlightState = FlightState.inbound
    scheduled_arrival: int = Field(default=0, ge=0, le=240)
    planned_gate: str
    connections: int = Field(default=0, ge=0, le=600)
    fuel_margin_min: int = Field(default=40, ge=0, le=180)
    medical_priority: bool = False
    emissions_factor: float = Field(default=1.0, ge=0.2, le=4)
    origin: str = "—"


class WeatherSpec(StrictModel):
    severity: int = Field(default=1, ge=0, le=5)
    label: str = "正常"
    visibility_km: float = Field(default=10, ge=0.1, le=100)
    closure_mean_min: float = Field(default=0, ge=0, le=240)
    closure_std_min: float = Field(default=0, ge=0, le=90)
    source: str = "synthetic"
    observed_at: datetime | None = None
    raw_report: str | None = None


class EventSpec(StrictModel):
    id: str = Field(default_factory=lambda: new_id("evt"))
    type: EventType
    minute: int = Field(default=0, ge=0, le=240)
    resource_id: str | None = None
    severity: int = Field(default=1, ge=0, le=5)
    duration_min: int = Field(default=0, ge=0, le=240)
    payload: dict[str, Any] = Field(default_factory=dict)


class LayoutPoint(StrictModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class RunwayLayout(StrictModel):
    id: str
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=.2)


class GateLayout(StrictModel):
    id: str
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class TerminalLayout(StrictModel):
    id: str = "T1"
    label: str = "TERMINAL"
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class AirportLayoutSpec(StrictModel):
    source_type: Literal["synthetic_schematic", "public_aip_simplified"]
    source_label: str
    source_url: str | None = None
    effective_date: str | None = None
    geometry_fidelity: Literal["schematic", "public-layout-simplified"] = "schematic"
    terminal: TerminalLayout
    runways: list[RunwayLayout]
    gates: list[GateLayout]
    taxiways: list[list[LayoutPoint]] = Field(default_factory=list)
    disclaimer: str


class ScenarioSpec(StrictModel):
    id: str
    name: str
    airport: str
    airport_icao: str | None = None
    mode: Mode
    seed: int = Field(default=2026, ge=0)
    current_minute: int = Field(default=0, ge=0, le=240)
    runways: list[RunwaySpec]
    gates: list[GateSpec]
    flights: list[FlightSpec]
    weather: WeatherSpec
    events: list[EventSpec] = Field(default_factory=list)
    layout: AirportLayoutSpec | None = None
    committed_reservations: list[ResourceReservation] = Field(
        default_factory=list
    )
    disclaimer: str = "运营演练，不代表真实机场决策"

    @model_validator(mode="after")
    def validate_unique_resources(self) -> "ScenarioSpec":
        for values, label in ((self.runways, "runway"), (self.gates, "gate"), (self.flights, "flight")):
            ids = [value.id for value in values]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {label} id")
        if self.layout is not None:
            resource_runways = {item.id for item in self.runways}
            layout_runways = {item.id for item in self.layout.runways}
            resource_gates = {item.id for item in self.gates}
            layout_gates = {item.id for item in self.layout.gates}
            if layout_runways != resource_runways:
                raise ValueError("layout runway ids must match scenario runway ids")
            if layout_gates != resource_gates:
                raise ValueError("layout gate ids must match scenario gate ids")
        return self


class ObjectiveWeights(StrictModel):
    delay: float = Field(default=0.30, ge=0, le=1)
    connections: float = Field(default=0.30, ge=0, le=1)
    fuel_risk: float = Field(default=0.20, ge=0, le=1)
    conflicts: float = Field(default=0.10, ge=0, le=1)
    emissions: float = Field(default=0.10, ge=0, le=1)

    def normalized(self) -> "ObjectiveWeights":
        values = self.model_dump()
        total = sum(values.values()) or 1.0
        return ObjectiveWeights(**{key: value / total for key, value in values.items()})


class ScheduledConstraint(StrictModel):
    kind: Literal["close_resource", "reopen_resource", "priority", "delay_cap"]
    resource_id: str | None = None
    minute: int | None = Field(default=None, ge=0, le=240)
    value: float | str | None = None
    hard: bool = False


class IntentIssue(StrictModel):
    code: str
    excerpt: str
    message: str


class IntentEvidence(StrictModel):
    field: str
    excerpt: str


class IntentClarificationQuestion(StrictModel):
    issue_index: int = Field(ge=0)
    target: str = "clause"
    question: str
    placeholder: str


class IntentClarificationTurn(StrictModel):
    version: int = Field(ge=0)
    issue_index: int = Field(ge=0)
    question: str
    excerpt: str
    answer: str
    replacement: str


class OperatorIntent(StrictModel):
    original_text: str | None = None
    clarification_questions: list[IntentClarificationQuestion] = Field(default_factory=list)
    clarification_history: list[IntentClarificationTurn] = Field(default_factory=list, max_length=8)
    base_minute: float = 0
    specified_fields: list[str] = Field(default_factory=list)
    cleared_fields: list[str] = Field(default_factory=list)
    inherited_fields: list[str] = Field(default_factory=list)
    evidence: list[IntentEvidence] = Field(default_factory=list)
    issues: list[IntentIssue] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    # Version zero and no confirmation clock identify old sessions.
    version: int = Field(default=0, ge=0)
    confirmed_minute: float | None = None
    raw_text: str
    weights: ObjectiveWeights = Field(default_factory=ObjectiveWeights)
    average_delay_cap: float | None = Field(default=None, ge=0, le=180)
    minimum_connection_rate: float | None = Field(default=None, ge=0, le=1)
    maximum_missed_connections: int | None = Field(default=None, ge=0, le=6000)
    maximum_tail_exposure_rate: float | None = Field(default=None, ge=0, le=1)
    scheduled_constraints: list[ScheduledConstraint] = Field(default_factory=list)
    summary: str = "使用默认均衡目标"
    needs_confirmation: bool = True
    source: Literal["text", "voice", "fallback"] = "text"


class AgentProposal(StrictModel):
    agent: str
    summary: str
    observations: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)
    tool_evidence: dict[str, Any] = Field(default_factory=dict)


class Explanation(StrictModel):
    recommendation: str
    why: list[str] = Field(default_factory=list)
    why_not: list[str] = Field(default_factory=list)
    sensitivities: list[str] = Field(default_factory=list)
    caveat: str = "所有动作由约束求解器生成并通过安全检查"


class FlightAssignment(StrictModel):
    flight_id: str
    runway: str
    landing_minute: int
    gate: str
    hold_minutes: int
    taxi_minutes: int
    remote_stand: bool = False


class CommittedOperation(StrictModel):
    flight_id: str
    plan_id: str
    revision: int = Field(ge=1)
    landing_minute: int = Field(ge=0, le=480)
    runway: str
    runway_release_minute: int = Field(gt=0, le=480)
    gate: str
    gate_start_minute: int = Field(ge=0, le=480)
    gate_release_minute: int = Field(gt=0, le=480)
    stage: Literal["taxiing", "at_gate", "released"] = "taxiing"
    runway_released: bool = False


class ConnectingDeparture(StrictModel):
    """One explicit synthetic connection, separate from inbound optimization metrics."""
    flight_id: str = "NH701"
    source_flight_id: str
    passenger_count: int = Field(ge=1, le=600)
    passenger_stage: Literal["onboard", "disembarking", "transferring", "waiting", "boarding", "boarded"] = "onboard"
    stage: Literal["waiting_aircraft", "turnaround", "boarding", "pushback", "taxiing", "holding_short", "takeoff", "airborne", "completed", "blocked", "cancelled"] = "waiting_aircraft"
    source_gate: str | None = None
    source_arrival_minute: int | None = None
    transfer_start_minute: int | None = None
    transfer_ready_minute: int | None = None
    carrier_flight_id: str | None = None
    gate: str | None = None
    runway: str | None = None
    boarding_start_minute: int | None = None
    pushback_minute: int | None = None
    taxi_start_minute: int | None = None
    holding_short_minute: int | None = None
    takeoff_minute: int | None = None
    airborne_minute: int | None = None
    completed_minute: int | None = None
    next_minute: int | None = None
    boarded_count: int = 0
    safety_checks: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None


class AdvanceReceipt(StrictModel):
    request_id: str
    target_minute: int
    expected_revision: int
    resulting_revision: int
    resulting_minute: int
    event_sequences: list[int] = Field(default_factory=list)
    next_event_minute: int | None = None
    stopped_reason: str | None = None


class ExecutionCursor(StrictModel):
    version: int = 1
    revision: int = Field(default=1, ge=1)
    active_plan_id: str
    minute: int = Field(default=0, ge=0, le=480)
    status: Literal[
        "ready", "running", "paused", "blocked", "completed"
    ] = "ready"
    scope_flight_ids: list[str] = Field(default_factory=list)
    pending_assignments: list[FlightAssignment] = Field(default_factory=list)
    committed_operations: list[CommittedOperation] = Field(default_factory=list)
    scheduled_events: list[EventSpec] = Field(default_factory=list)
    processed_event_ids: list[str] = Field(default_factory=list)
    request_receipts: dict[str, AdvanceReceipt] = Field(default_factory=dict)
    last_stopped_reason: str | None = None
    connection: ConnectingDeparture | None = None


class SimulationReport(StrictModel):
    # Missing fields in persisted V1/V2 sessions mean legacy, never the current method.
    method_version: str = "legacy"
    raw_metrics: dict[str, float] = Field(default_factory=dict)
    runs: int
    mean_delay_min: float
    p90_delay_min: float
    cvar_delay_min: float
    missed_connections: int
    fuel_risk_probability: float
    remaining_conflicts: int
    emissions_index: float
    recovery_minute: float
    confidence_low: float
    confidence_high: float
    per_flight: dict[str, dict[str, float]] = Field(default_factory=dict)


class PlanCandidate(StrictModel):
    id: str = Field(default_factory=lambda: new_id("plan"))
    profile: PlanProfile
    title: str
    pareto_optimal: bool = True
    ideal_distance: float = 0.0
    assignments: list[FlightAssignment]
    actions: list[str]
    report: SimulationReport
    score: float
    feasible: bool = True
    constraint_proof: list[str] = Field(default_factory=list)
    explanation: str = ""
    why_not: list[str] = Field(default_factory=list)
    rank: int = 0
    risk_shield: dict[str, Any] = Field(default_factory=dict)
    evidence_basis: dict[str, Any] = Field(default_factory=dict)


class RelaxationOption(StrictModel):
    id: Literal[
        "declare_runway_recovery", "enable_remote_stand",
        "divert_fuel_critical", "remove_delay_cap",
    ]
    label: str
    effect: str


class ConflictCoreItem(StrictModel):
    id: str
    category: str
    label: str
    rule: str
    affected: list[str] = Field(default_factory=list)
    safety_class: Literal["hard_safety", "operator_soft", "operational"]
    relaxable: bool = False
    remedy: str


class InfeasibilityReport(StrictModel):
    reasons: list[str]
    relaxations: list[str]
    options: list[RelaxationOption] = Field(default_factory=list)
    method: str = "heuristic-diagnostic"
    solver_status: str = "unknown"
    core_irreducible: bool = False
    solver_calls: int = 0
    hard_safety_relaxations_allowed: int = 0
    summary: str = ""
    conflict_core: list[ConflictCoreItem] = Field(default_factory=list)


class AgentTrace(StrictModel):
    agent: str
    role: str
    status: Literal["queued", "running", "completed", "fallback", "failed"]
    summary: str
    tool: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    timestamp: datetime = Field(default_factory=utc_now)


class ExecutionEvent(StrictModel):
    sequence: int = 0
    session_id: str
    sim_minute: float
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    prev_hash: str = ""
    event_hash: str = ""


class SessionCreate(StrictModel):
    mode: Mode = Mode.guided
    scenario_id: str = "storm"
    data_mode: DataMode = DataMode.synthetic
    seed: int = Field(default=2026, ge=0)
    sandbox_flights: int = Field(default=12, ge=4, le=20)
    imported_scenario: ScenarioSpec | None = None


class SessionState(StrictModel):
    id: str = Field(default_factory=lambda: new_id("session"))
    mode: Mode
    data_mode: DataMode
    scenario: ScenarioSpec
    phase: Phase = Phase.normal
    intent: OperatorIntent | None = None
    plans: list[PlanCandidate] = Field(default_factory=list)
    selected_plan_id: str | None = None
    traces: list[AgentTrace] = Field(default_factory=list)
    sequence: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    llm_tier: Literal["deep", "fast", "fallback"] = "fallback"
    data_badge: str = "合成数据"
    data_snapshot: dict[str, Any] = Field(default_factory=dict)
    decision_lab: dict[str, Any] = Field(default_factory=dict)
    execution: ExecutionCursor | None = None


class ExecutionAdvanceRequest(StrictModel):
    target_minute: int = Field(ge=0, le=480)
    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=120)


class PlanRequest(StrictModel):
    profiles: list[PlanProfile] = Field(default_factory=lambda: [
        PlanProfile.balanced, PlanProfile.passenger, PlanProfile.resilient, PlanProfile.green
    ])
    deep: bool = False
    replan: bool = False


class EventRequest(StrictModel):
    event: EventSpec
    expected_revision: int | None = Field(default=None, ge=1)
    request_id: str | None = Field(default=None, min_length=1, max_length=120)


class SandboxUpdate(StrictModel):
    weather_severity: int | None = Field(default=None, ge=0, le=5)
    runway_closure_min: int | None = Field(default=None, ge=0, le=120)
    passenger_multiplier: float | None = Field(default=None, ge=.25, le=3)
    fuel_margin_delta: int | None = Field(default=None, ge=-20, le=60)
    priority_flight_id: str | None = None
    weights: ObjectiveWeights | None = None


class IntentRequest(StrictModel):
    text: str = Field(min_length=1, max_length=1000)
    source: Literal["text", "voice"] = "text"


class IntentClarificationRequest(StrictModel):
    expected_version: int = Field(ge=0)
    issue_index: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=1000)


class IntentConfirmation(StrictModel):
    approved: bool = True


class RelaxationRequest(StrictModel):
    option_id: str


class ObservationRequest(StrictModel):
    query_id: Literal["weather_reopen_nowcast", "critical_gate_readiness"]
    source_plan_id: str | None = Field(default=None, min_length=1, max_length=80)


class PreferenceRequest(StrictModel):
    left_plan_id: str = Field(min_length=1, max_length=80)
    right_plan_id: str = Field(min_length=1, max_length=80)
    preferred_plan_id: str = Field(min_length=1, max_length=80)


class ApprovalResponse(StrictModel):
    session: SessionState
    execution_events: list[ExecutionEvent]


class ExecutionAdvanceResponse(StrictModel):
    session: SessionState
    execution_events: list[ExecutionEvent]
    next_event_minute: int | None = None
    stopped_reason: str | None = None
    revision: int


class HealthStatus(StrictModel):
    status: Literal["ok", "degraded"]
    service: str = "stormshift-v2"
    components: dict[str, str]
