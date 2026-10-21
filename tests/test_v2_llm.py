import asyncio
from dataclasses import replace
from unittest.mock import patch

from stormshift_v2.config import settings
from stormshift_v2.llm import explain_recommendation, parse_intent
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.schemas import (
    Explanation,
    ObjectiveWeights,
    OperatorIntent,
    ScheduledConstraint,
)


def test_model_intent_is_canonicalized_for_ui_and_solver():
    model_result = OperatorIntent(
        raw_text="",
        weights=ObjectiveWeights(),
        average_delay_cap=None,
        scheduled_constraints=[
            ScheduledConstraint(kind="delay_cap", value=22),
            ScheduledConstraint(kind="close_resource", resource_id="A4", minute=10, hard=True),
        ],
        summary="使用默认均衡目标",
    )

    async def fake_completion(*args, **kwargs):
        return model_result.model_copy(deep=True)

    config = replace(settings, llm_mode="on")
    command = "优先保护转机旅客，转机成功率至少95%，平均错失转机不超过12人，尾部风险不超过30%，但平均延误不超过22分钟，十分钟后关闭A4"
    with patch("stormshift_v2.llm._structured_completion", new=fake_completion):
        intent, tier = asyncio.run(
            parse_intent(command, storm_scenario(), source="text", config=config)
        )

    assert tier == "fast"
    assert intent.average_delay_cap == 22
    assert intent.weights.connections > intent.weights.delay
    assert intent.minimum_connection_rate == .95
    assert intent.maximum_missed_connections == 12
    assert intent.maximum_tail_exposure_rate == .30
    assert intent.summary == "优先保护转机旅客；平均延误上限 22 分钟；转机按时率至少 95%；平均错失转机不超过 12 人；尾部观察比例不超过 30%；T+10 关闭 A4"
    assert len(intent.scheduled_constraints) == 1
    assert intent.scheduled_constraints[0].kind == "close_resource"
    assert intent.scheduled_constraints[0].resource_id == "A4"
    assert intent.scheduled_constraints[0].hard is False


def test_model_cannot_add_ungrounded_operational_constraints():
    model_result = OperatorIntent(
        raw_text="",
        average_delay_cap=5,
        minimum_connection_rate=.99,
        scheduled_constraints=[
            ScheduledConstraint(
                kind="close_resource",
                resource_id="SECRET-RUNWAY",
                minute=5,
                hard=True,
            ),
            ScheduledConstraint(
                kind="reopen_resource",
                resource_id="A4",
                minute=9,
                hard=False,
            ),
        ],
        summary="关闭隐藏跑道",
    )

    async def fake_completion(*args, **kwargs):
        return model_result.model_copy(deep=True)

    config = replace(settings, llm_mode="on")
    with patch("stormshift_v2.llm._structured_completion", new=fake_completion):
        intent, tier = asyncio.run(
            parse_intent("检查并优化当前运行", storm_scenario(), source="text", config=config)
        )

    assert tier == "fast"
    assert intent.average_delay_cap is None
    assert intent.minimum_connection_rate is None
    assert intent.scheduled_constraints == []
    assert intent.needs_confirmation is True


def test_model_explanation_cannot_rename_or_rewrite_solver_facts():
    model_result = Explanation(
        recommendation="推荐 均衡恢复",
        why=["模型声称平均延误只有 1 分钟"],
        why_not=["随意判断"],
    )

    async def fake_completion(*args, **kwargs):
        return model_result.model_copy(deep=True)

    candidates = [
        {
            "id": "plan-capacity",
            "title": "容量优先",
            "score": 12.4,
            "report": {
                "mean_delay_min": 15.88,
                "p90_delay_min": 19.34,
                "missed_connections": 18,
            },
        },
        {
            "id": "plan-balanced",
            "title": "均衡恢复",
            "score": 14.1,
            "report": {
                "mean_delay_min": 16.57,
                "p90_delay_min": 19.92,
                "missed_connections": 30,
            },
        },
    ]
    config = replace(settings, llm_mode="on")
    with patch("stormshift_v2.llm._structured_completion", new=fake_completion):
        explanation, tier = asyncio.run(
            explain_recommendation(storm_scenario(), candidates, config=config)
        )

    assert tier == "fast"
    assert explanation.recommendation == "推荐 容量优先"
    assert "15.9" in explanation.why[0]
    assert "19.3" in explanation.why[0]
    assert "1 分钟" not in " ".join(explanation.why)
    assert "均衡恢复" in explanation.why_not[0]

def test_fallback_understands_natural_decimal_on_time_transfer_rate():
    command = "优先保护转机旅客，按时转机率至少 99.5%，尾部观察比例不超过 1%"
    intent, tier = asyncio.run(
        parse_intent(
            command,
            storm_scenario(),
            source="text",
            config=replace(settings, llm_mode="off"),
        )
    )

    assert tier == "fallback"
    assert intent.minimum_connection_rate == .995
    assert intent.maximum_tail_exposure_rate == .01
    assert "99.5%" in intent.summary

