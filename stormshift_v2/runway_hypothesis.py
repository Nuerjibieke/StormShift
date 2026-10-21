"""A single explicit counterfactual notice; never changes the source airport."""
from __future__ import annotations

from pydantic import Field

from .schemas import EventSpec, EventType, ResourceStatus, StrictModel


class RunwayDelayHypothesis(StrictModel):
    runway_id: str = Field(min_length=1, max_length=40)
    delay_min: int = Field(strict=True, ge=1, le=30)


def delay_notice(state, events, hypothesis: RunwayDelayHypothesis):
    runway = next((r for r in state.scenario.runways if r.id == hypothesis.runway_id), None)
    now = state.scenario.current_minute
    if (runway is None or runway.status != ResourceStatus.closed
            or runway.reopen_minute is None or runway.reopen_minute <= now):
        raise ValueError("假设只能延后当前关闭跑道的未来开放时刻 / Runway must be closed with a future reopening")
    end = runway.reopen_minute + hypothesis.delay_min
    if end > 240:
        raise ValueError("假设超出演练时间范围 / Hypothesis exceeds the simulation horizon")
    processed = set(state.execution.processed_event_ids) if state.execution else set()
    for event in events:
        if (event.id not in processed and now <= event.minute <= end
                and event.resource_id == runway.id
                and event.type in {EventType.runway_closed, EventType.runway_reopened}):
            raise ValueError("同一跑道已有冲突的排定事件，不能叠加此假设 / Conflicting scheduled runway event")
    identifier = f"whatif:runway-delay:{runway.id}:{end}"
    if any(event.id == identifier for event in events):
        raise ValueError("假设事件标识冲突 / Hypothesis event ID collision")
    metadata = {
        "kind": "runway_reopening_delay", "runway_id": runway.id,
        "delay_min": hypothesis.delay_min, "notice_minute": now,
        "original_reopen_minute": runway.reopen_minute,
        "hypothetical_reopen_minute": end, "event_id": identifier,
        "scope": "nominal_event_rehearsal_only",
    }
    notice = EventSpec(
        id=identifier, type=EventType.runway_closed, minute=now,
        resource_id=runway.id, duration_min=end - now,
        payload={"reason": "假设：收到跑道晚开放通知 / Hypothetical delayed reopening notice",
                 "preview_only": True, "hypothesis": metadata},
    )
    return metadata, notice
