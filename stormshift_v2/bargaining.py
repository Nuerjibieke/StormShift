"""Auditable multi-stakeholder bargaining over solver-generated candidates.

The bargaining layer never schedules aircraft. It maps already-safe CP-SAT
candidates to stakeholder-specific losses, normalizes those losses against the
candidate set, and selects the Nash social-welfare compromise.
"""

from __future__ import annotations

import math
from typing import Any

from .schemas import PlanCandidate


_STAKEHOLDERS = (
    {
        "id": "airport",
        "agent": "Airport Operations Agent",
        "label": "机场运行",
        "label_en": "Airport operations",
        "objective": "恢复吞吐并控制尾部延误",
        "objective_en": "Restore throughput and limit tail delay",
    },
    {
        "id": "passenger",
        "agent": "Airline & Passenger Agent",
        "label": "航司与旅客",
        "label_en": "Airlines and passengers",
        "objective": "减少错失转机和旅客延误",
        "objective_en": "Reduce missed connections and passenger delay",
    },
    {
        "id": "safety",
        "agent": "Safety Critic",
        "label": "运行风险",
        "label_en": "Operational risk",
        "objective": "保护燃油与不确定性运行余量",
        "objective_en": "Protect fuel and uncertainty margins",
    },
    {
        "id": "climate",
        "agent": "Sustainability Agent",
        "label": "低碳运行",
        "label_en": "Sustainability",
        "objective": "减少滑行与远机位排放",
        "objective_en": "Reduce taxi and remote-stand emissions",
    },
)


def _losses(plan: PlanCandidate) -> dict[str, float]:
    report = plan.report
    shield = plan.risk_shield or {}
    return {
        "airport": (
            float(report.p90_delay_min)
            + 8.0 * float(report.remaining_conflicts)
            + 0.08 * float(report.recovery_minute)
        ),
        "passenger": (
            float(report.missed_connections)
            + 0.22 * float(report.mean_delay_min)
        ),
        "safety": (
            100.0 * float(report.fuel_risk_probability)
            + 18.0 * float(shield.get("fuel_escalations", 0))
            + 0.04 * float(shield.get("connection_passengers_exposed", 0))
        ),
        "climate": float(report.emissions_index),
    }


def nash_bargaining(plans: list[PlanCandidate]) -> dict[str, Any]:
    """Select a compromise using equal-weight Nash social welfare.

    Each stakeholder utility is normalized only within the displayed candidate
    set. A 0.05 floor represents a reservation utility and keeps the geometric
    score interpretable when a candidate is worst for one stakeholder.
    """
    if not plans:
        return {
            "method": "equal-weight Nash social welfare over CP-SAT candidates",
            "status": "unavailable",
            "recommended_id": None,
            "candidates": [],
            "stakeholders": list(_STAKEHOLDERS),
        }

    raw = {plan.id: _losses(plan) for plan in plans}
    bounds = {
        stakeholder["id"]: {
            "best": min(row[stakeholder["id"]] for row in raw.values()),
            "disagreement": max(row[stakeholder["id"]] for row in raw.values()),
        }
        for stakeholder in _STAKEHOLDERS
    }
    rows: list[dict[str, Any]] = []
    for plan in plans:
        utilities: dict[str, float] = {}
        for stakeholder in _STAKEHOLDERS:
            stakeholder_id = stakeholder["id"]
            best = bounds[stakeholder_id]["best"]
            disagreement = bounds[stakeholder_id]["disagreement"]
            span = disagreement - best
            normalized = 1.0 if span <= 1e-9 else (
                disagreement - raw[plan.id][stakeholder_id]
            ) / span
            utilities[stakeholder_id] = round(
                max(0.05, min(1.0, 0.05 + 0.95 * normalized)),
                3,
            )
        log_welfare = sum(math.log(value) for value in utilities.values())
        geometric_welfare = math.exp(log_welfare / len(utilities))
        rows.append({
            "id": plan.id,
            "title": plan.title,
            "profile": plan.profile.value,
            "utilities": utilities,
            "losses": {
                key: round(value, 2)
                for key, value in raw[plan.id].items()
            },
            "nash_welfare": round(geometric_welfare, 3),
            "minimum_utility": round(min(utilities.values()), 3),
            "autonomy_blocked": bool(
                plan.risk_shield.get("blocks_unattended_execution", False)
            ),
            "hard_constraints_passed": bool(plan.feasible),
        })
    rows.sort(key=lambda row: (
        -float(row["nash_welfare"]),
        -float(row["minimum_utility"]),
        bool(row["autonomy_blocked"]),
        str(row["id"]),
    ))
    for rank, row in enumerate(rows, start=1):
        row["bargaining_rank"] = rank

    winner = rows[0]
    nominal_id = plans[0].id
    changed = winner["id"] != nominal_id
    if changed:
        summary = (
            f"单一加权目标推荐{plans[0].title}；四方等权 Nash 协商推荐"
            f"{winner['title']}，其几何福利 {winner['nash_welfare']:.3f}、"
            f"最低方效用 {winner['minimum_utility']:.3f}。"
        )
        summary_en = (
            f"The nominal objective recommends {plans[0].title}; equal-weight "
            f"four-party Nash bargaining recommends {winner['title']} with "
            f"geometric welfare {winner['nash_welfare']:.3f} and minimum "
            f"stakeholder utility {winner['minimum_utility']:.3f}."
        )
    else:
        summary = (
            f"{winner['title']}同时是单一加权目标与四方 Nash 协商结果；"
            f"几何福利 {winner['nash_welfare']:.3f}。"
        )
        summary_en = (
            f"{winner['title']} is selected by both the nominal objective and "
            f"four-party Nash bargaining; geometric welfare "
            f"{winner['nash_welfare']:.3f}."
        )

    return {
        "method": "equal-weight Nash social welfare over CP-SAT candidates",
        "status": "compromise_found",
        "nominal_id": nominal_id,
        "recommended_id": winner["id"],
        "recommendation_changed": changed,
        "reservation_utility": 0.05,
        "stakeholders": [
            {
                **stakeholder,
                "weight": 0.25,
                "best_loss": round(bounds[stakeholder["id"]]["best"], 2),
                "disagreement_loss": round(
                    bounds[stakeholder["id"]]["disagreement"], 2
                ),
            }
            for stakeholder in _STAKEHOLDERS
        ],
        "candidates": rows,
        "summary": summary,
        "summary_en": summary_en,
        "caveat": (
            "这是演练中的透明协商机制，不代表真实航司合同或时隙权利；"
            "它只能在 CP-SAT 已生成且硬约束通过的候选之间选择，"
            "Risk Shield 仍可要求人工审批。"
        ),
    }
