"""Second-stage passenger intervention portfolio optimization.

Aircraft assignments are frozen. CP-SAT selects scarce ground interventions,
then paired SimPy worlds validate whether their synthetic connection-window
effects meet the requested passenger-service target.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from ortools.sat.python import cp_model

from .risk_shield import assess_conformal_risk
from .schemas import OperatorIntent, PlanCandidate, ScenarioSpec
from .simulation import simulate_candidate


ACTION_LIBRARY = (
    {
        "kind": "connection_assist",
        "label": "转机协助小组",
        "label_en": "Connection assist team",
        "bonus_min": 6,
        "cost": 2,
        "resources": {"connection_team": 1},
    },
    {
        "kind": "airside_shuttle",
        "label": "空侧快速摆渡",
        "label_en": "Airside express shuttle",
        "bonus_min": 10,
        "cost": 4,
        "resources": {"connection_team": 1, "airside_shuttle": 1},
    },
    {
        "kind": "protected_hold",
        "label": "下游航班短时保班",
        "label_en": "Protected downstream hold",
        "bonus_min": 14,
        "cost": 7,
        "resources": {"hold_authorization": 1},
    },
)
RESOURCE_BUDGETS = {
    "connection_team": 3,
    "airside_shuttle": 2,
    "hold_authorization": 1,
}
RESOURCE_LABELS = {
    "connection_team": ("转机协助组", "Assist teams"),
    "airside_shuttle": ("空侧摆渡车", "Airside shuttles"),
    "hold_authorization": ("短时保班许可", "Hold approvals"),
}
DEFAULT_CONNECTION_RATE = .95
DEFAULT_TAIL_EXPOSURE_RATE = .20


def _assignment_fingerprint(plan: PlanCandidate) -> str:
    canonical = "|".join(
        f"{item.flight_id}:{item.runway}:{item.landing_minute}:"
        f"{item.gate}:{item.hold_minutes}:{item.taxi_minutes}:{int(item.remote_stand)}"
        for item in sorted(plan.assignments, key=lambda row: row.flight_id)
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _passenger_scope(scenario: ScenarioSpec, plan: PlanCandidate) -> int:
    flights = {item.id: item for item in scenario.flights}
    return sum(
        max(0, flights[item.flight_id].connections)
        for item in plan.assignments
        if item.flight_id in flights
    )


def _targets(
    scope: int,
    intent: OperatorIntent | None,
) -> dict[str, Any]:
    confirmed = intent is not None and not intent.needs_confirmation
    rate = (
        float(intent.minimum_connection_rate)
        if confirmed and intent.minimum_connection_rate is not None
        else DEFAULT_CONNECTION_RATE
    )
    tail_rate = (
        float(intent.maximum_tail_exposure_rate)
        if confirmed and intent.maximum_tail_exposure_rate is not None
        else DEFAULT_TAIL_EXPOSURE_RATE
    )
    rate_missed_limit = max(0, int(math.floor(scope * (1.0 - rate) + 1e-9)))
    explicit_missed = (
        int(intent.maximum_missed_connections)
        if confirmed and intent.maximum_missed_connections is not None
        else None
    )
    missed_limit = (
        min(rate_missed_limit, explicit_missed)
        if explicit_missed is not None
        else rate_missed_limit
    )
    return {
        "source": "operator" if confirmed and any(
            value is not None for value in (
                intent.minimum_connection_rate,
                intent.maximum_missed_connections,
                intent.maximum_tail_exposure_rate,
            )
        ) else "proactive_guardrail",
        "minimum_on_time_rate": round(rate, 4),
        "maximum_missed_connections": missed_limit,
        "maximum_tail_exposure_rate": round(tail_rate, 4),
        "maximum_tail_exposed": max(0, int(math.floor(scope * tail_rate + 1e-9))),
    }


def _tail_exposure(
    rows: list[dict[str, Any]],
    bonuses: dict[str, float] | None = None,
) -> int:
    active = bonuses or {}
    return sum(
        int(row.get("transfer_passengers", 0) or 0)
        for row in rows
        if int(row.get("transfer_passengers", 0) or 0) > 0
        and float(row.get("upper_gate_delay_min", 0) or 0)
        > float(row.get("transfer_buffer_min", 0) or 0)
        + max(0.0, float(active.get(str(row.get("flight_id")), 0.0)))
    )


def _metric_row(
    scope: int,
    missed: int,
    tail_exposed: int,
) -> dict[str, Any]:
    bounded_missed = min(scope, max(0, int(missed)))
    bounded_tail = min(scope, max(0, int(tail_exposed)))
    on_time = max(0, scope - bounded_missed)
    return {
        "passenger_scope": scope,
        "predicted_on_time": on_time,
        "predicted_missed": bounded_missed,
        "predicted_on_time_rate": round(on_time / scope, 4) if scope else 1.0,
        "tail_risk_exposed": bounded_tail,
        "tail_risk_exposure_rate": round(bounded_tail / scope, 4) if scope else 0.0,
    }


def apply_intervention_outcome(
    plan: PlanCandidate,
    analysis: dict[str, Any],
) -> PlanCandidate:
    """Project a validated passenger intervention onto a copy of the plan."""
    effective = plan.model_copy(deep=True)
    exact = analysis.get("simulation_evidence", {})
    if exact.get("method_version") == effective.report.method_version and exact.get("raw_metrics"):
        effective.report.raw_metrics = dict(exact["raw_metrics"])
        effective.report.runs = int(exact["runs"])
    elif analysis.get("mitigated"):
        # Old intervention records do not contain exact validated expectations.
        effective.report.raw_metrics = {}
        effective.report.method_version = "legacy"
    effective.report.missed_connections = int(
        analysis.get("mitigated", {}).get(
            "predicted_missed",
            effective.report.missed_connections,
        )
    )
    residual = analysis.get("residual_expected_missed_by_flight", {})
    for flight_id, value in residual.items():
        if flight_id in effective.report.per_flight:
            effective.report.per_flight[flight_id]["expected_missed_passengers"] = (
                float(value)
            )

    bonuses = analysis.get("applied_connection_buffer_bonus", {})
    shield = dict(effective.risk_shield or {})
    adjusted_rows: list[dict[str, Any]] = []
    for original in shield.get("rows", []):
        row = dict(original)
        flight_id = str(row.get("flight_id", ""))
        bonus = max(0.0, float(bonuses.get(flight_id, 0.0)))
        if bonus:
            row["intervention_bonus_min"] = bonus
            row["effective_transfer_buffer_min"] = round(
                float(row.get("transfer_buffer_min", 0) or 0) + bonus,
                1,
            )
            row["transfer_exposed"] = bool(
                float(row.get("upper_gate_delay_min", 0) or 0)
                > float(row["effective_transfer_buffer_min"])
            )
        adjusted_rows.append(row)
    shield["rows"] = adjusted_rows
    shield["connection_passengers_exposed"] = int(
        analysis.get("mitigated", {}).get(
            "tail_risk_exposed",
            shield.get("connection_passengers_exposed", 0),
        )
    )
    shield["second_stage_intervention_applied"] = bool(
        analysis.get("selected_actions")
    )
    effective.risk_shield = shield
    return effective


def optimize_intervention_portfolio(
    scenario: ScenarioSpec,
    plan: PlanCandidate,
    intent: OperatorIntent | None = None,
    runs: int | None = None,
) -> dict[str, Any]:
    """Select and validate a minimal passenger-service intervention portfolio."""
    validation_runs = max(10, int(runs or plan.report.runs or 50))
    screening_runs = min(24, validation_runs)
    frozen_hash = _assignment_fingerprint(plan)
    scope = _passenger_scope(scenario, plan)
    targets = _targets(scope, intent)

    baseline_report = simulate_candidate(scenario, plan, validation_runs)
    baseline_plan = plan.model_copy(deep=True)
    baseline_plan.report = baseline_report
    baseline_shield = assess_conformal_risk(scenario, baseline_plan)
    shield_rows = list(baseline_shield.get("rows", []))
    baseline_tail = _tail_exposure(shield_rows)
    baseline = _metric_row(
        scope,
        baseline_report.missed_connections,
        baseline_tail,
    )

    already_met = (
        baseline["predicted_missed"] <= targets["maximum_missed_connections"]
        and baseline["tail_risk_exposed"] <= targets["maximum_tail_exposed"]
    )
    flights = {item.id: item for item in scenario.flights}
    ranked_flights = sorted(
        (
            item for item in plan.assignments
            if item.flight_id in flights and flights[item.flight_id].connections > 0
        ),
        key=lambda item: (
            -float(baseline_report.per_flight.get(item.flight_id, {}).get(
                "expected_missed_passengers", 0
            )),
            -int(flights[item.flight_id].connections),
            item.flight_id,
        ),
    )[:4]

    candidate_actions: list[dict[str, Any]] = []
    if not already_met:
        screening_baseline = simulate_candidate(scenario, plan, screening_runs)
        for assignment in ranked_flights:
            flight = flights[assignment.flight_id]
            row = next(
                (
                    value for value in shield_rows
                    if value.get("flight_id") == assignment.flight_id
                ),
                {},
            )
            for template in ACTION_LIBRARY:
                bonus = int(template["bonus_min"])
                action_report = simulate_candidate(
                    scenario,
                    plan,
                    screening_runs,
                    {assignment.flight_id: bonus},
                )
                saved = max(
                    0,
                    int(screening_baseline.missed_connections)
                    - int(action_report.missed_connections),
                )
                tail_saved = (
                    int(flight.connections)
                    if bool(row.get("transfer_exposed"))
                    and float(row.get("upper_gate_delay_min", 0) or 0)
                    <= float(row.get("transfer_buffer_min", 0) or 0) + bonus
                    else 0
                )
                if saved <= 0 and tail_saved <= 0:
                    continue
                candidate_actions.append({
                    "id": f"{template['kind']}:{assignment.flight_id}",
                    "kind": template["kind"],
                    "flight_id": assignment.flight_id,
                    "label": template["label"],
                    "label_en": template["label_en"],
                    "bonus_min": bonus,
                    "estimated_saved_passengers": saved,
                    "estimated_tail_removed": tail_saved,
                    "cost_points": int(template["cost"]),
                    "resources": dict(template["resources"]),
                })

    model = cp_model.CpModel()
    variables = {
        action["id"]: model.new_bool_var(action["id"].replace(":", "_"))
        for action in candidate_actions
    }
    for assignment in ranked_flights:
        selected_for_flight = [
            variables[action["id"]]
            for action in candidate_actions
            if action["flight_id"] == assignment.flight_id
        ]
        if selected_for_flight:
            model.add(sum(selected_for_flight) <= 1)
    for resource, budget in RESOURCE_BUDGETS.items():
        model.add(
            sum(
                int(action["resources"].get(resource, 0)) * variables[action["id"]]
                for action in candidate_actions
            )
            <= budget
        )

    missed_shortfall = model.new_int_var(0, max(1, scope), "missed_shortfall")
    tail_shortfall = model.new_int_var(0, max(1, scope), "tail_shortfall")
    model.add(
        missed_shortfall
        >= int(baseline["predicted_missed"])
        - sum(
            int(action["estimated_saved_passengers"]) * variables[action["id"]]
            for action in candidate_actions
        )
        - int(targets["maximum_missed_connections"])
    )
    model.add(
        tail_shortfall
        >= int(baseline["tail_risk_exposed"])
        - sum(
            int(action["estimated_tail_removed"]) * variables[action["id"]]
            for action in candidate_actions
        )
        - int(targets["maximum_tail_exposed"])
    )
    cost = sum(
        int(action["cost_points"]) * variables[action["id"]]
        for action in candidate_actions
    )
    action_count = sum(variables.values())
    model.minimize(
        1_000_000 * (missed_shortfall + tail_shortfall)
        + 100 * cost
        + action_count
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 1.0
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = int(scenario.seed % 2_147_483_647)
    solver_status = solver.solve(model)
    selected = (
        [
            action for action in candidate_actions
            if solver.boolean_value(variables[action["id"]])
        ]
        if solver_status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        else []
    )
    bonuses = {
        action["flight_id"]: float(action["bonus_min"])
        for action in selected
    }
    mitigated_report = (
        simulate_candidate(
            scenario,
            plan,
            validation_runs,
            connection_buffer_bonus=bonuses,
        )
        if selected
        else baseline_report
    )
    mitigated_tail = _tail_exposure(shield_rows, bonuses)
    mitigated = _metric_row(
        scope,
        mitigated_report.missed_connections,
        mitigated_tail,
    )
    met = (
        mitigated["predicted_missed"] <= targets["maximum_missed_connections"]
        and mitigated["tail_risk_exposed"] <= targets["maximum_tail_exposed"]
    )
    status = "not_needed" if already_met else "target_met" if met else "partial"

    used_resources = {
        resource: sum(
            int(action["resources"].get(resource, 0))
            for action in selected
        )
        for resource in RESOURCE_BUDGETS
    }
    resources = [
        {
            "id": resource,
            "label": RESOURCE_LABELS[resource][0],
            "label_en": RESOURCE_LABELS[resource][1],
            "used": used_resources[resource],
            "budget": budget,
        }
        for resource, budget in RESOURCE_BUDGETS.items()
    ]
    invariant_fields = (
        "mean_delay_min",
        "p90_delay_min",
        "cvar_delay_min",
        "fuel_risk_probability",
        "remaining_conflicts",
        "emissions_index",
        "recovery_minute",
    )
    operational_invariance = all(
        getattr(baseline_report, field) == getattr(mitigated_report, field)
        for field in invariant_fields
    )
    if status == "not_needed":
        summary = "当前方案已满足主动旅客保护线，无需额外占用地面资源。"
        summary_en = "The plan already meets the proactive passenger guardrail; no extra resource is needed."
    elif status == "target_met":
        summary = (
            f"CP-SAT 选择 {len(selected)} 项最小干预；成对仿真确认预计超时"
            f"由 {baseline['predicted_missed']} 降至 {mitigated['predicted_missed']} 人，"
            f"尾部观察由 {baseline['tail_risk_exposed']} 降至 {mitigated['tail_risk_exposed']} 人。"
        )
        summary_en = (
            f"CP-SAT selected {len(selected)} interventions; paired simulation reduced "
            f"expected misses from {baseline['predicted_missed']} to {mitigated['predicted_missed']}."
        )
    else:
        summary = (
            f"现有资源只能部分缓解风险：预计超时由 {baseline['predicted_missed']} "
            f"降至 {mitigated['predicted_missed']} 人；剩余缺口已明确保留，未伪造达标。"
        )
        summary_en = (
            "Available resources reduce risk only partially; the residual target gap remains explicit."
        )

    return {
        "method": "lexicographic CP-SAT resource portfolio + paired-seed SimPy validation",
        "status": status,
        "source_plan_id": plan.id,
        "scope": {
            "flights_considered": len(ranked_flights),
            "passengers": scope,
            "aircraft_assignments_frozen": True,
        },
        "targets": targets,
        "baseline": baseline,
        "mitigated": mitigated,
        "simulation_evidence": {
            "method_version": mitigated_report.method_version,
            "raw_metrics": dict(mitigated_report.raw_metrics),
            "runs": mitigated_report.runs,
        },
        "deltas": {
            "predicted_missed_saved": (
                baseline["predicted_missed"] - mitigated["predicted_missed"]
            ),
            "tail_exposure_reduced": (
                baseline["tail_risk_exposed"] - mitigated["tail_risk_exposed"]
            ),
            "on_time_rate_gain_points": round(
                100 * (
                    mitigated["predicted_on_time_rate"]
                    - baseline["predicted_on_time_rate"]
                ),
                1,
            ),
        },
        "selected_actions": selected,
        "candidate_action_count": len(candidate_actions),
        "included_in_candidate": True,
        "applied_connection_buffer_bonus": bonuses,
        "residual_expected_missed_by_flight": {
            flight_id: float(values.get("expected_missed_passengers", 0.0))
            for flight_id, values in mitigated_report.per_flight.items()
        },
        "resources": resources,
        "proof": {
            "solver_status": solver.status_name(solver_status),
            "validation_runs": validation_runs,
            "screening_runs": screening_runs,
            "paired_common_random_numbers": True,
            "assignment_fingerprint_before": frozen_hash,
            "assignment_fingerprint_after": _assignment_fingerprint(plan),
            "assignments_unchanged": frozen_hash == _assignment_fingerprint(plan),
            "operational_metrics_unchanged": operational_invariance,
            "safety_constraints_unchanged": True,
        },
        "summary": summary,
        "summary_en": summary_en,
        "caveat": (
            "干预收益与资源成本是合成演练参数；真实部署需接入 AODB/PSS、"
            "地服容量和下游航班授权。该层不生成或修改飞机安全调度。"
        ),
    }
