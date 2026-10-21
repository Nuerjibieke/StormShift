"""Bounded counterexample search for plan robustness margins.

The module deliberately keeps language models out of executable perturbations:
all witnesses are produced by deterministic edits, re-checked by the hard
constraint validator, and the nearest witness is handed to CP-SAT for repair.
"""

from __future__ import annotations

from typing import Any, Callable

from .optimizer import solve_candidate, validate_assignments
from .schemas import (
    OperatorIntent,
    PlanCandidate,
    ResourceStatus,
    ScenarioSpec,
)


Mutation = Callable[[ScenarioSpec, str, int], None]
Description = Callable[[ScenarioSpec, str, int], tuple[str, str]]


def _failures(
    scenario: ScenarioSpec,
    plan: PlanCandidate,
    intent: OperatorIntent | None,
) -> list[str]:
    return [
        item
        for item in validate_assignments(scenario, plan.assignments, intent)
        if item.startswith("FAIL")
    ]


def _changed_flights(
    before: PlanCandidate,
    after: PlanCandidate,
) -> list[str]:
    before_by_id = {
        item.flight_id: (
            item.runway,
            item.gate,
            item.landing_minute,
            item.hold_minutes,
            item.taxi_minutes,
            item.remote_stand,
        )
        for item in before.assignments
    }
    after_by_id = {
        item.flight_id: (
            item.runway,
            item.gate,
            item.landing_minute,
            item.hold_minutes,
            item.taxi_minutes,
            item.remote_stand,
        )
        for item in after.assignments
    }
    return sorted(
        flight_id
        for flight_id in before_by_id.keys() | after_by_id.keys()
        if before_by_id.get(flight_id) != after_by_id.get(flight_id)
    )


def _first_failed_delta(probe: Callable[[int], bool], limit: int, *, monotone: bool) -> int | None:
    """Find the first failing integer; bisection requires a proven monotone axis.

    The fallback is exhaustive for arbitrary/non-monotone mutations. Probes are
    cached by the caller, so endpoint and predecessor checks do not repeat work.
    """
    if limit < 1:
        return None
    if not monotone:
        return next((delta for delta in range(1, limit + 1) if probe(delta)), None)
    if probe(1):
        return 1
    lo, hi = 1, min(2, limit)
    while hi > lo and not probe(hi):
        lo = hi
        hi = min(limit, hi * 2)
    if hi == lo:
        return None
    # lo passes and hi fails. Hard safety is checked by the original validator.
    while hi - lo > 1:
        middle = (lo + hi) // 2
        if probe(middle):
            hi = middle
        else:
            lo = middle
    assert not probe(hi - 1)
    return hi


def _search_axis(
    scenario: ScenarioSpec,
    plan: PlanCandidate,
    intent: OperatorIntent | None,
    *,
    axis_id: str,
    label: str,
    label_en: str,
    targets: list[str],
    maximum_delta: int,
    target_limit: Callable[[ScenarioSpec, str], int],
    mutate: Mutation,
    describe: Description,
    monotone: bool = False,
) -> tuple[dict[str, Any], ScenarioSpec | None]:
    evaluations = 0
    target_witnesses: list[tuple[int, str, ScenarioSpec, list[str], str, str]] = []

    searched_limits = {}
    for target_id in sorted(set(targets)):
        limit = min(maximum_delta, max(0, target_limit(scenario, target_id)))
        searched_limits[target_id] = limit
        probes = {}

        def probe(delta: int) -> bool:
            nonlocal evaluations
            if delta not in probes:
                stressed = scenario.model_copy(deep=True)
                mutate(stressed, target_id, delta)
                probes[delta] = (stressed, _failures(stressed, plan, intent))
                evaluations += 1
            return bool(probes[delta][1])

        delta = _first_failed_delta(probe, limit, monotone=monotone)
        if delta is not None:
            stressed, failures = probes[delta]
            description, description_en = describe(scenario, target_id, delta)
            target_witnesses.append((
                delta, target_id, stressed, failures, description, description_en,
            ))

    if not target_witnesses:
        return ({
            "id": axis_id,
            "label": label,
            "label_en": label_en,
            "status": "lower_bound",
            "target_id": None,
            "delta": None,
            "unit": "min",
            "distance_ratio": None,
            "description": "在各资源实际可搜索范围内未发现硬约束反例；请查看逐资源上限",
            "description_en": (
                "No hard-constraint counterexample within each resource-specific search limit"
            ),
            "failures": [],
            "minimality": {
                "baseline_passed": True,
                "predecessor_delta": min(searched_limits.values(), default=0),
                "predecessor_passed": True,
                "witness_failed": False,
            },
            "searched_targets": len(set(targets)),
            "evaluations": evaluations,
            "search_limit": maximum_delta,
            "target_search_limits": searched_limits,
            "repair": {
                "status": "not_required",
                "available": None,
                "changed_flights": [],
                "changed_count": 0,
                "hard_constraints_passed": None,
                "proof": [],
            },
        }, None)

    delta, target_id, stressed, failures, description, description_en = min(
        target_witnesses,
        key=lambda item: (item[0], item[1]),
    )
    return ({
        "id": axis_id,
        "label": label,
        "label_en": label_en,
        "status": "witness",
        "target_id": target_id,
        "delta": delta,
        "unit": "min",
        "distance_ratio": round(delta / maximum_delta, 4),
        "description": description,
        "description_en": description_en,
        "failures": failures[:4],
        "minimality": {
            "baseline_passed": True,
            "predecessor_delta": delta - 1,
            "predecessor_passed": True,
            "witness_failed": True,
        },
        "searched_targets": len(set(targets)),
        "evaluations": evaluations,
        "search_limit": maximum_delta,
        "target_search_limits": searched_limits,
        "repair": {
            "status": "not_evaluated",
            "available": None,
            "changed_flights": [],
            "changed_count": 0,
            "hard_constraints_passed": None,
            "proof": [],
        },
    }, stressed)


def discover_minimal_counterexamples(
    scenario: ScenarioSpec,
    plan: PlanCandidate,
    intent: OperatorIntent | None = None,
    *,
    search_strategy: str = "monotone",
) -> dict[str, Any]:
    """Find the closest bounded hard-constraint witness and attempt safe repair."""
    if search_strategy not in {"monotone", "linear"}:
        raise ValueError("unknown counterexample search strategy")
    if not plan.assignments:
        return {
            "method": "bounded counterexample search + CP-SAT repair",
            "status": "not_applicable",
            "plan_id": plan.id,
            "categories_tested": 0,
            "witness_count": 0,
            "evaluations": 0,
            "nearest_witness_id": None,
            "margin_tier": "not_applicable",
            "summary": "当前没有待执行航班，因此无需搜索计划击穿点",
            "summary_en": "No pending flights exist, so no plan breakpoint is applicable.",
            "axes": [],
            "caveat": "有限离散搜索不是对搜索范围外扰动的全局鲁棒性证明。",
        }

    baseline_failures = _failures(scenario, plan, intent)
    if baseline_failures:
        return {
            "method": "bounded counterexample search + CP-SAT repair",
            "status": "baseline_invalid",
            "plan_id": plan.id,
            "categories_tested": 0,
            "witness_count": 0,
            "evaluations": 0,
            "nearest_witness_id": None,
            "margin_tier": "invalid",
            "summary": "基准方案已违反硬约束，不能计算有效安全余量",
            "summary_en": "The baseline plan already violates hard constraints.",
            "baseline_failures": baseline_failures,
            "axes": [],
            "caveat": "必须先修复基准方案，再进行反例搜索。",
        }

    assignment_runways = sorted({item.runway for item in plan.assignments})
    assignment_gates = sorted({item.gate for item in plan.assignments})
    assignment_flights = sorted({item.flight_id for item in plan.assignments})

    def runway_limit(base: ScenarioSpec, target_id: str) -> int:
        runway = next(item for item in base.runways if item.id == target_id)
        start = (
            int(runway.reopen_minute or base.current_minute)
            if runway.status == ResourceStatus.closed
            else base.current_minute
        )
        return min(45, 240 - start)

    def runway_mutation(stressed: ScenarioSpec, target_id: str, delta: int) -> None:
        runway = next(item for item in stressed.runways if item.id == target_id)
        if runway.status == ResourceStatus.closed:
            start = int(runway.reopen_minute or stressed.current_minute)
        else:
            start = stressed.current_minute
        runway.status = ResourceStatus.closed
        runway.reopen_minute = start + delta
        runway.reason = "最小反例搜索"

    def runway_description(
        base: ScenarioSpec,
        target_id: str,
        delta: int,
    ) -> tuple[str, str]:
        runway = next(item for item in base.runways if item.id == target_id)
        if runway.status == ResourceStatus.closed:
            return (
                f"{target_id} 比预测晚 {delta} 分钟开放时，原计划首次违反硬约束",
                f"The fixed plan first breaks if {target_id} reopens {delta} minutes late",
            )
        return (
            f"{target_id} 从现在起突发关闭 {delta} 分钟时，原计划首次违反硬约束",
            f"The fixed plan first breaks if {target_id} closes now for {delta} minutes",
        )

    def gate_limit(base: ScenarioSpec, target_id: str) -> int:
        gate = next(item for item in base.gates if item.id == target_id)
        start = max(base.current_minute, gate.available_minute)
        return min(45, 240 - start)

    def gate_mutation(stressed: ScenarioSpec, target_id: str, delta: int) -> None:
        gate = next(item for item in stressed.gates if item.id == target_id)
        gate.status = ResourceStatus.blocked
        gate.available_minute = max(
            stressed.current_minute,
            gate.available_minute,
        ) + delta

    def gate_description(
        _base: ScenarioSpec,
        target_id: str,
        delta: int,
    ) -> tuple[str, str]:
        return (
            f"{target_id} 不可用窗口再延长 {delta} 分钟时，原计划首次违反硬约束",
            f"The fixed plan first breaks if {target_id} stays unavailable {delta} minutes longer",
        )

    def separation_limit(base: ScenarioSpec, target_id: str) -> int:
        runway = next(item for item in base.runways if item.id == target_id)
        return min(4, 10 - runway.separation_min)

    def separation_mutation(
        stressed: ScenarioSpec,
        target_id: str,
        delta: int,
    ) -> None:
        runway = next(item for item in stressed.runways if item.id == target_id)
        runway.separation_min += delta

    def separation_description(
        _base: ScenarioSpec,
        target_id: str,
        delta: int,
    ) -> tuple[str, str]:
        return (
            f"{target_id} 最小落地间隔增加 {delta} 分钟时，原计划首次违反硬约束",
            f"The fixed plan first breaks if {target_id} separation grows by {delta} minutes",
        )

    def fuel_limit(base: ScenarioSpec, target_id: str) -> int:
        flight = next(item for item in base.flights if item.id == target_id)
        return min(30, flight.fuel_margin_min)

    def fuel_mutation(stressed: ScenarioSpec, target_id: str, delta: int) -> None:
        flight = next(item for item in stressed.flights if item.id == target_id)
        flight.fuel_margin_min = max(0, flight.fuel_margin_min - delta)

    def fuel_description(
        _base: ScenarioSpec,
        target_id: str,
        delta: int,
    ) -> tuple[str, str]:
        return (
            f"{target_id} 可用燃油时间下修 {delta} 分钟时，原计划首次违反硬约束",
            f"The fixed plan first breaks if {target_id} fuel time is revised down by {delta} minutes",
        )

    specs = [
        {
            "axis_id": "runway_closure",
            "label": "跑道关闭延长",
            "label_en": "Runway closure",
            "targets": assignment_runways,
            "maximum_delta": 45,
            "target_limit": runway_limit,
            "mutate": runway_mutation,
            "describe": runway_description,
        },
        {
            "axis_id": "gate_outage",
            "label": "机位不可用延长",
            "label_en": "Gate outage",
            "targets": assignment_gates,
            "maximum_delta": 45,
            "target_limit": gate_limit,
            "mutate": gate_mutation,
            "describe": gate_description,
        },
        {
            "axis_id": "separation_increase",
            "label": "湿跑道间隔增加",
            "label_en": "Runway separation",
            "targets": assignment_runways,
            "maximum_delta": 4,
            "target_limit": separation_limit,
            "mutate": separation_mutation,
            "describe": separation_description,
        },
        {
            "axis_id": "fuel_margin_loss",
            "label": "燃油余量下修",
            "label_en": "Fuel reserve revision",
            "targets": assignment_flights,
            "maximum_delta": 30,
            "target_limit": fuel_limit,
            "mutate": fuel_mutation,
            "describe": fuel_description,
        },
    ]

    axes: list[dict[str, Any]] = []
    witness_scenarios: dict[str, ScenarioSpec] = {}
    for spec in specs:
        axis, witness_scenario = _search_axis(
            scenario,
            plan,
            intent,
            **spec,
            # These four mutations only tighten fixed-plan constraints: later
            # readiness, longer runway occupancy, or earlier fuel deadlines.
            # New axes must not opt in without the same monotonicity argument.
            monotone=search_strategy == "monotone",
        )
        axes.append(axis)
        if witness_scenario is not None:
            witness_scenarios[str(axis["id"])] = witness_scenario

    witnessed = [axis for axis in axes if axis["status"] == "witness"]
    nearest = min(
        witnessed,
        key=lambda axis: (
            float(axis["distance_ratio"]),
            int(axis["delta"]),
            str(axis["id"]),
        ),
        default=None,
    )

    if nearest is not None:
        stressed = witness_scenarios[str(nearest["id"])]
        repaired, failure = solve_candidate(
            stressed,
            plan.profile,
            intent,
        )
        if repaired is not None:
            repair_proof = validate_assignments(
                stressed,
                repaired.assignments,
                intent,
            )
            repair_passed = not any(
                item.startswith("FAIL") for item in repair_proof
            )
            changed = _changed_flights(plan, repaired)
            nearest["repair"] = {
                "status": "safe_repair" if repair_passed else "unsafe_repair",
                "available": repair_passed,
                "plan_id": repaired.id,
                "changed_flights": changed,
                "changed_count": len(changed),
                "hard_constraints_passed": repair_passed,
                "proof": repair_proof,
            }
        else:
            nearest["repair"] = {
                "status": "manual_required",
                "available": False,
                "plan_id": None,
                "changed_flights": [],
                "changed_count": 0,
                "hard_constraints_passed": False,
                "proof": (
                    [failure.summary]
                    if failure is not None
                    else ["压力反例下没有可行 CP-SAT 修复"]
                ),
            }

    nearest_ratio = (
        float(nearest["distance_ratio"])
        if nearest is not None
        else 1.0
    )
    margin_tier = (
        "thin"
        if nearest_ratio <= .10
        else "watch"
        if nearest_ratio <= .25
        else "buffered"
    )
    evaluations = sum(int(axis["evaluations"]) for axis in axes)
    witness_count = len(witnessed)

    if nearest is None:
        summary = (
            f"在 {evaluations} 个有界扰动点中没有发现硬约束反例；"
            "安全余量至少覆盖当前搜索范围"
        )
        summary_en = (
            f"No hard-constraint witness was found across {evaluations} bounded probes."
        )
    else:
        repair = nearest["repair"]
        repair_zh = (
            f"；CP-SAT 可通过改动 {repair['changed_count']} 架航班安全修复"
            if repair["available"]
            else "；该反例需要人工处置"
        )
        repair_en = (
            f"; CP-SAT repairs it by changing {repair['changed_count']} flights"
            if repair["available"]
            else "; this witness requires human handling"
        )
        summary = (
            f"自动检查 {evaluations} 个扰动点；最近击穿点："
            f"{nearest['description']}{repair_zh}"
        )
        summary_en = (
            f"Searched {evaluations} perturbations; nearest breakpoint: "
            f"{nearest['description_en']}{repair_en}."
        )

    return {
        "method": (
            "deterministic bounded counterexample search + "
            "hard-constraint oracle + CP-SAT repair"
        ),
        "status": "completed",
        "plan_id": plan.id,
        "categories_tested": len(axes),
        "witness_count": witness_count,
        "evaluations": evaluations,
        "nearest_witness_id": nearest["id"] if nearest is not None else None,
        "margin_tier": margin_tier,
        "summary": summary,
        "summary_en": summary_en,
        "axes": axes,
        "caveat": (
            "这是四类整数扰动上的有界反例搜索；它证明最小性只限于已声明的"
            "搜索步长与范围，不代表范围外或未建模世界的全局鲁棒性。"
        ),
    }
