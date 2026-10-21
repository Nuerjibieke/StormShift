"""SQLite event store for sessions, plans, replay, and cached live data."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schemas import ExecutionEvent, PlanCandidate, SessionState


GENESIS_HASH = "0" * 64


def _event_digest(
    session_id: str,
    sequence: int,
    sim_minute: float,
    event_type: str,
    payload: dict[str, Any],
    created_at: str,
    prev_hash: str,
) -> str:
    material = json.dumps(
        {
            "session_id": session_id,
            "sequence": sequence,
            "sim_minute": float(sim_minute),
            "type": event_type,
            "payload": payload,
            "created_at": created_at,
            "prev_hash": prev_hash,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class SessionStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    state_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    sim_minute REAL NOT NULL,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    prev_hash TEXT,
                    event_hash TEXT,
                    PRIMARY KEY(session_id, sequence),
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS plans (
                    session_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    approved INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, plan_id),
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS data_cache (
                    cache_key TEXT PRIMARY KEY,
                    expires_at REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS preference_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    scenario_id TEXT NOT NULL,
                    winner_vector_json TEXT NOT NULL,
                    loser_vector_json TEXT NOT NULL,
                    winner_title TEXT NOT NULL,
                    loser_title TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS preference_active_idx
                    ON preference_observations(revoked, id DESC);
                """
            )
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(events)")
            }
            for name in ("prev_hash", "event_hash"):
                if name not in columns:
                    db.execute(f"ALTER TABLE events ADD COLUMN {name} TEXT")
            self._backfill_event_hashes(db)

    def _backfill_event_hashes(self, db: sqlite3.Connection) -> None:
        """Hash legacy rows once without repairing already-hashed evidence."""
        session_rows = db.execute(
            "SELECT DISTINCT session_id FROM events ORDER BY session_id"
        ).fetchall()
        for session_row in session_rows:
            session_id = str(session_row["session_id"])
            rows = db.execute(
                """
                SELECT sequence, sim_minute, type, payload_json, created_at,
                       prev_hash, event_hash
                FROM events
                WHERE session_id=?
                ORDER BY sequence
                """,
                (session_id,),
            ).fetchall()
            previous = GENESIS_HASH
            for row in rows:
                stored_previous = str(row["prev_hash"] or "")
                stored_hash = str(row["event_hash"] or "")
                if stored_previous and stored_hash:
                    previous = stored_hash
                    continue
                payload = json.loads(row["payload_json"])
                digest = _event_digest(
                    session_id,
                    int(row["sequence"]),
                    float(row["sim_minute"]),
                    str(row["type"]),
                    payload,
                    str(row["created_at"]),
                    previous,
                )
                db.execute(
                    """
                    UPDATE events
                    SET prev_hash=?, event_hash=?
                    WHERE session_id=? AND sequence=?
                    """,
                    (previous, digest, session_id, int(row["sequence"])),
                )
                previous = digest

    def save_session(self, state: SessionState) -> SessionState:
        state.updated_at = datetime.now(timezone.utc)
        payload = state.model_dump_json()
        with self._lock, self._connect() as db:
            db.execute(
                """
                INSERT INTO sessions(id, created_at, updated_at, state_json)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  updated_at=excluded.updated_at,
                  state_json=excluded.state_json
                """,
                (state.id, state.created_at.isoformat(), state.updated_at.isoformat(), payload),
            )
        return state

    def get_session(self, session_id: str) -> SessionState | None:
        with self._connect() as db:
            row = db.execute("SELECT state_json FROM sessions WHERE id=?", (session_id,)).fetchone()
        return SessionState.model_validate_json(row["state_json"]) if row else None

    def list_sessions(self, limit: int = 20) -> list[SessionState]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT state_json FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [SessionState.model_validate_json(row["state_json"]) for row in rows]

    def append_event(
        self,
        session_id: str,
        sim_minute: float,
        event_type: str,
        payload: dict[str, Any],
    ) -> ExecutionEvent:
        with self._lock, self._connect() as db:
            row = db.execute(
                """
                SELECT sequence, event_hash
                FROM events
                WHERE session_id=?
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            sequence = int(row["sequence"]) + 1 if row else 1
            previous = str(row["event_hash"] or GENESIS_HASH) if row else GENESIS_HASH
            event = ExecutionEvent(
                sequence=sequence,
                session_id=session_id,
                sim_minute=sim_minute,
                type=event_type,
                payload=payload,
                prev_hash=previous,
            )
            created_at = event.created_at.isoformat()
            event.event_hash = _event_digest(
                session_id,
                sequence,
                float(sim_minute),
                event_type,
                payload,
                created_at,
                previous,
            )
            db.execute(
                """
                INSERT INTO events(
                    session_id, sequence, sim_minute, type, payload_json,
                    created_at, prev_hash, event_hash
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    sequence,
                    sim_minute,
                    event_type,
                    json.dumps(payload, ensure_ascii=False),
                    created_at,
                    previous,
                    event.event_hash,
                ),
            )
        return event

    def commit_execution_batch(
        self,
        state: SessionState,
        events: list[ExecutionEvent],
        *,
        expected_previous_sequence: int,
    ) -> list[ExecutionEvent]:
        """Atomically append execution facts and persist their resulting state."""
        now = datetime.now(timezone.utc)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT sequence, event_hash
                FROM events
                WHERE session_id=?
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (state.id,),
            ).fetchone()
            current_sequence = int(row["sequence"]) if row else 0
            previous = (
                str(row["event_hash"] or GENESIS_HASH)
                if row
                else GENESIS_HASH
            )
            if current_sequence != expected_previous_sequence:
                raise RuntimeError(
                    "event stream changed during execution transaction"
                )

            for offset, event in enumerate(events, start=1):
                event.sequence = current_sequence + offset
                event.prev_hash = previous
                created_at = event.created_at.isoformat()
                event.event_hash = _event_digest(
                    state.id,
                    event.sequence,
                    float(event.sim_minute),
                    event.type,
                    event.payload,
                    created_at,
                    previous,
                )
                db.execute(
                    """
                    INSERT INTO events(
                        session_id, sequence, sim_minute, type, payload_json,
                        created_at, prev_hash, event_hash
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.id,
                        event.sequence,
                        event.sim_minute,
                        event.type,
                        json.dumps(event.payload, ensure_ascii=False),
                        created_at,
                        event.prev_hash,
                        event.event_hash,
                    ),
                )
                previous = event.event_hash

            state.sequence = current_sequence + len(events)
            state.updated_at = now
            payload = state.model_dump_json()
            db.execute(
                """
                INSERT INTO sessions(id, created_at, updated_at, state_json)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  updated_at=excluded.updated_at,
                  state_json=excluded.state_json
                """,
                (
                    state.id,
                    state.created_at.isoformat(),
                    state.updated_at.isoformat(),
                    payload,
                ),
            )
        return events

    def replay(self, session_id: str, after_sequence: int = 0) -> list[ExecutionEvent]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT sequence, sim_minute, type, payload_json, created_at,
                       prev_hash, event_hash
                FROM events WHERE session_id=? AND sequence>?
                ORDER BY sequence
                """,
                (session_id, after_sequence),
            ).fetchall()
        return [
            ExecutionEvent(
                sequence=row["sequence"],
                session_id=session_id,
                sim_minute=row["sim_minute"],
                type=row["type"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
                prev_hash=str(row["prev_hash"] or ""),
                event_hash=str(row["event_hash"] or ""),
            )
            for row in rows
        ]

    def verify_event_chain(self, session_id: str) -> dict[str, Any]:
        """Verify every stored event against its predecessor and canonical body."""
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT sequence, sim_minute, type, payload_json, created_at,
                       prev_hash, event_hash
                FROM events
                WHERE session_id=?
                ORDER BY sequence
                """,
                (session_id,),
            ).fetchall()

        previous = GENESIS_HASH
        first_invalid: int | None = None
        links: list[dict[str, Any]] = []
        for row in rows:
            sequence = int(row["sequence"])
            payload = json.loads(row["payload_json"])
            stored_previous = str(row["prev_hash"] or "")
            stored_hash = str(row["event_hash"] or "")
            expected = _event_digest(
                session_id,
                sequence,
                float(row["sim_minute"]),
                str(row["type"]),
                payload,
                str(row["created_at"]),
                previous,
            )
            link_valid = (
                stored_previous == previous
                and stored_hash == expected
            )
            if not link_valid and first_invalid is None:
                first_invalid = sequence
            links.append({
                "sequence": sequence,
                "sim_minute": float(row["sim_minute"]),
                "type": str(row["type"]),
                "hash_prefix": stored_hash[:10],
                "valid": link_valid,
            })
            previous = stored_hash

        checkpoints: list[dict[str, Any]] = []
        for link in links:
            if checkpoints and checkpoints[-1]["type"] == link["type"]:
                checkpoints[-1] = {
                    **link,
                    "count": int(checkpoints[-1]["count"]) + 1,
                    "valid": bool(checkpoints[-1]["valid"] and link["valid"]),
                }
            else:
                checkpoints.append({**link, "count": 1})


        valid = first_invalid is None
        return {
            "method": "canonical-json + chained-sha256",
            "algorithm": "SHA-256",
            "status": "empty" if not rows else "verified" if valid else "broken",
            "valid": valid,
            "tamper_evident": True,
            "event_count": len(rows),
            "first_invalid_sequence": first_invalid,
            "head_hash": previous if rows else GENESIS_HASH,
            "genesis_hash": GENESIS_HASH,
            "links": checkpoints[-8:],
            "coverage": {
                "first_sequence": int(rows[0]["sequence"]) if rows else None,
                "last_sequence": int(rows[-1]["sequence"]) if rows else None,
            },
            "caveat": (
                "哈希链可检测事件内容修改、插入、重排和链中删除；"
                "检测尾部截断仍需把 HEAD 外部签名或锚定。它不能证明传感器输入真实，"
                "也不替代航空安全审计。"
            ),
        }

    def save_plan(self, session_id: str, plan: PlanCandidate, approved: bool = False) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                """
                INSERT INTO plans(session_id, plan_id, approved, payload_json, created_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(session_id, plan_id) DO UPDATE SET
                  approved=excluded.approved,
                  payload_json=excluded.payload_json
                """,
                (
                    session_id,
                    plan.id,
                    int(approved),
                    plan.model_dump_json(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def approve_plan(self, session_id: str, plan_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE plans SET approved=0 WHERE session_id=?", (session_id,))
            db.execute(
                "UPDATE plans SET approved=1 WHERE session_id=? AND plan_id=?",
                (session_id, plan_id),
            )

    def save_preference_observation(
        self,
        *,
        session_id: str,
        scenario_id: str,
        winner_vector: list[float],
        loser_vector: list[float],
        winner_title: str,
        loser_title: str,
        metadata: dict[str, Any],
    ) -> int:
        """Persist one explicit soft-objective comparison for future sessions."""
        with self._lock, self._connect() as db:
            cursor = db.execute(
                """
                INSERT INTO preference_observations(
                    session_id, scenario_id, winner_vector_json,
                    loser_vector_json, winner_title, loser_title,
                    metadata_json, created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    scenario_id,
                    json.dumps(winner_vector),
                    json.dumps(loser_vector),
                    winner_title,
                    loser_title,
                    json.dumps(metadata, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def list_preference_observations(
        self,
        *,
        limit: int = 64,
        exclude_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return active cross-session evidence; revoked rows are never learned."""
        query = """
            SELECT id, session_id, scenario_id, winner_vector_json,
                   loser_vector_json, winner_title, loser_title,
                   metadata_json, created_at
            FROM preference_observations
            WHERE revoked=0
        """
        params: list[Any] = []
        if exclude_session_id is not None:
            query += " AND session_id<>?"
            params.append(exclude_session_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(512, int(limit))))
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return [
            {
                "id": int(row["id"]),
                "session_id": str(row["session_id"]),
                "scenario_id": str(row["scenario_id"]),
                "winner_vector": json.loads(row["winner_vector_json"]),
                "loser_vector": json.loads(row["loser_vector_json"]),
                "winner_title": str(row["winner_title"]),
                "loser_title": str(row["loser_title"]),
                "metadata": json.loads(row["metadata_json"]),
                "created_at": str(row["created_at"]),
            }
            for row in reversed(rows)
        ]

    def revoke_all_preference_observations(self) -> int:
        """Revoke learned soft preferences without deleting the audit record."""
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "UPDATE preference_observations SET revoked=1 WHERE revoked=0"
            )
            return int(cursor.rowcount)

    def get_cache(self, key: str, now_epoch: float) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload_json, expires_at FROM data_cache WHERE cache_key=?", (key,)
            ).fetchone()
        if not row or row["expires_at"] < now_epoch:
            return None
        return json.loads(row["payload_json"])

    def set_cache(self, key: str, expires_at: float, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                """
                INSERT INTO data_cache(cache_key, expires_at, payload_json, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                  expires_at=excluded.expires_at,
                  payload_json=excluded.payload_json,
                  updated_at=excluded.updated_at
                """,
                (
                    key,
                    expires_at,
                    json.dumps(payload, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
