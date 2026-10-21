"""Runtime verification for the event-time recovery contract.

This monitor verifies observable execution events. It does not infer hidden
reasoning and does not replace the assignment-level safety validator.
"""

from __future__ import annotations

from typing import Any, Iterable

from .schemas import ExecutionEvent, ScenarioSpec


_INCIDENT_TYPES = {
    "ground_vehicle_failure",
    "gate_outage",
    "runway_closed",
    "storm",
    "medical_diversion",
}
_REPLAN_TYPES = {
    "automatic_replan",
    "automatic_policy_replan",
    "automatic_replan_requires_approval",
    "automatic_policy_replan_requires_approval",
    "automatic_replan_failed",
    "automatic_policy_replan_failed",
}


def _is_incident(event: ExecutionEvent) -> bool:
    return event.type in _INCIDENT_TYPES


def _is_replan(event: ExecutionEvent) -> bool:
    return event.type in _REPLAN_TYPES


def _label(event_type: str) -> str:
    return {
        "plan_approved": "人工批准",
        "execution_tick": "调度执行",
        "ground_vehicle_failure": "地服故障",
        "gate_outage": "机位故障",
        "runway_closed": "跑道关闭",
        "storm": "天气恶化",
        "medical_diversion": "医疗备降",
        "automatic_replan": "自动局部重规划",
        "automatic_policy_replan": "策略滚动重规划",
        "automatic_replan_requires_approval": "安全停机待批",
        "automatic_policy_replan_requires_approval": "安全停机待批",
        "automatic_replan_failed": "重规划不可行",
        "automatic_policy_replan_failed": "重规划不可行",
        "recovered": "运行恢复",
    }.get(event_type, event_type.replace("_", " "))




def _resource_event_type(event: Any) -> str:
    value = getattr(event, "type", "")
    return str(getattr(value, "value", value))


def _resource_event_minute(event: Any) -> float:
    value = getattr(event, "sim_minute", None)
    if value is None:
        value = getattr(event, "minute", 0.0)
    return float(value)


def _resource_event_runway(event: Any) -> str:
    payload = getattr(event, "payload", {}) or {}
    return str(
        getattr(event, "resource_id", None)
        or payload.get("resource_id")
        or payload.get("runway")
        or ""
    )


def _resource_event_duration(event: Any) -> float:
    value = getattr(event, "duration_min", None)
    if value is None:
        value = (getattr(event, "payload", {}) or {}).get("duration_min", 0.0)
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _runway_closure_windows(
    scenario: ScenarioSpec,
    observed_events: Iterable[ExecutionEvent],
) -> dict[str, list[dict[str, float | None]]]:
    """Reconstruct half-open [closed, reopened) windows from event sourcing."""
    runway_by_id = {item.id: item for item in scenario.runways}
    raw: dict[str, list[list[float | None]]] = {
        runway_id: [] for runway_id in runway_by_id
    }

    def append_closure(event: Any) -> None:
        runway_id = _resource_event_runway(event)
        if runway_id not in raw:
            return
        start = _resource_event_minute(event)
        duration = _resource_event_duration(event)
        runway = runway_by_id[runway_id]
        end = start + duration if duration > 0 else runway.reopen_minute
        normalized_end = float(end) if end is not None else None
        if any(
            float(window[0]) == start and window[1] == normalized_end
            for window in raw[runway_id]
        ):
            return
        raw[runway_id].append([start, normalized_end])

    ordered = sorted(
        [*scenario.events, *list(observed_events)],
        key=lambda item: (_resource_event_minute(item), getattr(item, "sequence", 0)),
    )
    for event in ordered:
        event_type = _resource_event_type(event)
        runway_id = _resource_event_runway(event)
        if event_type == "runway_closed":
            append_closure(event)
        elif event_type == "runway_reopened" and runway_id in raw:
            reopened = _resource_event_minute(event)
            eligible = [
                window
                for window in raw[runway_id]
                if float(window[0]) <= reopened
                and (window[1] is None or reopened <= float(window[1]))
            ]
            if eligible:
                latest = max(eligible, key=lambda window: float(window[0]))
                latest[1] = reopened

    for runway in scenario.runways:
        if runway.status.value == "closed" and not raw[runway.id]:
            raw[runway.id].append([
                float(scenario.current_minute),
                (
                    float(runway.reopen_minute)
                    if runway.reopen_minute is not None
                    else None
                ),
            ])

    merged: dict[str, list[dict[str, float | None]]] = {}
    for runway_id, windows in raw.items():
        compact: list[list[float | None]] = []
        for start, end in sorted(
            windows,
            key=lambda item: (float(item[0]), float("inf") if item[1] is None else float(item[1])),
        ):
            if not compact:
                compact.append([start, end])
                continue
            previous = compact[-1]
            previous_end = previous[1]
            if previous_end is None or float(start) <= float(previous_end):
                if previous_end is None or end is None:
                    previous[1] = None
                else:
                    previous[1] = max(float(previous_end), float(end))
            else:
                compact.append([start, end])
        merged[runway_id] = [
            {"closed_from": float(start), "reopened_at": end}
            for start, end in compact
        ]
    return merged



def _fuel_clock_contract(
    events: Iterable[ExecutionEvent],
) -> dict[str, Any]:
    """Verify conservation of remaining fuel time across event-time advances."""
    event_rows: list[dict[str, Any]] = []
    violations: list[str] = []
    checked_flights = 0

    for event in events:
        nested = event.payload.get("payload", {})
        clock = (
            nested.get("fuel_clock")
            if isinstance(nested, dict)
            else None
        ) or event.payload.get("fuel_clock")
        if not isinstance(clock, dict):
            continue

        event_reasons: list[str] = []
        try:
            from_minute = int(clock["from_minute"])
            to_minute = int(clock["to_minute"])
            elapsed = int(clock["elapsed_min"])
            updates = list(clock.get("updates", []))
        except (KeyError, TypeError, ValueError):
            violations.append(f"T+{event.sim_minute:g}: malformed fuel-clock evidence")
            event_rows.append({
                "event_type": event.type,
                "minute": event.sim_minute,
                "checked_flights": 0,
                "passed": False,
                "reasons": ["malformed fuel-clock evidence"],
            })
            continue

        if elapsed < 0 or to_minute - from_minute != elapsed:
            event_reasons.append("event-time delta does not match elapsed fuel time")

        declared_expired = {
            str(item) for item in clock.get("expired_flight_ids", [])
        }
        derived_expired: set[str] = set()
        for update in updates:
            checked_flights += 1
            try:
                flight_id = str(update["flight_id"])
                before = int(update["before_min"])
                after = int(update["after_min"])
                deadline = int(update["absolute_deadline_min"])
            except (KeyError, TypeError, ValueError):
                event_reasons.append("malformed per-flight fuel evidence")
                continue

            expected_after = max(0, before - elapsed)
            if after != expected_after:
                event_reasons.append(
                    f"{flight_id}: remaining fuel changed {before}→{after}, "
                    f"expected {expected_after}"
                )
            expected_deadline = from_minute + before
            if deadline != expected_deadline:
                event_reasons.append(
                    f"{flight_id}: recorded deadline {deadline} != {expected_deadline}"
                )
            if before < elapsed:
                derived_expired.add(flight_id)
                if str(update.get("status", "")) != "diverted":
                    event_reasons.append(
                        f"{flight_id}: overdue fuel clock was not diverted"
                    )
            elif to_minute + after != deadline:
                event_reasons.append(
                    f"{flight_id}: absolute fuel deadline moved"
                )

        if declared_expired != derived_expired:
            event_reasons.append("declared diverted flights do not match fuel arithmetic")
        if int(clock.get("updated_flights", -1)) != len(updates):
            event_reasons.append("updated-flight count does not match evidence rows")

        if event_reasons:
            violations.extend(
                f"T+{event.sim_minute:g}: {reason}" for reason in event_reasons
            )
        event_rows.append({
            "event_type": event.type,
            "minute": event.sim_minute,
            "elapsed_min": elapsed,
            "checked_flights": len(updates),
            "expired_flight_ids": sorted(derived_expired),
            "passed": not event_reasons,
            "reasons": event_reasons,
        })

    return {
        "method": "event-time fuel conservation invariant + fail-safe diversion",
        "observed_events": len(event_rows),
        "checked_flights": checked_flights,
        "passed": not violations,
        "events": event_rows,
        "violations": violations,
        "summary": (
            f"{len(event_rows)} 次时钟推进、{checked_flights} 架次燃油余量守恒；"
            "越过截止的航班只允许安全备降"
            if not violations
            else f"燃油时钟检测到 {len(violations)} 条不一致"
        ),
    }


def _execution_envelope(
    motions: Iterable[ExecutionEvent],
    replans: Iterable[ExecutionEvent],
    observed_events: Iterable[ExecutionEvent],
    scenario: ScenarioSpec,
) -> dict[str, Any]:
    """Re-check observable motion against the live resource-time envelope."""
    motion_list = list(motions)
    replan_list = list(replans)
    event_list = list(observed_events)
    runway_by_id = {item.id: item for item in scenario.runways}
    gate_ids = {item.id for item in scenario.gates}
    flight_by_id = {item.id: item for item in scenario.flights}
    runway_windows = _runway_closure_windows(scenario, event_list)
    approved_plan_ids = {
        str(item.payload.get("plan_id"))
        for item in event_list
        if item.type == "plan_approved" and item.payload.get("plan_id")
    }
    provenance_enabled = any(item.payload.get("plan_id") for item in motion_list)
    seen_flights: set[str] = set()
    rows: list[dict[str, Any]] = []
    violations: list[str] = []

    for event in motion_list:
        flight_id = str(event.payload.get("flight_id", ""))
        runway_id = str(event.payload.get("runway", ""))
        gate_id = str(event.payload.get("gate", ""))
        plan_id = str(event.payload.get("plan_id", ""))
        revision = str(event.payload.get("schedule_revision", ""))
        reasons: list[str] = []

        if not flight_id or flight_id not in flight_by_id:
            reasons.append("unknown flight")
        elif flight_id in seen_flights:
            reasons.append("duplicate execution")
        seen_flights.add(flight_id)

        runway = runway_by_id.get(runway_id)
        if runway is None:
            reasons.append("unknown runway")
        else:
            blocked_window = next(
                (
                    window
                    for window in runway_windows.get(runway_id, [])
                    if float(window["closed_from"]) <= event.sim_minute
                    and (
                        window["reopened_at"] is None
                        or event.sim_minute < float(window["reopened_at"])
                    )
                ),
                None,
            )
            if blocked_window is not None:
                closed_from = float(blocked_window["closed_from"])
                reopened_at = blocked_window["reopened_at"]
                if reopened_at is None:
                    window_label = f"closed from T+{closed_from:g} with no reopening ETA"
                elif closed_from <= 0:
                    window_label = f"unavailable until T+{float(reopened_at):g}"
                else:
                    window_label = (
                        f"closed T+{closed_from:g}–T+{float(reopened_at):g}"
                    )
                reasons.append(f"{runway_id} {window_label}")

        if gate_id not in gate_ids:
            reasons.append("unknown gate")
        flight = flight_by_id.get(flight_id)
        if (
            flight is not None
            and event.sim_minute > scenario.current_minute + flight.fuel_margin_min
        ):
            reasons.append("fuel deadline exceeded")

        if provenance_enabled and (not plan_id or not revision):
            reasons.append("missing schedule provenance")
        if revision == "approved" and plan_id not in approved_plan_ids:
            reasons.append("action does not belong to an approved plan")
        elif revision == "automatic_replan":
            matching_switch = any(
                item.sim_minute <= event.sim_minute
                and str(item.payload.get("selected_plan_id", "")) == plan_id
                and item.payload.get("switch_executed") is not False
                and item.payload.get("safety_gate") == "passed"
                for item in replan_list
            )
            if not matching_switch:
                reasons.append("revised action lacks a passed plan switch")

        if reasons:
            violations.extend(f"{flight_id or 'unknown'}: {reason}" for reason in reasons)
        rows.append({
            "flight_id": flight_id,
            "minute": event.sim_minute,
            "runway": runway_id,
            "gate": gate_id,
            "plan_id": plan_id or None,
            "schedule_revision": revision or None,
            "passed": not reasons,
            "reasons": reasons,
        })

    for runway in scenario.runways:
        sequence = sorted(
            (item for item in motion_list if item.payload.get("runway") == runway.id),
            key=lambda item: (item.sim_minute, item.sequence),
        )
        for left, right in zip(sequence, sequence[1:]):
            spacing = right.sim_minute - left.sim_minute
            if spacing < runway.separation_min:
                violations.append(
                    f"{runway.id}: {left.payload.get('flight_id')}/"
                    f"{right.payload.get('flight_id')} separation {spacing:g}m "
                    f"< {runway.separation_min}m"
                )

    return {
        "method": "observable resource-time envelope + schedule provenance",
        "passed": not violations,
        "checked_actions": len(motion_list),
        "runway_windows": runway_windows,
        "checks": rows,
        "violations": violations,
        "summary": (
            f"{len(motion_list)} 条执行动作均满足跑道、燃油、间隔、去重与版本来源约束"
            if not violations
            else f"检测到 {len(violations)} 条运行期动作违约"
        ),
    }


def verify_runtime_contract(
    events: Iterable[ExecutionEvent],
    *,
    target_phase: str,
    recovery_minute: float | None = None,
    maximum_response_min: float = 2.0,
    scenario: ScenarioSpec | None = None,
) -> dict[str, Any]:
    """Verify authorization, response-time, gate, and recovery-order rules."""
    observed = list(events)
    chronological = sorted(observed, key=lambda item: (item.sim_minute, item.sequence))
    approvals = [item for item in chronological if item.type == "plan_approved"]
    motions = [item for item in chronological if item.type == "execution_tick"]
    incidents = [item for item in chronological if _is_incident(item)]
    replans = [item for item in chronological if _is_replan(item)]

    approval_minute = min((item.sim_minute for item in approvals), default=None)
    authorization_passed = approval_minute is not None and all(
        item.sim_minute >= approval_minute for item in motions
    )
    authorization_evidence = (
        f"T+{approval_minute:g} 已批准；随后下发 {len(motions)} 条航班动作"
        if authorization_passed and approval_minute is not None
        else "发现未经批准的执行动作或缺少批准事件"
    )

    response_rows: list[dict[str, Any]] = []
    for incident in incidents:
        incident_id = str(incident.payload.get("id", ""))
        policy = incident.payload.get("payload", {}).get("policy_decision", {})
        matching = [
            item for item in replans
            if item.sim_minute >= incident.sim_minute
            and (
                not incident_id
                or str(item.payload.get("trigger", "")) == incident_id
            )
        ]
        if matching:
            response = matching[0]
            latency = round(float(response.sim_minute - incident.sim_minute), 2)
            response_rows.append({
                "incident_type": incident.type,
                "incident_minute": incident.sim_minute,
                "response_type": response.type,
                "response_minute": response.sim_minute,
                "latency_min": latency,
                "passed": latency <= maximum_response_min,
            })
        elif policy.get("action") == "continue_and_monitor":
            response_rows.append({
                "incident_type": incident.type,
                "incident_minute": incident.sim_minute,
                "response_type": "continue_and_monitor",
                "response_minute": incident.sim_minute,
                "latency_min": 0.0,
                "passed": True,
            })
        else:
            response_rows.append({
                "incident_type": incident.type,
                "incident_minute": incident.sim_minute,
                "response_type": "missing",
                "response_minute": None,
                "latency_min": None,
                "passed": False,
            })
    response_passed = all(item["passed"] for item in response_rows)
    worst_latency = max(
        (
            float(item["latency_min"])
            for item in response_rows
            if item["latency_min"] is not None
        ),
        default=None,
    )
    response_evidence = (
        "本次执行没有运行期事故，响应时限规则不触发"
        if not incidents
        else (
            f"{len(response_rows)}/{len(incidents)} 个事故已在 "
            f"{worst_latency:g} 分钟内完成策略响应"
            if response_passed and worst_latency is not None
            else f"{sum(1 for item in response_rows if item['passed'])}/{len(incidents)} 个事故满足响应时限"
        )
    )

    gate_rows: list[dict[str, Any]] = []
    for event in replans:
        gate = str(event.payload.get("safety_gate", "stopped"))
        safe_stop = event.type.endswith("requires_approval") and gate == "human_required"
        passed = gate == "passed" or safe_stop
        gate_rows.append({
            "event_type": event.type,
            "minute": event.sim_minute,
            "safety_gate": gate,
            "safe_stop": safe_stop,
            "passed": passed,
        })
    safety_gate_passed = all(item["passed"] for item in gate_rows)
    if not replans:
        safety_evidence = "没有方案切换，安全闸门规则不触发"
    elif safety_gate_passed:
        stopped = sum(1 for item in gate_rows if item["safe_stop"])
        safety_evidence = (
            f"{len(gate_rows)} 次方案切换均通过安全闸门"
            if not stopped
            else f"{stopped} 次风险升级已安全停机并转人工审批"
        )
    else:
        safety_evidence = "存在未通过安全闸门的方案切换"

    execution_envelope = (
        _execution_envelope(motions, replans, chronological, scenario)
        if scenario is not None
        else None
    )
    fuel_clock_contract = _fuel_clock_contract(chronological)

    recovery_requested = target_phase == "recovered"
    unresolved = [
        item for item in response_rows
        if not item["passed"] or item["response_type"].endswith("failed")
    ]
    safe_stops = [item for item in gate_rows if item["safe_stop"]]
    last_required_minute = max(
        [item.sim_minute for item in incidents + replans] + [approval_minute or 0.0]
    )
    recovery_passed = (
        (not recovery_requested)
        or (
            recovery_minute is not None
            and recovery_minute >= last_required_minute
            and not unresolved
            and not safe_stops
        )
    )
    recovery_evidence = (
        f"T+{recovery_minute:g} 在全部事故响应与安全闸门之后宣布恢复"
        if recovery_requested and recovery_passed and recovery_minute is not None
        else (
            "系统保持安全停机/监控状态，没有错误宣布恢复"
            if not recovery_requested
            else "仍有未解决事故或人工审批要求，不能宣布恢复"
        )
    )

    rules = [
        {
            "id": "authorization_before_motion",
            "label": "先授权，后执行",
            "formula": "APPROVED precedes every EXECUTION_TICK",
            "passed": authorization_passed,
            "evidence": authorization_evidence,
        },
        {
            "id": "bounded_incident_response",
            "label": f"事故后 {maximum_response_min:g} 分钟内响应",
            "formula": f"INCIDENT → F≤{maximum_response_min:g} REPLAN_OR_MONITOR",
            "passed": response_passed,
            "evidence": response_evidence,
        },
        {
            "id": "safety_gate_before_switch",
            "label": "安全闸门先于方案切换",
            "formula": "REPLAN → SAFETY_PASS ∨ SAFE_STOP",
            "passed": safety_gate_passed,
            "evidence": safety_evidence,
        },
        {
            "id": "resolution_before_recovery",
            "label": "先解决事件，后宣布恢复",
            "formula": "RECOVERED → no UNRESOLVED_INCIDENT",
            "passed": recovery_passed,
            "evidence": recovery_evidence,
        },
    ]
    if execution_envelope is not None:
        rules.insert(1, {
            "id": "execution_resource_envelope",
            "label": "执行动作处于资源安全包络",
            "formula": "MOTION → RUNWAY_OPEN ∧ FUEL_SAFE ∧ SEPARATED ∧ VERSIONED",
            "passed": execution_envelope["passed"],
            "evidence": execution_envelope["summary"],
        })
    if fuel_clock_contract["observed_events"]:
        rules.insert(2 if execution_envelope is not None else 1, {
            "id": "fuel_clock_conservation",
            "label": "燃油时钟守恒或安全备降",
            "formula": "FUEL(t+Δ)=max(0,FUEL(t)−Δ) ∧ OVERDUE → DIVERTED",
            "passed": fuel_clock_contract["passed"],
            "evidence": fuel_clock_contract["summary"],
        })

    passed_count = sum(1 for item in rules if item["passed"])
    if passed_count != len(rules):
        status = "failed"
    elif safe_stops or target_phase in {"awaiting_approval", "infeasible"}:
        status = "safe_stop"
    else:
        status = "passed"

    timeline = [
        {
            "type": item.type,
            "label": _label(item.type),
            "minute": item.sim_minute,
            "sequence": item.sequence,
        }
        for item in chronological
        if item.type == "plan_approved" or _is_incident(item) or _is_replan(item)
    ]
    if recovery_minute is not None:
        timeline.append({
            "type": "recovered",
            "label": _label("recovered"),
            "minute": recovery_minute,
            "sequence": max((item.sequence for item in observed), default=0) + 1,
        })
    timeline.sort(key=lambda item: (float(item["minute"]), int(item["sequence"])))

    return {
        "method": (
            "bounded event-time runtime contract monitor + "
            "observable execution resource envelope"
            if execution_envelope is not None
            else "bounded event-time runtime contract monitor"
        ),
        "status": status,
        "target_phase": target_phase,
        "passed": passed_count,
        "total": len(rules),
        "maximum_response_min": maximum_response_min,
        "observed_worst_response_min": worst_latency,
        "rules": rules,
        "responses": response_rows,
        "safety_gates": gate_rows,
        "execution_envelope": execution_envelope,
        "fuel_clock": fuel_clock_contract,
        "timeline": timeline,
        "summary": (
            f"运行时契约 {passed_count}/{len(rules)} 条通过"
            + (
                f"；最慢事故响应 {worst_latency:g} 分钟"
                if worst_latency is not None
                else "；本轮没有运行期事故"
            )
        ),
        "caveat": (
            "该监视器验证事件日志中的授权、资源时间包络、燃油截止、"
            "航班间隔、调度版本、响应时限和安全闸门顺序；"
            "仍不替代 ATC、适航认证或现场运行授权。"
        ),
    }
