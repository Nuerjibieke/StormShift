"""Source-bound command compiler. Unresolved meaning never becomes an action.

This intentionally bounded grammar is also the no-model path. Unsupported
language is exposed as a clarification, not silently replaced by a default.
It does not implement decision-aware question selection or free-form planning.
"""
from __future__ import annotations

import math
import re

from .schemas import IntentEvidence, IntentIssue, ObjectiveWeights, OperatorIntent, ScenarioSpec, ScheduledConstraint

TARGETS = {
    "average_delay_cap": (r"(?:平均)?延误|(?:average\s+)?delay", 180, 1, "平均延误上限 {} 分钟"),
    "minimum_connection_rate": (r"(?:按时)?(?:转机|衔接)(?:成功率|按时率|保障率|率)|(?:connection|transfer)\s+(?:rate|success)", 100, 100, "转机按时率至少 {}%"),
    "maximum_missed_connections": (r"错失转机|未能衔接|未衔接|转机超时|没赶上|missed connections?", 6000, 1, "平均错失转机不超过 {} 人"),
    "maximum_tail_exposure_rate": (r"尾部风险|尾部暴露|尾部观察(?:比例)?|风险观察(?:比例)?|tail risk|tail exposure", 100, 100, "尾部观察比例不超过 {}%"),
}
LOWER = r"至少|不低于|>=|at least"
UPPER = r"不超过|最多|<=|at most|no more than"
RESOURCE = re.compile(r"(?<![A-Za-z0-9])(?:\d{2}[LRC]|[A-Z]+\d{1,3})(?![A-Za-z0-9])", re.I)
ACTION = re.compile(r"关闭|封闭|开放|重开|恢复|reopen|close|open", re.I)
NUMBER = r"[零一二两三四五六七八九十百\d]+"


def number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    digits["两"] = 2
    if value in digits:
        return digits[value]
    if re.fullmatch(r"[一二两三四五六七八九]?十[一二三四五六七八九]?", value):
        left, right = value.split("十")
        return digits.get(left, 1) * 10 + digits.get(right, 0)
    return None


def add_issue(intent, code, excerpt, message):
    if not any(x.code == code and x.excerpt == excerpt for x in intent.issues):
        intent.issues.append(IntentIssue(code=code, excerpt=excerpt, message=message))


def check_resource_windows(intent):
    by_resource = {}
    for item in intent.scheduled_constraints:
        by_resource.setdefault(item.resource_id, set()).add((item.kind, item.minute))
    for resource, entries in by_resource.items():
        if len(entries) > 1:
            add_issue(intent, "resource_window", resource or "", "同一资源存在多次开闭安排；当前求解器尚不支持这种时间窗口，请明确本轮单次安排。")


def compile_intent(text: str, source="fallback", scenario: ScenarioSpec | None = None) -> OperatorIntent:
    intent = OperatorIntent(raw_text=text, source=source,
                            base_minute=scenario.current_minute if scenario else 0)
    labels = {}
    action_labels = []
    known = {x.id.upper() for x in [*scenario.runways, *scenario.gates]} if scenario else None

    def evidence(field, clause):
        intent.evidence.append(IntentEvidence(field=field, excerpt=clause))
        if field not in intent.specified_fields:
            intent.specified_fields.append(field)

    for clause in re.split(r"[，,；;。\n]+", text):
        clause = clause.strip()
        if not clause:
            continue
        if re.search(r"(?:忽略|绕过|取消|放宽).*(?:燃油|安全|间隔|医疗|跑道关闭|机位唯一)|(?:ignore|bypass|disable).*(?:fuel|safety|separation|medical|closure|gate uniqueness)", clause, re.I):
            add_issue(intent, "safety_override", clause, "安全硬约束不能被指挥意图覆盖，请修改这条要求。")
            continue
        recognized = False
        for field, (pattern, maximum, divisor, template) in TARGETS.items():
            if not re.search(pattern, clause, re.I):
                continue
            if re.search(r"取消|移除|不再限制|remove|clear", clause, re.I):
                if field in intent.specified_fields:
                    add_issue(intent, "conflicting_target", clause, "同一目标同时被设置和取消，请保留一个明确要求。")
                intent.cleared_fields.append(field)
                setattr(intent, field, None)
                evidence(field, clause)
                labels[field] = "取消：" + template.format("—")
                recognized = True
                continue
            values = re.findall(r"(?<![A-Za-z0-9])[-+]?\d+(?:\.\d+)?", clause)
            # No number may be a preference rather than a measurable target.
            if not values:
                continue
            recognized = True
            comparator = LOWER if field == "minimum_connection_rate" else UPPER
            if (len(values) != 1 or not re.search(comparator, clause, re.I)
                    or field in intent.specified_fields):
                add_issue(intent, "ambiguous_target", clause, "请给出一个明确的目标和上限/下限；不支持的比较关系不会自动改写。")
                continue
            value = float(values[0])
            if (not 0 <= value <= maximum
                    or (field == "maximum_missed_connections" and not value.is_integer())
                    or (divisor == 100 and "%" not in clause and "％" not in clause)):
                add_issue(intent, "invalid_target", clause, f"目标数值或单位无效；支持范围为 0–{maximum}，百分比需要 %。")
                continue
            setattr(intent, field, int(value) if field == "maximum_missed_connections" else value / divisor)
            evidence(field, clause)
            labels[field] = template.format(f"{value:g}")

        resource_tokens = RESOURCE.findall(clause)
        verbs = ACTION.findall(clause)
        if resource_tokens or (verbs and not recognized and not re.search("容量|吞吐", clause)):
            recognized = True
            if len(verbs) > 1:
                add_issue(intent, "ambiguous_action", clause, "同条内容有多个动作，请用分号分开每个资源指令。")
                continue
            if re.search(r"直到|持续|仅|只|如果|除非|until|only|unless|\bif\b|\bfor\b", clause, re.I):
                add_issue(intent, "conditional_action", clause, "条件或持续时间不能改写成单次资源动作，请明确本轮安排。")
                continue
            if re.search(r"不要|不应|勿|别|暂不|尚未|未(?:开放|恢复)|不(?:再|能|得)?(?:关闭|开放|恢复|重开)|do not|don't|never|\bnot\b", clause, re.I):
                intent.notes.append("未生成资源动作（否定表达）：" + clause)
                continue
            if re.search(r"预计|可能|估计|视情况|may|might|expected|预计", clause, re.I):
                add_issue(intent, "forecast_confirmation", clause, "这是预测还是已确认的资源指令？预计恢复不等于获准开放。")
                continue
            if re.search(r"取消|撤销|cancel", clause, re.I):
                add_issue(intent, "unsupported_cancellation", clause, "资源安排的撤销需要明确对应的已确认动作；本条未执行。")
                continue
            if len(resource_tokens) != 1 or len(verbs) != 1:
                add_issue(intent, "ambiguous_action", clause, "请逐条明确资源、开闭动作和时刻；部分机型限制不能当作整个机位关闭。")
                continue
            resource = resource_tokens[0].upper()
            if known is not None and resource not in known:
                add_issue(intent, "unknown_resource", clause, "当前场景不存在资源 " + resource + "，请核对编号。")
                continue
            relative = re.search(r"(" + NUMBER + r")\s*分钟\s*后|\bin\s+(\d+)\s*(?:minutes?|mins?)\b", clause, re.I)
            absolute = re.search(r"T\s*\+\s*(\d+)", clause, re.I)
            if (relative and absolute) or len(re.findall(r"分钟|minutes?|mins?", clause, re.I)) > 1:
                add_issue(intent, "ambiguous_time", clause, "同条消息有多个时间基准，请只保留一个。")
                continue
            if relative:
                offset = number(relative.group(1) or relative.group(2))
                minute = math.ceil(intent.base_minute + offset) if offset is not None else None
            elif absolute:
                minute = int(absolute.group(1))
            elif re.search(r"\d|[一二三四五六七八九十]+.*(?:分钟|小时)", RESOURCE.sub("", clause)):
                add_issue(intent, "ambiguous_time", clause, "时间表达尚不能可靠解析，请用“十分钟后”或“T+20”。")
                continue
            else:
                minute = math.ceil(intent.base_minute)
            if minute is None or not intent.base_minute <= minute <= 240:
                add_issue(intent, "invalid_time", clause, "执行时刻须在当前仿真时刻至 T+240 之间，不会自动截断。")
                continue
            verb = verbs[0].lower()
            kind = "close_resource" if verb in {"关闭", "封闭", "close"} else "reopen_resource"
            action = ScheduledConstraint(kind=kind, resource_id=resource, minute=minute, hard=False)
            if action not in intent.scheduled_constraints:
                intent.scheduled_constraints.append(action)
                evidence("scheduled_constraints", clause)
                action_labels.append(f"T+{minute} {'关闭' if kind == 'close_resource' else '开放'} {resource}")

        # Explicit soft preference, not merely mentioning a metric or negating it.
        preference = re.search(r"优先|优先保护|降低|提高|尽快恢复|prioriti[sz]e|prefer|reduce", clause, re.I)
        if preference and not re.search(r"不要|不再|do not|don't", clause, re.I):
            presets = [
                (r"转机|旅客|乘客|connection|passenger", (.24,.48,.16,.07,.05), "优先保护转机旅客"),
                (r"容量|吞吐|capacity", (.52,.18,.16,.09,.05), "优先恢复容量"),
                (r"低碳|排放|emission|carbon", (.18,.16,.12,.06,.48), "降低滑行排放"),
                (r"燃油|油量|fuel", (.20,.20,.45,.10,.05), "提高燃油风险权重"),
            ]
            for pattern, values, label in presets:
                if re.search(pattern, clause, re.I):
                    if "weights" in intent.specified_fields:
                        add_issue(intent, "multiple_preferences", clause, "多个优先目标需要明确主次；请给出本轮最优先目标。")
                    intent.weights = ObjectiveWeights(**dict(zip(ObjectiveWeights.model_fields, values))).normalized()
                    evidence("weights", clause)
                    labels["weights"] = label
                    recognized = True
        if not recognized:
            if re.fullmatch(r"(?:请)?(?:检查并)?优化(?:当前运行|当前方案)?|检查当前运行|optimize(?: current operations)?", clause, re.I):
                intent.notes.append("重新检查当前运行，保留已确认限制。")
            else:
                add_issue(intent, "unsupported_clause", clause, "此条内容尚未可靠转换，请改写或明确资源、目标和时刻；未忽略这条消息。")

    check_resource_windows(intent)
    intent.summary = "；".join([labels[k] for k in ["weights", *TARGETS] if k in labels] + action_labels)
    if not intent.summary:
        intent.summary = "需要澄清后再确认" if intent.issues else "没有新增执行动作，保留当前限制"
    return intent


def merge_confirmed(draft: OperatorIntent, previous: OperatorIntent | None) -> OperatorIntent:
    """Omission is not revocation. Pending drafts never supply inherited facts."""
    if previous is None or previous.needs_confirmation:
        return draft
    for field in ["weights", *TARGETS]:
        if field not in draft.specified_fields:
            setattr(draft, field, getattr(previous, field))
            if getattr(previous, field) is not None:
                draft.inherited_fields.append(field)
    inherited = [x for x in previous.scheduled_constraints if x not in draft.scheduled_constraints]
    draft.scheduled_constraints = [*inherited, *draft.scheduled_constraints]
    if inherited:
        draft.inherited_fields.append("scheduled_constraints")
    check_resource_windows(draft)
    if draft.inherited_fields:
        draft.notes.append(f"保留已确认指令 v{previous.version} 的未修改限制；本次未提及不代表取消。")
    return draft

def confirmed_intent(state):
    """Execution/analysis consumes confirmed facts, never an in-progress draft."""
    if state.intent is None or not state.intent.needs_confirmation:
        return state.intent
    snapshot = state.decision_lab.get("intent_predecessor", {}).get("snapshot")
    return OperatorIntent.model_validate(snapshot) if snapshot else None
