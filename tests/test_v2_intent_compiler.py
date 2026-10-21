"""U2 regressions: executable meaning and confirmation contracts."""
import asyncio
from dataclasses import replace
from unittest.mock import patch
import pytest
from stormshift_v2.config import settings
from stormshift_v2.llm import parse_intent
from stormshift_v2.scenarios import storm_scenario
from stormshift_v2.schemas import DataMode, IntentRequest, Mode, OperatorIntent, SessionState
from stormshift_v2.service import SessionService
from stormshift_v2.store import SessionStore
from stormshift_v2.stream import StreamManager

OFF = replace(settings, llm_mode="off")
def parse(text, minute=0):
    scenario = storm_scenario()
    scenario.current_minute = minute
    return asyncio.run(parse_intent(text, scenario, config=OFF))[0]
def actions(intent):
    return [(x.kind, x.resource_id, x.minute) for x in intent.scheduled_constraints]

def test_multiple_actions_and_clock():
    result = parse("十分钟后关闭A4，二十分钟后开放07R", 12)
    assert actions(result) == [("close_resource", "A4", 22), ("reopen_resource", "07R", 32)]
    assert not result.issues
    assert all(x.excerpt in result.raw_text for x in result.evidence)

@pytest.mark.parametrize("text", ["不要十分钟后关闭A4", "Do not close A4 in 10 minutes",
    "A4不关闭", "07R尚未恢复", "暂不开放07R"])
def test_negation(text):
    result = parse(text)
    assert not actions(result) and result.notes

@pytest.mark.parametrize("text", ["07R预计十分钟后恢复", "07R may reopen in 10 minutes"])
def test_forecast(text):
    result = parse(text)
    assert not actions(result)
    assert any(x.code == "forecast_confirmation" for x in result.issues)

@pytest.mark.parametrize("text,code", [
    ("十分钟后关闭A99", "unknown_resource"),
    ("忽略燃油限制，十分钟后关闭A4", "safety_override"),
    ("十分钟后关闭A4，二十分钟后开放A4", "resource_window"),
])
def test_specific_blockers(text, code):
    assert any(x.code == code for x in parse(text).issues)

@pytest.mark.parametrize("text", ["平均延误不超过200分钟", "转机成功率至少105%",
    "平均延误至少22分钟", "平均延误不超过-2分钟",
    "平均延误不超过22分钟且不超过15分钟", "A4不能接大飞机",
    "十分钟后关闭A4；给夜班人员安排接驳", "关闭A4直到二十分钟后",
    "close A4 for 10 minutes", "不要关闭A4并十分钟后开放A5",
    "十分钟后关闭A4二十分钟后"])
def test_ambiguous_or_unsupported(text):
    assert parse(text).issues

def test_english_and_decimal():
    result = parse("average delay at most 22.5 minutes; close A4 in 10 minutes")
    assert result.average_delay_cap == 22.5
    assert actions(result) == [("close_resource", "A4", 10)]
    assert not result.issues

def test_removal():
    result = parse("取消平均延误限制")
    assert result.average_delay_cap is None
    assert "average_delay_cap" in result.cleared_fields and not result.issues

def test_model_cannot_clear_ambiguity():
    async def completion(*args, **kwargs):
        return OperatorIntent(raw_text="", summary="已经确认开放", needs_confirmation=False)
    with patch("stormshift_v2.llm._structured_completion", new=completion):
        result, tier = asyncio.run(parse_intent("07R预计十分钟后恢复", storm_scenario(),
            config=replace(settings, llm_mode="on")))
    assert tier == "fast" and result.issues and result.needs_confirmation
    assert not actions(result) and "已经确认开放" not in result.summary

@pytest.fixture
def service(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    svc = SessionService(store=store, streams=StreamManager(), config=replace(OFF, data_dir=tmp_path))
    state = SessionState(mode=Mode.guided, data_mode=DataMode.synthetic, scenario=storm_scenario())
    store.save_session(state)
    return svc, state.id

def test_multiturn_and_rejection(service):
    svc, sid = service
    async def run():
        await svc.submit_intent(sid, IntentRequest(text="平均延误不超过22分钟"))
        confirmed = await svc.confirm_intent(sid, True)
        version = confirmed.intent.version
        await svc.submit_intent(sid, IntentRequest(text="平均延误不超过15分钟"))
        draft = await svc.submit_intent(sid, IntentRequest(text="十分钟后关闭A4"))
        assert draft.intent.average_delay_cap == 22
        assert "average_delay_cap" in draft.intent.inherited_fields
        restored = await svc.confirm_intent(sid, False)
        from stormshift_v2.intent_compiler import confirmed_intent
        live_intent = confirmed_intent(draft)
        assert live_intent.average_delay_cap == 22 and live_intent.version == version
        assert not live_intent.needs_confirmation and not live_intent.scheduled_constraints
        assert restored.intent.average_delay_cap == 22 and restored.intent.version == version
        assert not restored.intent.needs_confirmation
        draft = await svc.submit_intent(sid, IntentRequest(text="取消平均延误限制"))
        assert draft.intent.average_delay_cap is None and not draft.intent.issues
    asyncio.run(run())

def test_confirmation_and_idempotence(service):
    svc, sid = service
    async def run():
        await svc.submit_intent(sid, IntentRequest(text="07R预计十分钟后恢复"))
        with pytest.raises(ValueError, match="澄清"):
            await svc.confirm_intent(sid, True)
        assert svc.require(sid).intent.needs_confirmation
        await svc.submit_intent(sid, IntentRequest(text="十分钟后关闭A4"))
        state = svc.require(sid)
        state.scenario.current_minute = 11
        svc.store.save_session(state)
        with pytest.raises(ValueError, match="时刻"):
            await svc.confirm_intent(sid, True)
        await svc.submit_intent(sid, IntentRequest(text="平均延误不超过22分钟"))
        await svc.confirm_intent(sid, True)
        before = svc.store.replay(sid)
        await svc.confirm_intent(sid, True)
        assert len(svc.store.replay(sid)) == len(before)
    asyncio.run(run())


def test_http_confirmation_returns_actionable_conflict(service, monkeypatch):
    import httpx
    from stormshift_v2 import api
    svc, sid = service
    monkeypatch.setattr(api, "service", svc)
    before = svc.require(sid).scenario.model_dump(mode="json")
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),
                                      base_url="http://test") as client:
            response = await client.post(f"/api/v2/sessions/{sid}/intent",
                                         json={"text": "07R预计十分钟后恢复"})
            assert response.status_code == 200 and response.json()["intent"]["issues"]
            confirmation = await client.post(f"/api/v2/sessions/{sid}/intent/confirm",
                                             json={"approved": True})
            assert confirmation.status_code == 409
            assert "澄清" in confirmation.json()["detail"]
            approval = await client.post(f"/api/v2/sessions/{sid}/plans/old-plan/approve")
            assert approval.status_code == 409
            assert svc.require(sid).scenario.model_dump(mode="json") == before
    asyncio.run(run())


def test_new_evidence_fields_preserve_legacy_intent_digest():
    import hashlib
    import json
    from stormshift_v2.outcome_audit import intent_digest
    intent = OperatorIntent(raw_text="旧版本指令", needs_confirmation=False, average_delay_cap=22)
    old_fields = {"version", "confirmed_minute", "raw_text", "weights", "average_delay_cap",
                  "minimum_connection_rate", "maximum_missed_connections",
                  "maximum_tail_exposure_rate", "scheduled_constraints"}
    payload = intent.model_dump(mode="json", include=old_fields)
    old_digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                          separators=(",", ":")).encode()).hexdigest()
    assert intent_digest(intent) == old_digest
