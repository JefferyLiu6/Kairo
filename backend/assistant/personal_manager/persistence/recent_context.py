"""Non-blocking short-term context for PM parsing hints."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .control_store import pm_db_path


RECENT_CONTEXT_TYPES = {"activity_topic"}
RECENT_CONTEXT_STATUSES = {"active", "expired", "replaced"}

_CONTEXT_TTL = {
    "activity_topic": timedelta(minutes=30),
}
_CONTEXT_STALE_AFTER = {
    "activity_topic": timedelta(minutes=15),
}


@dataclass(frozen=True)
class RecentContextRecord:
    id: str
    session_id: str
    context_type: str
    status: str
    payload: dict[str, Any]
    created_at: str
    updated_at: str
    expires_at: str


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


def _conn(session_id: str, data_dir: str) -> sqlite3.Connection:
    path = pm_db_path(session_id, data_dir)
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


def init_recent_context_store(session_id: str, data_dir: str) -> None:
    with _conn(session_id, data_dir) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS recent_context (
                id           TEXT PRIMARY KEY,
                session_id   TEXT NOT NULL,
                context_type TEXT NOT NULL,
                status       TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                expires_at   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_recent_context_session_type
                ON recent_context(session_id, status, context_type, updated_at DESC);
            """
        )


def _row_to_record(row: sqlite3.Row) -> RecentContextRecord:
    return RecentContextRecord(
        id=row["id"],
        session_id=row["session_id"],
        context_type=row["context_type"],
        status=row["status"],
        payload=_json_loads(row["payload_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
    )


def context_ttl(context_type: str) -> timedelta:
    return _CONTEXT_TTL.get(context_type, timedelta(minutes=30))


def context_stale_after(context_type: str) -> timedelta:
    return _CONTEXT_STALE_AFTER.get(context_type, timedelta(minutes=15))


def recent_context_is_stale(record: RecentContextRecord, *, now: Optional[datetime] = None) -> bool:
    parsed = _parse_iso(record.updated_at)
    if parsed is None:
        return False
    current = now or datetime.now(timezone.utc)
    return current - parsed > context_stale_after(record.context_type)


def save_recent_context(
    session_id: str,
    data_dir: str,
    *,
    context_type: str,
    payload: dict[str, Any],
    updated_at: Optional[str] = None,
    expires_at: Optional[str] = None,
) -> RecentContextRecord:
    init_recent_context_store(session_id, data_dir)
    if context_type not in RECENT_CONTEXT_TYPES:
        raise ValueError(f"unsupported recent context type: {context_type}")
    now = _now()
    updated = updated_at or now
    updated_dt = _parse_iso(updated) or datetime.now(timezone.utc)
    expires = expires_at or (updated_dt + context_ttl(context_type)).isoformat()
    record_id = uuid.uuid4().hex[:10]
    with _conn(session_id, data_dir) as conn:
        conn.execute(
            """
            INSERT INTO recent_context (
                id, session_id, context_type, status, payload_json,
                created_at, updated_at, expires_at
            )
            VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
            """,
            (record_id, session_id, context_type, _json_dumps(payload), now, updated, expires),
        )
        row = conn.execute("SELECT * FROM recent_context WHERE id = ?", (record_id,)).fetchone()
    return _row_to_record(row)


def upsert_activity_context(
    session_id: str,
    data_dir: str,
    *,
    activity: str,
    payload: dict[str, Any],
) -> RecentContextRecord:
    init_recent_context_store(session_id, data_dir)
    active = list_recent_context(session_id, data_dir, context_type="activity_topic", include_stale=True)
    existing = next(
        (
            record
            for record in active
            if str(record.payload.get("activity") or "").lower() == activity.lower()
        ),
        None,
    )
    now = _now()
    expires = (datetime.now(timezone.utc) + context_ttl("activity_topic")).isoformat()
    merged = dict(existing.payload) if existing is not None else {}
    merged.update(payload)
    merged["activity"] = activity
    if existing is None:
        return save_recent_context(
            session_id,
            data_dir,
            context_type="activity_topic",
            payload=merged,
            updated_at=now,
            expires_at=expires,
        )
    with _conn(session_id, data_dir) as conn:
        conn.execute(
            """
            UPDATE recent_context
            SET payload_json = ?, status = 'active', updated_at = ?, expires_at = ?
            WHERE session_id = ? AND id = ?
            """,
            (_json_dumps(merged), now, expires, session_id, existing.id),
        )
        row = conn.execute("SELECT * FROM recent_context WHERE id = ?", (existing.id,)).fetchone()
    return _row_to_record(row)


def list_recent_context(
    session_id: str,
    data_dir: str,
    *,
    context_type: Optional[str] = None,
    include_stale: bool = False,
) -> list[RecentContextRecord]:
    init_recent_context_store(session_id, data_dir)
    now_dt = datetime.now(timezone.utc)
    with _conn(session_id, data_dir) as conn:
        rows = conn.execute(
            """
            SELECT * FROM recent_context
            WHERE session_id = ? AND status = 'active'
            ORDER BY updated_at DESC
            """,
            (session_id,),
        ).fetchall()
        records: list[RecentContextRecord] = []
        for row in rows:
            record = _row_to_record(row)
            expires = _parse_iso(record.expires_at)
            if expires is not None and expires <= now_dt:
                conn.execute(
                    """
                    UPDATE recent_context
                    SET status = 'expired', updated_at = ?
                    WHERE id = ?
                    """,
                    (_now(), record.id),
                )
                continue
            if context_type and record.context_type != context_type:
                continue
            if not include_stale and recent_context_is_stale(record, now=now_dt):
                continue
            records.append(record)
    return records


def update_recent_context_payload(
    session_id: str,
    data_dir: str,
    context_id: str,
    payload: dict[str, Any],
) -> Optional[RecentContextRecord]:
    init_recent_context_store(session_id, data_dir)
    now = _now()
    with _conn(session_id, data_dir) as conn:
        row = conn.execute(
            "SELECT * FROM recent_context WHERE session_id = ? AND id = ?",
            (session_id, context_id),
        ).fetchone()
        if row is None:
            return None
        record = _row_to_record(row)
        merged = dict(record.payload)
        merged.update(payload)
        expires = (datetime.now(timezone.utc) + context_ttl(record.context_type)).isoformat()
        conn.execute(
            """
            UPDATE recent_context
            SET payload_json = ?, status = 'active', updated_at = ?, expires_at = ?
            WHERE session_id = ? AND id = ?
            """,
            (_json_dumps(merged), now, expires, session_id, context_id),
        )
        row = conn.execute("SELECT * FROM recent_context WHERE id = ?", (context_id,)).fetchone()
    return _row_to_record(row)
