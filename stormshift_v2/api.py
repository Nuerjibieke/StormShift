"""FastAPI surface for StormShift V2 plus the V1 compatibility endpoints."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from stormshift.agents import negotiate as legacy_negotiate
from stormshift.scenario import get_scenario as legacy_scenario

from .config import settings
from .branch_preview import BranchPreviewRequest
from .llm import model_health
from .scenarios import scenario_catalog
from .schemas import (
    ApprovalResponse,
    EventRequest,
    ExecutionAdvanceRequest,
    ExecutionAdvanceResponse,
    HealthStatus,
    IntentConfirmation,
    IntentRequest,
    IntentClarificationRequest,
    ObservationRequest,
    PlanRequest,
    PreferenceRequest,
    SandboxUpdate,
    SessionCreate,
    SessionState,
)
from .service import (
    SessionNotFound,
    StaleExecution,
    UnsafeExecution,
    UnsafePlan,
    service,
)
from .stream import stream_manager
from .voice import transcribe_bytes


app = FastAPI(
    title="StormShift V2",
    version="2.0.0",
    description="Interactive airport recovery exercise twin. Not for operational aviation decisions.",
)
app.add_middleware(GZipMiddleware, minimum_size=900)


@app.exception_handler(SessionNotFound)
async def session_not_found(_, exc: SessionNotFound):
    return JSONResponse(status_code=404, content={"detail": f"session not found: {exc.args[0]}"})


@app.get("/api/health")
async def legacy_health() -> dict[str, str]:
    return {"status": "ok", "service": "stormshift-v2"}


@app.get("/api/v2/health", response_model=HealthStatus)
async def health() -> HealthStatus:
    models = await model_health()
    components = {
        "api": "ready",
        "sqlite": "ready",
        "optimizer": "ready",
        "fast_model": models["fast"],
        "deep_model": models["deep"],
        "voice": "ready" if settings.voice_mode != "off" else "disabled",
    }
    degraded = any(value not in {"ready", "disabled"} for value in components.values())
    return HealthStatus(status="degraded" if degraded else "ok", components=components)


@app.get("/api/scenario")
async def get_legacy_scenario() -> dict[str, Any]:
    return legacy_scenario()


@app.post("/api/negotiate")
async def negotiate_compatibility(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return legacy_negotiate(legacy_scenario(), str(payload.get("preference", "balanced")))
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v2/scenarios")
async def list_scenarios() -> list[dict[str, str]]:
    return scenario_catalog()

@app.delete("/api/v2/preferences")
async def reset_preference_memory() -> dict[str, Any]:
    return await service.reset_preference_memory()



@app.post("/api/v2/sessions", response_model=SessionState, status_code=201)
async def create_session(request: SessionCreate) -> SessionState:
    return await service.create(request)


@app.get("/api/v2/sessions", response_model=list[SessionState])
async def list_sessions(limit: int = Query(20, ge=1, le=100)) -> list[SessionState]:
    return service.store.list_sessions(limit)


@app.get("/api/v2/sessions/{session_id}", response_model=SessionState)
async def get_session(session_id: str) -> SessionState:
    return service.require(session_id)


@app.post("/api/v2/sessions/{session_id}/previews")
async def preview_branches(session_id: str, request: BranchPreviewRequest) -> dict[str, Any]:
    try:
        return await service.preview_branches(session_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/v2/sessions/{session_id}/previews/{preview_id}")
async def get_branch_preview(session_id: str, preview_id: str) -> dict[str, Any]:
    # SessionNotFound is a KeyError subclass; preserve its session-specific handler.
    service.require(session_id)
    try:
        return service.get_branch_preview(session_id, preview_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v2/sessions/{session_id}/clone", response_model=SessionState, status_code=201)
async def clone_session(session_id: str) -> SessionState:
    return await service.clone(session_id)


@app.post("/api/v2/sessions/{session_id}/events", response_model=SessionState)
async def inject_event(session_id: str, request: EventRequest) -> SessionState:
    try:
        return await service.inject_event(session_id, request)
    except (StaleExecution, UnsafeExecution) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.patch("/api/v2/sessions/{session_id}/sandbox", response_model=SessionState)
async def update_sandbox(session_id: str, request: SandboxUpdate) -> SessionState:
    try:
        return await service.update_sandbox(session_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v2/sessions/{session_id}/intent", response_model=SessionState)
async def submit_intent(session_id: str, request: IntentRequest) -> SessionState:
    return await service.submit_intent(session_id, request)


@app.post("/api/v2/sessions/{session_id}/intent/clarify", response_model=SessionState)
async def clarify_intent(session_id: str, request: IntentClarificationRequest) -> SessionState:
    try:
        return await service.clarify_intent(session_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v2/sessions/{session_id}/intent/confirm", response_model=SessionState)
async def confirm_intent(session_id: str, request: IntentConfirmation) -> SessionState:
    try:
        return await service.confirm_intent(session_id, request.approved)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v2/sessions/{session_id}/observations", response_model=SessionState)
async def acquire_observation(session_id: str, request: ObservationRequest) -> SessionState:
    try:
        return await service.acquire_observation(session_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v2/sessions/{session_id}/preferences", response_model=SessionState)
async def submit_preference(
    session_id: str,
    request: PreferenceRequest,
) -> SessionState:
    try:
        return await service.submit_preference(session_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v2/sessions/{session_id}/plans", response_model=SessionState)
async def generate_plans(session_id: str, request: PlanRequest) -> SessionState:
    try:
        return await service.plan(session_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/v2/sessions/{session_id}/plans/{plan_id}/flights/{flight_id}/explanation")
async def explain_flight(
    session_id: str,
    plan_id: str,
    flight_id: str,
) -> dict[str, Any]:
    try:
        return await service.explain_flight(session_id, plan_id, flight_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"plan or flight not found: {exc.args[0]}",
        ) from exc


@app.post(
    "/api/v2/sessions/{session_id}/plans/{plan_id}/approve",
    response_model=ApprovalResponse,
)
async def approve_plan(session_id: str, plan_id: str) -> ApprovalResponse:
    try:
        return await service.approve(session_id, plan_id)
    except UnsafePlan as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"plan not found: {exc.args[0]}") from exc


@app.post(
    "/api/v2/sessions/{session_id}/execution/advance",
    response_model=ExecutionAdvanceResponse,
)
async def advance_execution(
    session_id: str,
    request: ExecutionAdvanceRequest,
) -> ExecutionAdvanceResponse:
    try:
        return await service.advance_execution(session_id, request)
    except (StaleExecution, UnsafeExecution, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/v2/sessions/{session_id}/replay")
async def replay(
    session_id: str,
    after_sequence: int = Query(0, ge=0),
) -> dict[str, Any]:
    events = service.replay(session_id, after_sequence)
    return {
        "session_id": session_id,
        "after_sequence": after_sequence,
        "events": [event.model_dump(mode="json") for event in events],
    }


@app.get("/api/v2/sessions/{session_id}/integrity")
async def event_integrity(session_id: str) -> dict[str, Any]:
    service.require(session_id)
    return service.store.verify_event_chain(session_id)



@app.post("/api/v2/audio/transcribe")
async def transcribe(file: UploadFile = File(...)) -> dict[str, Any]:
    if settings.voice_mode.lower() in {"off", "false", "0"}:
        raise HTTPException(status_code=503, detail="语音服务已禁用，请使用文字输入")
    try:
        content = await file.read(12 * 1024 * 1024 + 1)
        return await transcribe_bytes(content, file.filename or "speech.webm")
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="语音模型尚未安装，请使用文字输入") from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=f"转写失败：{exc}") from exc


@app.websocket("/api/v2/sessions/{session_id}/stream")
async def websocket_stream(
    websocket: WebSocket,
    session_id: str,
    after_sequence: int = Query(0, ge=0),
) -> None:
    try:
        state = service.require(session_id)
    except SessionNotFound:
        await websocket.close(code=4404, reason="session not found")
        return
    await stream_manager.connect(session_id, websocket)
    try:
        await websocket.send_json({
            "type": "session.state",
            "payload": state.model_dump(mode="json"),
        })
        replay_events = service.replay(session_id, after_sequence)
        if replay_events:
            await websocket.send_json({
                "type": "replay.events",
                "payload": [event.model_dump(mode="json") for event in replay_events],
            })
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=25)
                if message == "ping":
                    await websocket.send_json({"type": "pong", "payload": {}})
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "heartbeat", "payload": {}})
    except WebSocketDisconnect:
        pass
    finally:
        await stream_manager.disconnect(session_id, websocket)


def mount_frontend(application: FastAPI, root: Path | None = None) -> None:
    static_root = root or (
        settings.frontend_dist if (settings.frontend_dist / "index.html").exists() else settings.legacy_web
    )
    application.mount("/", StaticFiles(directory=str(static_root), html=True), name="frontend")


mount_frontend(app)
