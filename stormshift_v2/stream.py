"""WebSocket fan-out with reconnect-safe event sequence numbers."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class StreamManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[session_id].add(websocket)

    async def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections[session_id].discard(websocket)
            if not self._connections[session_id]:
                self._connections.pop(session_id, None)

    async def publish(self, session_id: str, message_type: str, payload: Any) -> None:
        async with self._lock:
            targets = list(self._connections.get(session_id, ()))
        stale: list[WebSocket] = []
        message = {"type": message_type, "payload": payload}
        for websocket in targets:
            try:
                await websocket.send_json(message)
            except Exception:
                stale.append(websocket)
        if stale:
            async with self._lock:
                for websocket in stale:
                    self._connections[session_id].discard(websocket)

    async def publish_state(self, state) -> None:
        await self.publish(
            state.id,
            "session.state",
            state.model_dump(mode="json"),
        )


stream_manager = StreamManager()
