"""Active Bayesian preference learning over already-safe plan candidates.

The learner never creates or edits aircraft assignments. It treats the operator's
pairwise choices as noisy evidence about soft-objective weights, updates a
particle approximation to a Bradley-Terry posterior, and asks the next pair with
the highest expected information gain.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

import numpy as np

from .schemas import OperatorIntent, PlanCandidate, ScenarioSpec


OBJECTIVES = ("delay", "connections", "fuel_risk", "conflicts", "emissions")
LABELS = {
    "delay": ("尾部延误", "Tail delay"),
    "connections": ("转机保护", "Connections"),
    "fuel_risk": ("燃油风险", "Fuel risk"),
    "conflicts": ("资源排队", "Resource queues"),
    "emissions": ("低碳", "Carbon"),
}
DEFAULT_WEIGHTS = {
    "delay": .30,
    "connections": .30,
    "fuel_risk": .20,
    "conflicts": .10,
    "emissions": .10,
}
PARTICLE_COUNT = 4096
PRIOR_STRENGTH = 6.0
CHOICE_TEMPERATURE = 6.0


def _normalized_prior(intent: OperatorIntent | None) -> dict[str, float]:
    raw = (
        intent.weights.normalized().model_dump()
        if intent is not None
        else DEFAULT_WEIGHTS
    )
    positive = {key: max(1e-4, float(raw.get(key, 0.0))) for key in OBJECTIVES}
    total = sum(positive.values())
    return {key: value / total for key, value in positive.items()}


def _stable_seed(scenario: ScenarioSpec, plans: list[PlanCandidate]) -> int:
    signatures = sorted(
        (
            f"{plan.profile.value}:"
            f"{plan.report.cvar_delay_min:.4f}:"
            f"{plan.report.missed_connections}:"
            f"{plan.report.fuel_risk_probability:.4f}:"
            f"{plan.report.remaining_conflicts}:"
            f"{plan.report.emissions_index:.4f}"
        )
        for plan in plans
    )
    identity = f"{scenario.seed}:" + ":".join(signatures)
    digest = hashlib.blake2s(identity.encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") % (2**32)


def _raw_loss(plan: PlanCandidate) -> dict[str, float]:
    shield = plan.risk_shield or {}
    return {
        "delay": float(plan.report.cvar_delay_min),
        "connections": float(plan.report.missed_connections),
        "fuel_risk": (
            100.0 * float(plan.report.fuel_risk_probability)
            + 20.0 * float(shield.get("fuel_escalations", 0) or 0)
        ),
        "conflicts": float(plan.report.remaining_conflicts),
        "emissions": float(plan.report.emissions_index),
    }


def _loss_rows(plans: list[PlanCandidate]) -> dict[str, dict[str, Any]]:
    raw = {plan.id: _raw_loss(plan) for plan in plans}
    minima = {
        key: min(values[key] for values in raw.values())
        for key in OBJECTIVES
    }
    maxima = {
        key: max(values[key] for values in raw.values())
        for key in OBJECTIVES
    }
    result: dict[str, dict[str, Any]] = {}
    for plan in plans:
        vector: list[float] = []
        for key in OBJECTIVES:
            span = maxima[key] - minima[key]
            vector.append(
                0.0 if span <= 1e-12 else (raw[plan.id][key] - minima[key]) / span
            )
        result[plan.id] = {
            "plan": plan,
            "raw": {key: round(value, 4) for key, value in raw[plan.id].items()},
            "vector": np.asarray(vector, dtype=float),
        }
    return result


def encode_preference_observation(
    plans: list[PlanCandidate],
    *,
    left_plan_id: str,
    right_plan_id: str,
    preferred_plan_id: str,
) -> dict[str, Any]:
    """Encode a plan-ID choice as a session-invariant normalized feature pair."""
    rows = _loss_rows(plans)
    left = rows[left_plan_id]
    right = rows[right_plan_id]
    winner = left if preferred_plan_id == left_plan_id else right
    loser = right if winner is left else left
    return {
        "objective_order": list(OBJECTIVES),
        "winner_vector": [round(float(value), 8) for value in winner["vector"]],
        "loser_vector": [round(float(value), 8) for value in loser["vector"]],
        "winner_title": str(winner["plan"].title),
        "loser_title": str(loser["plan"].title),
        "winner_plan_id": str(winner["plan"].id),
        "loser_plan_id": str(loser["plan"].id),
    }


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _weighted_quantile(
    values: np.ndarray,
    masses: np.ndarray,
    probability: float,
) -> float:
    order = np.argsort(values)
    sorted_values = values[order]
    cumulative = np.cumsum(masses[order])
    index = min(len(sorted_values) - 1, int(np.searchsorted(cumulative, probability)))
    return float(sorted_values[index])


def _update_masses(
    particles: np.ndarray,
    masses: np.ndarray,
    rows: dict[str, dict[str, Any]],
    comparisons: list[dict[str, str]],
) -> np.ndarray:
    updated = masses.copy()
    for comparison in comparisons:
        winner = rows.get(str(comparison.get("preferred_plan_id")))
        left = rows.get(str(comparison.get("left_plan_id")))
        right = rows.get(str(comparison.get("right_plan_id")))
        if winner is None or left is None or right is None:
            continue
        loser = right if winner is left else left
        if str(comparison.get("preferred_plan_id")) not in {
            str(comparison.get("left_plan_id")),
            str(comparison.get("right_plan_id")),
        }:
            continue
        # Positive evidence means the winner has lower weighted loss.
        advantage = particles @ (loser["vector"] - winner["vector"])
        likelihood = _sigmoid(CHOICE_TEMPERATURE * advantage)
        updated *= np.maximum(likelihood, 1e-12)
        normalizer = float(updated.sum())
        if normalizer <= 1e-15:
            updated.fill(1.0 / len(updated))
        else:
            updated /= normalizer
    return updated


def _update_historical_masses(
    particles: np.ndarray,
    masses: np.ndarray,
    observations: list[dict[str, Any]],
) -> tuple[np.ndarray, int]:
    """Apply portable winner/loser feature evidence from earlier sessions."""
    updated = masses.copy()
    accepted = 0
    for observation in observations:
        winner = np.asarray(observation.get("winner_vector", []), dtype=float)
        loser = np.asarray(observation.get("loser_vector", []), dtype=float)
        if (
            winner.shape != (len(OBJECTIVES),)
            or loser.shape != (len(OBJECTIVES),)
            or not np.all(np.isfinite(winner))
            or not np.all(np.isfinite(loser))
        ):
            continue
        likelihood = _sigmoid(
            CHOICE_TEMPERATURE * (particles @ (loser - winner))
        )
        updated *= np.maximum(likelihood, 1e-12)
        normalizer = float(updated.sum())
        if normalizer <= 1e-15:
            updated.fill(1.0 / len(updated))
        else:
            updated /= normalizer
        accepted += 1
    return updated, accepted


def _information_gain_bits(
    likelihood: np.ndarray,
    masses: np.ndarray,
) -> tuple[float, float]:
    p_left = float(np.dot(masses, likelihood))
    p_left = min(1.0 - 1e-12, max(1e-12, p_left))
    left_mass = masses * likelihood / p_left
    right_mass = masses * (1.0 - likelihood) / (1.0 - p_left)
    positive = masses > 0
    kl_left = float(np.sum(
        left_mass[positive]
        * np.log(np.maximum(left_mass[positive], 1e-300) / masses[positive])
    ))
    kl_right = float(np.sum(
        right_mass[positive]
        * np.log(np.maximum(right_mass[positive], 1e-300) / masses[positive])
    ))
    bits = (p_left * kl_left + (1.0 - p_left) * kl_right) / math.log(2.0)
    return max(0.0, bits), p_left


def learn_operator_preferences(
    scenario: ScenarioSpec,
    plans: list[PlanCandidate],
    intent: OperatorIntent | None = None,
    comparisons: list[dict[str, str]] | None = None,
    historical_observations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a deterministic particle posterior and its most informative query."""
    eligible = [plan for plan in plans if plan.feasible]
    history = list(comparisons or [])
    historical = list(historical_observations or [])
    historical_count = len(historical)
    prior = _normalized_prior(intent)
    if len(eligible) < 2:
        return {
            "method": "particle Bayesian Bradley-Terry + active mutual information",
            "status": "unavailable",
            "comparison_count": len(history) + historical_count,
            "session_comparison_count": len(history),
            "historical_comparison_count": historical_count,
            "comparisons": history,
            "recommended_id": eligible[0].id if eligible else None,
            "query": None,
            "posterior": {"weights": [], "effective_particles": 0.0},
            "candidates": [],
            "summary": "不足两个安全候选，无法进行成对偏好学习。",
            "summary_en": "Fewer than two safe candidates are available for pairwise learning.",
            "caveat": "偏好层只重新排序已通过硬约束的候选，绝不修改安全规则。",
        }

    rows = _loss_rows(eligible)
    alpha = np.asarray(
        [max(.05, prior[key] * PRIOR_STRENGTH) for key in OBJECTIVES],
        dtype=float,
    )
    rng = np.random.default_rng(_stable_seed(scenario, eligible))
    particles = rng.dirichlet(alpha, size=PARTICLE_COUNT)
    masses = np.full(PARTICLE_COUNT, 1.0 / PARTICLE_COUNT, dtype=float)
    masses = _update_masses(particles, masses, rows, history)
    masses, historical_count = _update_historical_masses(particles, masses, historical)

    posterior_mean = np.sum(particles * masses[:, None], axis=0)
    weights = []
    for index, key in enumerate(OBJECTIVES):
        weights.append({
            "id": key,
            "label": LABELS[key][0],
            "label_en": LABELS[key][1],
            "prior": round(prior[key], 4),
            "mean": round(float(posterior_mean[index]), 4),
            "low90": round(_weighted_quantile(particles[:, index], masses, .05), 4),
            "high90": round(_weighted_quantile(particles[:, index], masses, .95), 4),
        })

    matrix = np.stack([rows[plan.id]["vector"] for plan in eligible], axis=1)
    losses = particles @ matrix
    winners = np.argmin(losses, axis=1)
    optimal_probabilities = [
        float(masses[winners == index].sum())
        for index in range(len(eligible))
    ]
    candidate_rows = []
    for index, plan in enumerate(eligible):
        candidate_rows.append({
            "id": plan.id,
            "title": plan.title,
            "profile": plan.profile.value,
            "posterior_expected_loss": round(float(np.dot(masses, losses[:, index])), 4),
            "optimal_probability": round(optimal_probabilities[index], 4),
            "raw_metrics": rows[plan.id]["raw"],
        })
    candidate_rows.sort(key=lambda item: (
        float(item["posterior_expected_loss"]),
        -float(item["optimal_probability"]),
        str(item["id"]),
    ))
    for rank, item in enumerate(candidate_rows, start=1):
        item["preference_rank"] = rank

    seen_pairs = {
        frozenset((str(item["left_plan_id"]), str(item["right_plan_id"])))
        for item in history
    }
    queries = []
    for left_index, left in enumerate(eligible):
        for right in eligible[left_index + 1:]:
            if frozenset((left.id, right.id)) in seen_pairs:
                continue
            likelihood = _sigmoid(
                CHOICE_TEMPERATURE
                * (particles @ (rows[right.id]["vector"] - rows[left.id]["vector"]))
            )
            information_bits, p_left = _information_gain_bits(likelihood, masses)
            distance = float(np.linalg.norm(
                rows[left.id]["vector"] - rows[right.id]["vector"]
            ))
            queries.append({
                "left_plan_id": left.id,
                "left_title": left.title,
                "right_plan_id": right.id,
                "right_title": right.title,
                "expected_information_bits": round(information_bits, 4),
                "predicted_left_probability": round(p_left, 4),
                "objective_distance": round(distance, 4),
            })
    queries.sort(key=lambda item: (
        -float(item["expected_information_bits"]),
        -float(item["objective_distance"]),
        str(item["left_plan_id"]),
        str(item["right_plan_id"]),
    ))
    query = queries[0] if queries else None
    winner = candidate_rows[0]
    nominal = eligible[0]
    strongest = max(weights, key=lambda item: float(item["mean"]))
    changed = winner["id"] != nominal.id
    total_comparisons = len(history) + historical_count
    if total_comparisons:
        summary = (
            f"综合 {historical_count} 次跨会话记忆与 {len(history)} 次本轮选择，"
            f"当前最可能偏好“{strongest['label']}”；偏好后验推荐{winner['title']}"
            f"（成为最优的概率 {float(winner['optimal_probability']) * 100:.1f}%）。"
        )
        summary_en = (
            f"Combining {historical_count} cross-session memories and {len(history)} "
            f"choices in this run, the posterior emphasizes {strongest['label_en']} "
            f"and recommends {winner['title']} with a "
            f"{float(winner['optimal_probability']) * 100:.1f}% probability of being optimal."
        )
    else:
        summary = (
            f"尚未读取操作员选择；当前以已确认目标为先验，下一问预计提供 "
            f"{float(query['expected_information_bits']) if query else 0:.3f} bit 信息。"
        )
        summary_en = (
            "No operator choices have been observed; confirmed objectives form the prior "
            f"and the next query is worth about {float(query['expected_information_bits']) if query else 0:.3f} bit."
        )

    return {
        "method": "4096-particle Bayesian Bradley-Terry + mutual-information active query",
        "status": "learning" if query is not None else "complete",
        "particle_count": PARTICLE_COUNT,
        "prior_strength": PRIOR_STRENGTH,
        "choice_temperature": CHOICE_TEMPERATURE,
        "comparison_count": total_comparisons,
        "session_comparison_count": len(history),
        "historical_comparison_count": historical_count,
        "memory_scope": "local-demo-profile",
        "comparisons": history,
        "prior_weights": prior,
        "posterior": {
            "weights": weights,
            "effective_particles": round(
                float(1.0 / np.sum(np.square(masses))),
                1,
            ),
        },
        "candidates": candidate_rows,
        "nominal_id": nominal.id,
        "recommended_id": winner["id"],
        "recommendation_changed": changed,
        "query": query,
        "summary": summary,
        "summary_en": summary_en,
        "caveat": (
            "这是合成演练中的小样本偏好后验，不代表稳定的人类价值观；"
            "每次回答只重新排序已通过 CP-SAT 硬约束的候选，"
            "跑道、机位、燃油和医疗优先规则不参与学习且不可被覆盖；"
            "跨会话记忆可由操作员撤销，撤销保留审计记录但不再参与后验。"
        ),
    }

