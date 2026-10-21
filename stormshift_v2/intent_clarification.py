"""Bounded, source-bound clarification of one pending command clause.

A reply edits only the selected unresolved clause. The complete draft is compiled
again and merged with the confirmed predecessor; it never becomes executable
until the ordinary confirmation and safety gates accept it.
"""
from __future__ import annotations

import re

from .intent_compiler import TARGETS, number
from .schemas import (
    IntentClarificationQuestion, IntentClarificationRequest, IntentClarificationTurn,
    OperatorIntent,
)

PROMPTS = {
    "average_delay_cap": ("平均延误上限设为多少分钟？", "例如：22 分钟", "平均延误不超过{}分钟"),
    "minimum_connection_rate": ("按时转机率至少需要多少百分比？", "例如：95%", "转机成功率至少{}%"),
    "maximum_missed_connections": ("平均错失转机人数最多允许多少人？", "例如：20 人", "平均错失转机不超过{}人"),
    "maximum_tail_exposure_rate": ("尾部观察比例最多允许多少百分比？", "例如：10%", "尾部风险不超过{}%"),
}
CLAUSE_QUESTION = "请只改写这条有问题的要求，明确资源、动作或目标；其余要求将保留。"
CLAUSE_PLACEHOLDER = "例如：暂不开放07R（不是仅回复“是”）"


def _clause_span(text: str, excerpt: str) -> tuple[int, int] | None:
    spans = []
    for match in re.finditer(r"[^，,；;。\n]+", text):
        value = match.group()
        if value.strip() == excerpt:
            start = match.start() + len(value) - len(value.lstrip())
            spans.append((start, start + len(excerpt)))
    # Cross-clause conflicts or duplicate text must be rewritten as a full command.
    return spans[0] if len(spans) == 1 else None


def clarification_questions(intent: OperatorIntent) -> list[IntentClarificationQuestion]:
    questions = []
    for index, issue in enumerate(intent.issues):
        if _clause_span(intent.raw_text, issue.excerpt) is None:
            continue
        target = "clause"
        # No convenient number answer for unsafe instructions or uncertain actions.
        if issue.code in {"unsupported_clause", "invalid_target", "ambiguous_target"}:
            matches = [field for field, (pattern, *_rest) in TARGETS.items()
                       if re.search(pattern, issue.excerpt, re.I)]
            if len(matches) == 1 and not re.search(r"关闭|开放|重开|恢复|close|open", issue.excerpt, re.I):
                target = matches[0]
        question, placeholder = PROMPTS[target][:2] if target in PROMPTS else (CLAUSE_QUESTION, CLAUSE_PLACEHOLDER)
        questions.append(IntentClarificationQuestion(
            issue_index=index, target=target, question=question, placeholder=placeholder))
    return questions


def _replacement(target: str, answer: str) -> str:
    if target not in PROMPTS:
        return answer
    if target in {"minimum_connection_rate", "maximum_tail_exposure_rate"}:
        unit = r"(?:%|％|percent)?"
    elif target == "average_delay_cap":
        unit = r"(?:分钟|分|minutes?|mins?)?"
    else:
        unit = r"(?:人|pax|passengers?)?"
    value = re.fullmatch(r"([-+]?\d+(?:\.\d+)?|[零一二两三四五六七八九十]+)\s*" + unit, answer, re.I)
    if not value:
        return answer  # Full replacement clauses still go through the normal compiler.
    raw = value.group(1)
    parsed = raw if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", raw) else number(raw)
    return PROMPTS[target][2].format(parsed) if parsed is not None else answer


def compose_clarification(intent: OperatorIntent | None, request: IntentClarificationRequest):
    if intent is None or not intent.needs_confirmation or not intent.issues:
        raise ValueError("没有待澄清的草稿，请先提交完整指令。")
    if request.expected_version != intent.version:
        raise ValueError("草稿版本已变化，请刷新并回答当前问题。")
    if len(intent.clarification_history) >= 8:
        raise ValueError("这份草稿已补充 8 次，请整理后重新解析完整指令。")
    question = next((q for q in clarification_questions(intent)
                     if q.issue_index == request.issue_index), None)
    if question is None:
        raise ValueError("该问题涉及多个子句或已变化，请重新解析完整指令。")
    answer = request.text.strip().rstrip("。！!？?").strip()
    if not answer or re.fullmatch(r"是|是的|确认|同意|好|好的|没问题|不是|否|yes|no|ok|okay|sure", answer, re.I):
        raise ValueError("仅回答是/否不能消除歧义，请补充具体数值或改写这条要求。")
    issue = intent.issues[request.issue_index]
    start, end = _clause_span(intent.raw_text, issue.excerpt)
    replacement = _replacement(question.target, answer)
    text = intent.raw_text[:start] + replacement + intent.raw_text[end:]
    if len(text) > 1000:
        raise ValueError("合并后的指令超过 1000 字，请整理后重新解析完整指令。")
    turn = IntentClarificationTurn(version=intent.version, issue_index=request.issue_index,
        question=question.question, excerpt=issue.excerpt, answer=request.text.strip(), replacement=replacement)
    return text, turn
