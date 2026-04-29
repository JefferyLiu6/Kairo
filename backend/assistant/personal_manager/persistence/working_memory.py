"""Short-term structured dialogue state for the personal-manager typed workflow."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .control_store import pm_db_path

WORKING_MEMORY_MODES = {
    "awaiting_clarification",
    "awaiting_choice",
    "awaiting_freeform",
    "awaiting_confirmation",
    "awaiting_disambiguation",
}
WORKING_MEMORY_STATUSES = {"active", "resolved", "expired", "cancelled", "replaced"}

_MODE_TTL = {
    "awaiting_choice": timedelta(minutes=30),
    "awaiting_clarification": timedelta(hours=2),
    "awaiting_freeform": timedelta(hours=2),
    "awaiting_confirmation": timedelta(minutes=10),
    "awaiting_disambiguation": timedelta(minutes=10),
}
_MODE_STALE_AFTER = {
    "awaiting_choice": timedelta(minutes=10),
    "awaiting_clarification": timedelta(minutes=30),
    "awaiting_freeform": timedelta(minutes=30),
    "awaiting_confirmation": timedelta(minutes=5),
    "awaiting_disambiguation": timedelta(minutes=5),
}


@dataclass(frozen=True)
class WorkingMemoryRecord:
    id: str
    session_id: str
    mode: str
    source: str
    expected_reply: str
    status: str
    payload: dict[str, Any]
    summary: str
    created_at: str
    last_updated_at: str
    expires_at: str
    resolved_at: Optional[str]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _conn(session_id: str, data_dir: str, *, user_id: str = "") -> sqlite3.Connection:
    path = pm_db_path(session_id, data_dir, user_id=user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _json_loads(value: str) -> dict[str, Any]:
    try:
        loaded = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {"value": loaded}


def init_working_memory_store(session_id: str, data_dir: str, *, user_id: str = "") -> None:
    with _conn(session_id, data_dir, user_id=user_id) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS working_memory (
                id              TEXT PRIMARY KEY,
                session_id      TEXT NOT NULL,
                mode            TEXT NOT NULL,
                source          TEXT NOT NULL,
                expected_reply  TEXT NOT NULL,
                status          TEXT NOT NULL,
                payload_json    TEXT NOT NULL,
                summary         TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                last_updated_at TEXT NOT NULL,
                expires_at      TEXT NOT NULL,
                resolved_at     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_working_memory_session_status
                ON working_memory(session_id, status, last_updated_at DESC);
            """
        )


def _row_to_record(row: sqlite3.Row) -> WorkingMemoryRecord:
    return WorkingMemoryRecord(
        id=row["id"],
        session_id=row["session_id"],
        mode=row["mode"],
        source=row["source"],
        expected_reply=row["expected_reply"],
        status=row["status"],
        payload=_json_loads(row["payload_json"]),
        summary=row["summary"],
        created_at=row["created_at"],
        last_updated_at=row["last_updated_at"],
        expires_at=row["expires_at"],
        resolved_at=row["resolved_at"],
    )


def mode_ttl(mode: str) -> timedelta:
    return _MODE_TTL.get(mode, timedelta(minutes=30))


def mode_stale_after(mode: str) -> timedelta:
    return _MODE_STALE_AFTER.get(mode, timedelta(minutes=10))


def working_memory_is_stale(record: WorkingMemoryRecord, *, now: Optional[datetime] = None) -> bool:
    parsed = _parse_iso(record.last_updated_at)
    if parsed is None:
        return False
    current = now or datetime.now(timezone.utc)
    return current - parsed > mode_stale_after(record.mode)


def save_working_memory(
    session_id: str,
    data_dir: str,
    *,
    user_id: str = "",
    mode: str,
    source: str,
    expected_reply: str,
    payload: dict[str, Any],
    summary: str = "",
    created_at: Optional[str] = None,
    last_updated_at: Optional[str] = None,
    expires_at: Optional[str] = None,
) -> WorkingMemoryRecord:
    init_working_memory_store(session_id, data_dir, user_id=user_id)
    if mode not in WORKING_MEMORY_MODES:
        raise ValueError(f"unsupported working memory mode: {mode}")
    now = _now()
    created = created_at or now
    updated = last_updated_at or now
    updated_dt = _parse_iso(updated) or datetime.now(timezone.utc)
    expires = expires_at or (updated_dt + mode_ttl(mode)).isoformat()
    memory_id = uuid.uuid4().hex[:10]
    with _conn(session_id, data_dir, user_id=user_id) as conn:
        conn.execute(
            """
            UPDATE working_memory
            SET status = 'replaced', resolved_at = ?, last_updated_at = ?
            WHERE session_id = ? AND status = 'active'
            """,
            (now, now, session_id),
        )
        conn.execute(
            """
            INSERT INTO working_memory (
                id, session_id, mode, source, expected_reply, status, payload_json,
                summary, created_at, last_updated_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                session_id,
                mode,
                source,
                expected_reply,
                _json_dumps(payload),
                summary,
                created,
                updated,
                expires,
            ),
        )
        row = conn.execute("SELECT * FROM working_memory WHERE id = ?", (memory_id,)).fetchone()
    return _row_to_record(row)


def load_active_working_memory(session_id: str, data_dir: str, *, user_id: str = "") -> Optional[WorkingMemoryRecord]:
    init_working_memory_store(session_id, data_dir, user_id=user_id)
    now = datetime.now(timezone.utc)
    with _conn(session_id, data_dir, user_id=user_id) as conn:
        rows = conn.execute(
            """
            SELECT * FROM working_memory
            WHERE session_id = ? AND status = 'active'
            ORDER BY last_updated_at DESC
            """,
            (session_id,),
        ).fetchall()
        for row in rows:
            record = _row_to_record(row)
            expires = _parse_iso(record.expires_at)
            if expires is not None and expires <= now:
                conn.execute(
                    """
                    UPDATE working_memory
                    SET status = 'expired', resolved_at = ?, last_updated_at = ?
                    WHERE id = ?
                    """,
                    (_now(), _now(), record.id),
                )
                continue
            return record
    return None


def clear_active_working_memory(
    session_id: str,
    data_dir: str,
    *,
    user_id: str = "",
    status: str = "resolved",
) -> None:
    init_working_memory_store(session_id, data_dir, user_id=user_id)
    if status not in WORKING_MEMORY_STATUSES - {"active"}:
        raise ValueError(f"unsupported working memory clear status: {status}")
    now = _now()
    with _conn(session_id, data_dir, user_id=user_id) as conn:
        conn.execute(
            """
            UPDATE working_memory
            SET status = ?, resolved_at = ?, last_updated_at = ?
            WHERE session_id = ? AND status = 'active'
            """,
            (status, now, now, session_id),
        )


def mark_working_memory_status(
    session_id: str,
    data_dir: str,
    memory_id: str,
    *,
    status: str,
) -> None:
    if status not in WORKING_MEMORY_STATUSES - {"active"}:
        raise ValueError(f"unsupported working memory status: {status}")
    init_working_memory_store(session_id, data_dir)
    now = _now()
    with _conn(session_id, data_dir) as conn:
        conn.execute(
            """
            UPDATE working_memory
            SET status = ?, resolved_at = ?, last_updated_at = ?
            WHERE session_id = ? AND id = ?
            """,
            (status, now, now, session_id, memory_id),
        )


def update_working_memory_timestamps(
    session_id: str,
    data_dir: str,
    memory_id: str,
    *,
    last_updated_at: Optional[str] = None,
    expires_at: Optional[str] = None,
) -> None:
    init_working_memory_store(session_id, data_dir)
    updates: list[str] = []
    params: list[Any] = []
    if last_updated_at is not None:
        updates.append("last_updated_at = ?")
        params.append(last_updated_at)
    if expires_at is not None:
        updates.append("expires_at = ?")
        params.append(expires_at)
    if not updates:
        return
    params.extend([session_id, memory_id])
    with _conn(session_id, data_dir) as conn:
        conn.execute(
            f"UPDATE working_memory SET {', '.join(updates)} WHERE session_id = ? AND id = ?",
            params,
        )


def list_working_memory(
    session_id: str,
    data_dir: str,
    *,
    status: Optional[str] = None,
    limit: int = 20,
) -> list[WorkingMemoryRecord]:
    init_working_memory_store(session_id, data_dir)
    sql = "SELECT * FROM working_memory WHERE session_id = ?"
    params: list[Any] = [session_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(limit, 100)))
    with _conn(session_id, data_dir) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_record(row) for row in rows]
