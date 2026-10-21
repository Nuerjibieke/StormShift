"""Controlled baselines for explaining where the evidence layer adds value.

This is intentionally not a benchmark against commercial airport products.  It
holds the optimizer family constant and injects information-quality faults to
separate scheduling quality from input trust and execution governance.
"""

from __future__ import annotations

import hashlib
import json
from statistics import mean
from typing import Any

from .optimizer import solve_candidate, validate_assignments
from .schemas import (
    DataMode,
    FlightState,
    OperatorIntent,
    PlanCandidate,
    ResourceStatus,
    ScenarioSpec,
)
from .simulation import SIMULATION_METHOD_VERSION, fcfs_baseline, report_metric, simulate_candidate
from .trusted_state import (
    apply_trusted_operational_state,
    build_trusted_operational_state,
)


SOLVER_BUDGET_SECONDS = .45


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _action_digest(plan: PlanCandidate | None) -> str | None:
    if plan is None:
        return None
    return _digest(sorted((item.model_dump(mode="json") for item in plan.assignments),
                          key=lambda item: item["flight_id"]))


def _input_digest(scenario: ScenarioSpec, intent: OperatorIntent | None) -> str:
    return _digest({"scenario": scenario.model_dump(mode="json"),
                    "intent": intent.model_dump(mode="json") if intent else None})


ACTIVE_STATES = {
    FlightState.inbound,
    FlightState.approach,
    FlightState.holding,
}


def _assignment_signature(plan: PlanCandidate | None) -> dict[str, tuple[Any, ...]]:
    if plan is None:
        return {}
    return {
        item.flight_id: (
            item.runway,
            item.gate,
            item.landing_minute,
            item.hold_minutes,
        )
        for item in plan.assignments
    }


def _changed_flights(
    nominal: PlanCandidate | None,
    candidate: PlanCandidate | None,
) -> int:
    before = _assignment_signature(nominal)
    after = _assignment_signature(candidate)
    return sum(
        before.get(flight_id) != after.get(flight_id)
        for flight_id in set(before) | set(after)
    )


def _evaluate_plan(
    truth: ScenarioSpec,
    plan: PlanCandidate | None,
    nominal: PlanCandidate | None,
    intent: OperatorIntent | None,
    runs: int,
    *,
    abstention_reason: str | None = None,
) -> dict[str, Any]:
    active_ids = {
        flight.id for flight in truth.flights
        if flight.state in ACTIVE_STATES
    }
    if plan is None:
        return {
            "status": "safe_abstention",
            "hard_constraints_passed": False,
            "unsafe_execution": False,
            "safe_abstention": True,
            "missing_flights": sorted(active_ids),
            "action_coverage": 0.0,
            "changed_flights": 0,
            "violations": [],
            "mean_delay_min": None,
            "p90_delay_min": None,
            "missed_connections": None,
            "remaining_conflicts": None,
            "reason": abstention_reason or "No feasible plan was issued.",
            "action_signature": None,
            "input_digest": _input_digest(truth, intent),
            "effective_sample_count": 0,
            "simulation_method_version": SIMULATION_METHOD_VERSION,
        }

    assignments = {item.flight_id for item in plan.assignments}
    missing = sorted(active_ids - assignments)
    violations = [
        item for item in validate_assignments(
            truth,
            plan.assignments,
            intent,
        )
        if item.startswith("FAIL")
    ]
    if missing:
        violations.append(
            "FAIL 缺少活动航班动作：" + "、".join(missing)
        )
    hard_pass = bool(plan.feasible) and not violations
    report = simulate_candidate(truth, plan, runs=runs) if hard_pass else None
    return {
        "status": "safe" if hard_pass else "unsafe_against_truth",
        "hard_constraints_passed": hard_pass,
        "unsafe_execution": not hard_pass,
        "safe_abstention": False,
        "missing_flights": missing,
        "action_coverage": round(
            len(assignments & active_ids) / max(1, len(active_ids)),
            4,
        ),
        "changed_flights": _changed_flights(nominal, plan),
        "violations": violations[:5],
        "mean_delay_min": report_metric(report, "mean_delay_min") if report else None,
        "p90_delay_min": report_metric(report, "p90_delay_min") if report else None,
        "missed_connections": report_metric(report, "missed_connections") if report else None,
        "remaining_conflicts": report_metric(report, "remaining_conflicts") if report else None,
        "action_signature": _action_digest(plan),
        "input_digest": _input_digest(truth, intent),
        "effective_sample_count": runs if report else 0,
        "simulation_method_version": SIMULATION_METHOD_VERSION,
        "reason": (
            "All executable actions validate against the post-update truth."
            if hard_pass
            else (violations[0] if violations else "Candidate marked infeasible")
        ),
    }


def _fault_worlds(
    scenario: ScenarioSpec,
    solo_plan: PlanCandidate,
) -> list[dict[str, Any]]:
    ordered = sorted(
        solo_plan.assignments,
        key=lambda item: (item.landing_minute, item.flight_id),
    )
    first = ordered[0]
    latest = max(
        ordered,
        key=lambda item: (item.hold_minutes, item.landing_minute),
    )

    gate_truth = scenario.model_copy(deep=True)
    gate = next(item for item in gate_truth.gates if item.id == first.gate)
    stale_gate_minute = gate.available_minute
    gate.available_minute = max(
        gate.available_minute,
        first.landing_minute + first.taxi_minutes + 6,
    )
    gate.status = ResourceStatus.blocked

    runway_truth = scenario.model_copy(deep=True)
    runway = next(
        item for item in runway_truth.runways if item.id == first.runway
    )
    stale_runway_status = runway.status.value
    runway.status = ResourceStatus.closed
    runway.reopen_minute = max(
        int(runway.reopen_minute or 0),
        first.landing_minute + 12,
    )
    runway.reason = "fault-injection inspection hold"

    fuel_truth = scenario.model_copy(deep=True)
    flight = next(
        item for item in fuel_truth.flights if item.id == latest.flight_id
    )
    stale_fuel_margin = flight.fuel_margin_min
    corrected_margin = max(
        flight.scheduled_arrival - fuel_truth.current_minute,
        latest.landing_minute - fuel_truth.current_minute - 1,
    )
    flight.fuel_margin_min = min(stale_fuel_margin, corrected_margin)

    return [
        {
            "id": "late_gate_readiness",
            "label": "机位恢复时间晚到",
            "label_en": "Late gate-readiness update",
            "changed_fact": f"gate.{first.gate}.available_minute",
            "stale_value": stale_gate_minute,
            "truth_value": gate.available_minute,
            "truth": gate_truth,
        },
        {
            "id": "runway_inspection_hold",
            "label": "跑道检查延长",
            "label_en": "Runway inspection extension",
            "changed_fact": f"runway.{first.runway}.status/reopen_minute",
            "stale_value": stale_runway_status,
            "truth_value": f"closed until T+{runway.reopen_minute}",
            "truth": runway_truth,
        },
        {
            "id": "fuel_clock_correction",
            "label": "燃油余量校正",
            "label_en": "Fuel-clock correction",
            "changed_fact": f"flight.{latest.flight_id}.fuel_margin_min",
            "stale_value": stale_fuel_margin,
            "truth_value": flight.fuel_margin_min,
            "truth": fuel_truth,
        },
    ]


def _aggregate(
    approach_id: str,
    label: str,
    label_en: str,
    nominal_result: dict[str, Any],
    trials: list[dict[str, Any]],
    capabilities: dict[str, bool],
) -> dict[str, Any]:
    total = len(trials)
    safe = sum(item["hard_constraints_passed"] for item in trials)
    abstentions = sum(item["safe_abstention"] for item in trials)
    unsafe = sum(item["unsafe_execution"] for item in trials)
    metrics = [
        item for item in trials
        if item["hard_constraints_passed"]
        and not item["safe_abstention"]
        and item["mean_delay_min"] is not None
    ]
    return {
        "id": approach_id,
        "label": label,
        "label_en": label_en,
        "nominal": nominal_result,
        "fault_trials": {
            "worlds": total,
            "hard_passed_worlds": safe,
            "unsafe_execution_worlds": unsafe,
            "safe_abstentions": abstentions,
            "hard_pass_rate": round(safe / max(1, total), 4),
            "safe_decision_rate": round(
                (safe + abstentions) / max(1, total),
                4,
            ),
            "metric_eligible_worlds": len(metrics),
            "mean_changed_flights": round(
                mean(item["changed_flights"] for item in trials),
                2,
            ),
            "mean_delay_min": (
                round(mean(item["mean_delay_min"] for item in metrics), 2)
                if metrics else None
            ),
            "mean_p90_delay_min": (
                round(mean(item["p90_delay_min"] for item in metrics), 2)
                if metrics else None
            ),
            "mean_missed_connections": (
                round(mean(item["missed_connections"] for item in metrics), 2)
                if metrics else None
            ),
        },
        "capabilities": capabilities,
    }


def compare_operational_baselines(
    scenario: ScenarioSpec,
    trusted_state: dict[str, Any],
    recommended: PlanCandidate,
    intent: OperatorIntent | None = None,
    *,
    runs: int = 16,
) -> dict[str, Any]:
    """Compare workflow assumptions under shared, controlled fault worlds."""
    rule_plan = fcfs_baseline(scenario)
    solo_plan, solo_failure = solve_candidate(
        scenario,
        recommended.profile,
        intent,
    )
    if solo_plan is None:
        raise ValueError(
            "Nominal standalone CP-SAT baseline is infeasible: "
            + str(solo_failure.reasons[0] if solo_failure else "unknown")
        )

    nominal_plans = {
        "rules": rule_plan,
        "solo_cp_sat": solo_plan,
        "updated_cp_sat": solo_plan,
        "stormshift": recommended,
    }
    nominal_results = {
        key: _evaluate_plan(scenario, plan, plan, intent, runs)
        for key, plan in nominal_plans.items()
    }
    trial_results: dict[str, list[dict[str, Any]]] = {key: [] for key in nominal_plans}
    world_rows: list[dict[str, Any]] = []
    solver_settings = {
        "profile": recommended.profile.value,
        "timeout_seconds": SOLVER_BUDGET_SECONDS,
        "max_deterministic_time": .12,
        "max_time_in_seconds": max(1.0, SOLVER_BUDGET_SECONDS * 3),
        "num_search_workers": 1,
        "seed_policy": "scenario seed + fixed profile offset",
    }
    for fault in _fault_worlds(scenario, solo_plan):
        truth = fault["truth"]
        reconciled = build_trusted_operational_state(truth, DataMode.synthetic,
                                                     previous=trusted_state)
        # Both dynamic methods receive exactly the same reconciled structural facts.
        # The optimizer-only branch does not depend on the evidence gate being ready.
        solver_truth = apply_trusted_operational_state(truth, reconciled)
        updated_plan, updated_failure = solve_candidate(
            solver_truth.model_copy(deep=True), recommended.profile, intent,
            timeout_seconds=SOLVER_BUDGET_SECONDS)
        updated_reason = (updated_failure.reasons[0] if updated_failure and
                          updated_failure.reasons else "No feasible updated schedule")
        evidence_plan = None
        evidence_reason = "Critical evidence unresolved; safely abstained."
        if reconciled["status"] == "ready":
            evidence_plan, failure = solve_candidate(
                solver_truth.model_copy(deep=True), recommended.profile, intent,
                timeout_seconds=SOLVER_BUDGET_SECONDS)
            evidence_reason = (failure.reasons[0] if failure and failure.reasons
                               else "No feasible verified schedule")
        results = {
            "rules": _evaluate_plan(truth, rule_plan, rule_plan, intent, runs),
            "solo_cp_sat": _evaluate_plan(truth, solo_plan, solo_plan, intent, runs),
            "updated_cp_sat": _evaluate_plan(truth, updated_plan, solo_plan, intent, runs,
                                            abstention_reason=updated_reason),
            "stormshift": _evaluate_plan(truth, evidence_plan, solo_plan, intent, runs,
                                         abstention_reason=evidence_reason),
        }
        for key in ("updated_cp_sat", "stormshift"):
            results[key]["input_digest"] = _input_digest(solver_truth, intent)
            results[key]["solver_settings"] = dict(solver_settings)
        for key in ("rules", "solo_cp_sat"):
            results[key]["input_digest"] = _input_digest(scenario, intent)
            results[key]["solver_settings"] = (None if key == "rules" else dict(solver_settings))
        for key, value in results.items():
            trial_results[key].append(value)
        world_rows.append({key: value for key, value in fault.items() if key != "truth"} | {
            "truth_digest": _input_digest(truth, intent),
            "outcomes": results,
        })

    rows = [
        _aggregate(
            "rules",
            "FCFS · 故障前静态方案（不更新）",
            "FCFS · static pre-fault schedule (no update)",
            nominal_results["rules"],
            trial_results["rules"],
            {
                "constraint_optimization": False,
                "conflict_reconciliation": False,
                "execution_certificate": False,
                "safe_abstention": False,
            },
        ),
        _aggregate(
            "solo_cp_sat",
            "CP-SAT · 故障前静态方案（不重规划）",
            "CP-SAT · static pre-fault schedule (no replan)",
            nominal_results["solo_cp_sat"],
            trial_results["solo_cp_sat"],
            {
                "constraint_optimization": True,
                "conflict_reconciliation": False,
                "execution_certificate": False,
                "safe_abstention": False,
            },
        ),
        _aggregate(
            "updated_cp_sat",
            "CP-SAT · 更新后正常重规划",
            "CP-SAT · updated-input replanning",
            nominal_results["updated_cp_sat"], trial_results["updated_cp_sat"],
            {"constraint_optimization": True, "conflict_reconciliation": False,
             "execution_certificate": False, "safe_abstention": True},
        ),
        _aggregate(
            "stormshift",
            "证据协调 + CP-SAT",
            "Evidence coordination + CP-SAT",
            nominal_results["stormshift"],
            trial_results["stormshift"],
            {
                "constraint_optimization": True,
                "conflict_reconciliation": True,
                "execution_certificate": True,
                "safe_abstention": True,
            },
        ),
    ]

    nominal_gap = (
        round(abs(nominal_results["solo_cp_sat"]["mean_delay_min"]
                  - nominal_results["stormshift"]["mean_delay_min"]), 2)
        if all(nominal_results[key]["mean_delay_min"] is not None
               for key in ("solo_cp_sat", "stormshift")) else None
    )
    dynamic_pairs = [world["outcomes"] for world in world_rows
                     if all(world["outcomes"][key]["mean_delay_min"] is not None
                            for key in ("updated_cp_sat", "stormshift"))]
    dynamic_delta = (mean(row["stormshift"]["mean_delay_min"]
                          - row["updated_cp_sat"]["mean_delay_min"]
                          for row in dynamic_pairs) if dynamic_pairs else None)
    delta_text = f"{dynamic_delta:+.3f}" if dynamic_delta is not None else "N/A"
    return {
        "method": "controlled update-value and equal-input dynamic CP-SAT comparison",
        "simulation_method_version": SIMULATION_METHOD_VERSION,
        "status": "completed",
        "scope": {"fault_worlds": len(world_rows), "simulation_runs_per_plan": runs,
                  "active_flights": sum(f.state in ACTIVE_STATES for f in scenario.flights),
                  "commercial_products_compared": 0},
        "rows": rows,
        "worlds": world_rows,
        "nominal_solver_gap_min": nominal_gap,
        "dynamic_comparison": {
            "pair": ["updated_cp_sat", "stormshift"],
            "eligible_worlds": len(dynamic_pairs),
            "mean_delay_delta_min": dynamic_delta,
            "same_input_and_budget": True,
        },
        "static_comparison": {"pair": ["solo_cp_sat", "updated_cp_sat"],
                              "purpose": "value of receiving updates and replanning"},
        "finding": (
            f"同输入、同预算的动态对照：证据流程减去正常 CP-SAT 的平均延误差为 {delta_text} 分钟"
            f"（{len(dynamic_pairs)} 个可比较世界）；零收益是有效结果。静态对照只说明更新与重规划的价值，"
            "不证明 Agent 的优化质量优于求解器。"),
        "finding_en": (
            f"Equal-input, equal-budget dynamic comparison: evidence workflow minus updated CP-SAT "
            f"mean delay = {delta_text} min across {len(dynamic_pairs)} eligible worlds. Zero gain is valid. "
            "Static comparisons measure update/replan value, not superior agent optimization."),
        "disclaimer": "内部合成受控实验，不是对 AODB/RMS、AMAN/DMAN 或商业机场产品的排名。",
        "disclaimer_en": "Internal synthetic controlled experiment; not a ranking of commercial airport systems.",
    }
