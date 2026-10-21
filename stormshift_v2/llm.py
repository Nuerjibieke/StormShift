"""Validated local-Qwen gateway with a deterministic, demo-safe fallback."""

from __future__ import annotations

import json
import re
from typing import Any, Literal, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .config import Settings, settings
from .intent_compiler import compile_intent
from .schemas import (
    Explanation,
    ObjectiveWeights,
    OperatorIntent,
    ScenarioSpec,
    ScheduledConstraint,
)

T = TypeVar("T", bound=BaseModel)


def deterministic_intent(
    text: str,
    source: Literal["text", "voice", "fallback"] = "fallback",
) -> OperatorIntent:
    """Compatibility entry point; API parsing supplies the live scenario."""
    return compile_intent(text, source=source)


async def _structured_completion(
    base_url: str,
    model: str,
    schema_type: type[T],
    system_prompt: str,
    user_prompt: str,
    timeout: float,
) -> T:
    schema = schema_type.model_json_schema()
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 650,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_type.__name__, "schema": schema, "strict": True},
        },
        "chat_template_kwargs": {"enable_thinking": False},
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{base_url.rstrip('/')}/chat/completions", json=payload)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return schema_type.model_validate_json(content)



def _canonicalize_intent(
    parsed: OperatorIntent,
    text: str,
    scenario: ScenarioSpec,
    source: Literal["text", "voice"],
) -> OperatorIntent:
    """Model output is advisory, never evidence for an executable constraint.

    The bounded compiler owns operational facts and unresolved questions in both
    model and fallback modes. Free-form model-only interpretations require a
    future grounded proposal/clarification protocol; they are not auto-applied.
    """
    return compile_intent(text, source=source, scenario=scenario)

async def parse_intent(
    text: str,
    scenario: ScenarioSpec,
    *,
    source: Literal["text", "voice"] = "text",
    deep: bool = False,
    config: Settings = settings,
) -> tuple[OperatorIntent, Literal["deep", "fast", "fallback"]]:
    if config.llm_mode.lower() not in {"off", "false", "0"}:
        tiers = (
            [(config.deep_base_url, config.deep_model, "deep", 12.0)] if deep else []
        ) + [(config.fast_base_url, config.fast_model, "fast", 4.5)]
        prompt = json.dumps(
            {
                "command": text,
                "airport": scenario.airport,
                "runways": [item.model_dump(mode="json") for item in scenario.runways],
                "gates": [item.model_dump(mode="json") for item in scenario.gates],
                "instruction": (
                    "Translate soft preferences, scheduled operator constraints, and explicit "
                    "passenger outcome targets. Outcome targets are probabilistic service goals, "
                    "never hard safety rules. Never weaken fuel, runway, separation, gate "
                    "uniqueness, medical, or safety rules."
                ),
            },
            ensure_ascii=False,
        )
        for base_url, model, tier, timeout in tiers:
            try:
                parsed = await _structured_completion(
                    base_url,
                    model,
                    OperatorIntent,
                    "You are an airport operations intent translator. Return schema-valid JSON only. Hard safety constraints cannot be changed.",
                    prompt,
                    timeout,
                )
                parsed = _canonicalize_intent(parsed, text, scenario, source)
                return parsed, tier  # type: ignore[return-value]
            except (httpx.HTTPError, KeyError, TypeError, ValidationError, json.JSONDecodeError):
                continue
    return compile_intent(text, source=source, scenario=scenario), "fallback"


def _canonicalize_explanation(
    parsed: Explanation,
    candidates: list[dict[str, Any]],
) -> Explanation:
    """Ground a schema-valid model response in solver and simulation facts."""
    if not candidates:
        return parsed
    best = candidates[0]
    report = best.get("report") or {}
    title = str(best.get("title", "候选方案"))
    score = float(best.get("score", 0))
    grounded_why = [
        (
            f"50 次固定种子仿真：平均延误 {float(report.get('mean_delay_min', 0)):.1f} 分钟，"
            f"P90 {float(report.get('p90_delay_min', 0)):.1f} 分钟，"
            f"预计错失转机 {int(report.get('missed_connections', 0))} 人"
        ),
        "独立安全闸门已验证跑道、间隔、机位、燃油及医疗优先硬约束",
    ]
    why_not = []
    for other in candidates[1:3]:
        other_report = other.get("report") or {}
        gap = max(0.0, float(other.get("score", score)) - score)
        why_not.append(
            f"{other.get('title', '其他候选')} 综合风险分高 {gap:.2f}；"
            f"平均延误 {float(other_report.get('mean_delay_min', 0)):.1f} 分钟，"
            f"错失转机 {int(other_report.get('missed_connections', 0))} 人"
        )
    if not why_not:
        why_not = ["其他策略生成了相同调度或未通过可行性筛选"]
    return Explanation(
        recommendation=f"推荐 {title}",
        why=grounded_why,
        why_not=why_not,
        sensitivities=parsed.sensitivities[:2],
        caveat="推荐名称与全部数值由求解器和仿真结果重建；模型不能改写可执行动作",
    )


async def explain_recommendation(
    scenario: ScenarioSpec,
    candidates: list[dict[str, Any]],
    *,
    deep: bool = False,
    config: Settings = settings,
) -> tuple[Explanation, Literal["deep", "fast", "fallback"]]:
    fallback = Explanation(
        recommendation=f"推荐 {candidates[0]['title']}" if candidates else "当前无可行方案",
        why=[
            "约束求解器证明方案满足跑道、机位、燃油和医疗优先硬约束",
            "固定种子的 Monte Carlo 仿真综合风险最低",
        ],
        why_not=["其他候选在延误、旅客影响或排放中至少一项更高"],
        sensitivities=["跑道恢复时间变化会触发重新仿真与自动重规划"],
    )
    if not candidates or config.llm_mode.lower() in {"off", "false", "0"}:
        return _canonicalize_explanation(fallback, candidates), "fallback"
    tiers = (
        [(config.deep_base_url, config.deep_model, "deep", 18.0)] if deep else []
    ) + [(config.fast_base_url, config.fast_model, "fast", 5.0)]
    prompt = json.dumps(
        {"scenario": scenario.name, "weather": scenario.weather.model_dump(mode="json"), "candidates": candidates},
        ensure_ascii=False,
    )
    for base_url, model, tier, timeout in tiers:
        try:
            result = await _structured_completion(
                base_url,
                model,
                Explanation,
                "Explain solver-generated airport recovery candidates. Do not invent actions, metrics, or hidden reasoning. Return JSON only.",
                prompt,
                timeout,
            )
            return _canonicalize_explanation(result, candidates), tier  # type: ignore[return-value]
        except (httpx.HTTPError, KeyError, TypeError, ValidationError, json.JSONDecodeError):
            continue
    return _canonicalize_explanation(fallback, candidates), "fallback"


async def model_health(config: Settings = settings) -> dict[str, str]:
    status: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=1.0) as client:
        for label, url in (("fast", config.fast_base_url), ("deep", config.deep_base_url)):
            try:
                response = await client.get(f"{url.rstrip('/')}/models")
                status[label] = "ready" if response.is_success else f"http-{response.status_code}"
            except httpx.HTTPError:
                status[label] = "fallback"
    return status
