"""Approval queue and audit log persistence for the personal-manager agent."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from .store import _pm_dir


APPROVAL_STATUSES = {"pending", "executing", "approved", "rejected", "executed", "failed"}


@dataclass(frozen=True)
class ApprovalRecord:
    id: str
    session_id: str
    action_type: str
    payload: dict[str, Any]
    summary: str
    risk_level: str
    status: str
    created_at: str
    decided_at: Optional[str]
    result: Optional[dict[str, Any]]

    def model_dump(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sessionId": self.session_id,
            "actionType": self.action_type,
            "payload": self.payload,
            "summary": self.summary,
            "riskLevel": self.risk_level,
            "status": self.status,
            "createdAt": self.created_at,
            "decidedAt": self.decided_at,
            "result": self.result,
        }


def pm_db_path(session_id: str, data_dir: str, *, user_id: str = "") -> str:
    """Return path to the pm.db file.

    ``user_id`` overrides which user directory is used (for thread-scoped
    operations where ``session_id`` is a thread_id, not the user_id).
    """
    key = user_id or session_id
    return os.path.join(_pm_dir(key, data_dir), "pm.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_control_store(session_id: str, data_dir: str, *, user_id: str = "") -> None:
    with _conn(pm_db_path(session_id, data_dir, user_id=user_id)) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS approval_requests (
                id           TEXT PRIMARY KEY,
                session_id   TEXT NOT NULL,
                action_type  TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                summary      TEXT NOT NULL,
                risk_level   TEXT NOT NULL,
                status       TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                decided_at   TEXT,
                result_json  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_approval_session_status
                ON approval_requests(session_id, status, created_at DESC);

            CREATE TABLE IF NOT EXISTS audit_events (
                id              TEXT PRIMARY KEY,
                session_id      TEXT NOT NULL,
                event_type      TEXT NOT NULL,
                intent          TEXT NOT NULL,
                action_type     TEXT NOT NULL,
                payload_summary TEXT NOT NULL,
                result_summary  TEXT NOT NULL,
                approval_id     TEXT,
                created_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_session_created
                ON audit_events(session_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS conversation_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id  TEXT NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conversation_log_thread
                ON conversation_log(thread_id, id);
            """
        )


def _row_to_approval(row: sqlite3.Row) -> ApprovalRecord:
    result_raw = row["result_json"]
    return ApprovalRecord(
        id=row["id"],
        session_id=row["session_id"],
        action_type=row["action_type"],
        payload=json.loads(row["payload_json"] or "{}"),
        summary=row["summary"],
        risk_level=row["risk_level"],
        status=row["status"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        result=json.loads(result_raw) if result_raw else None,
    )


def create_approval_request(
    session_id: str,
    data_dir: str,
    *,
    action_type: str,
    payload: dict[str, Any],
    summary: str,
    risk_level: str,
) -> ApprovalRecord:
    init_control_store(session_id, data_dir)
    approval_id = uuid.uuid4().hex[:8]
    with _conn(pm_db_path(session_id, data_dir)) as conn:
        conn.execute(
            """
            INSERT INTO approval_requests (
                id, session_id, action_type, payload_json, summary, risk_level,
                status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                approval_id,
                session_id,
                action_type,
                json.dumps(payload, ensure_ascii=False, default=str),
                summary,
                risk_level,
                _now(),
            ),
        )
    found = get_approval_request(session_id, data_dir, approval_id)
    if found is None:
        raise RuntimeError("approval request was not persisted")
    return found


def list_approval_requests(
    session_id: str,
    data_dir: str,
    *,
    status: Optional[str] = None,
    limit: int = 20,
) -> list[ApprovalRecord]:
    init_control_store(session_id, data_dir)
    sql = "SELECT * FROM approval_requests WHERE session_id = ?"
    params: list[Any] = [session_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(limit, 100)))
    with _conn(pm_db_path(session_id, data_dir)) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_approval(r) for r in rows]


def get_approval_request(
    session_id: str,
    data_dir: str,
    approval_id: str,
) -> Optional[ApprovalRecord]:
    init_control_store(session_id, data_dir)
    with _conn(pm_db_path(session_id, data_dir)) as conn:
        row = conn.execute(
            "SELECT * FROM approval_requests WHERE id = ? AND session_id = ?",
            (approval_id, session_id),
        ).fetchone()
    return _row_to_approval(row) if row else None


def find_approval_request(
    data_dir: str,
    approval_id: str,
    *,
    session_id: Optional[str] = None,
) -> Optional[ApprovalRecord]:
    if session_id:
        return get_approval_request(session_id, data_dir, approval_id)

    root = os.path.join(data_dir, "personal-manager")
    if not os.path.isdir(root):
        return None
    for name in os.listdir(root):
        db_path = os.path.join(root, name, "pm.db")
        if not os.path.isfile(db_path):
            continue
        with _conn(db_path) as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM approval_requests WHERE id = ?",
                    (approval_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                continue
        if row:
            return _row_to_approval(row)
    return None


def _claim_approval_in_db(
    db_path: str,
    approval_id: str,
    *,
    session_id: Optional[str] = None,
) -> Optional[ApprovalRecord]:
    now = _now()
    with _conn(db_path) as conn:
        try:
            if session_id:
                cur = conn.execute(
                    """
                    UPDATE approval_requests
                    SET status = 'executing', decided_at = COALESCE(decided_at, ?)
                    WHERE id = ? AND session_id = ? AND status = 'pending'
                    """,
                    (now, approval_id, session_id),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE approval_requests
                    SET status = 'executing', decided_at = COALESCE(decided_at, ?)
                    WHERE id = ? AND status = 'pending'
                    """,
                    (now, approval_id),
                )
        except sqlite3.OperationalError:
            return None
        if cur.rowcount != 1:
            return None
        row = conn.execute(
            "SELECT * FROM approval_requests WHERE id = ?",
            (approval_id,),
        ).fetchone()
    return _row_to_approval(row) if row else None


def claim_approval_request(
    data_dir: str,
    approval_id: str,
    *,
    session_id: Optional[str] = None,
) -> Optional[ApprovalRecord]:
    """Atomically claim a pending approval for execution."""
    if session_id:
        init_control_store(session_id, data_dir)
        return _claim_approval_in_db(
            pm_db_path(session_id, data_dir),
            approval_id,
            session_id=session_id,
        )

    root = os.path.join(data_dir, "personal-manager")
    if not os.path.isdir(root):
        return None
    for name in os.listdir(root):
        db_path = os.path.join(root, name, "pm.db")
        if not os.path.isfile(db_path):
            continue
        claimed = _claim_approval_in_db(db_path, approval_id)
        if claimed:
            return claimed
    return None


def _reject_approval_in_db(
    db_path: str,
    approval_id: str,
    *,
    session_id: Optional[str] = None,
) -> Optional[ApprovalRecord]:
    now = _now()
    with _conn(db_path) as conn:
        try:
            if session_id:
                cur = conn.execute(
                    """
                    UPDATE approval_requests
                    SET status = 'rejected', decided_at = COALESCE(decided_at, ?)
                    WHERE id = ? AND session_id = ? AND status = 'pending'
                    """,
                    (now, approval_id, session_id),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE approval_requests
                    SET status = 'rejected', decided_at = COALESCE(decided_at, ?)
                    WHERE id = ? AND status = 'pending'
                    """,
                    (now, approval_id),
                )
        except sqlite3.OperationalError:
            return None
        if cur.rowcount != 1:
            return None
        row = conn.execute(
            "SELECT * FROM approval_requests WHERE id = ?",
            (approval_id,),
        ).fetchone()
    return _row_to_approval(row) if row else None


def reject_approval_request(
    data_dir: str,
    approval_id: str,
    *,
    session_id: Optional[str] = None,
) -> Optional[ApprovalRecord]:
    """Atomically reject a pending approval without racing an execution claim."""
    if session_id:
        init_control_store(session_id, data_dir)
        return _reject_approval_in_db(
            pm_db_path(session_id, data_dir),
            approval_id,
            session_id=session_id,
        )

    root = os.path.join(data_dir, "personal-manager")
    if not os.path.isdir(root):
        return None
    for name in os.listdir(root):
        db_path = os.path.join(root, name, "pm.db")
        if not os.path.isfile(db_path):
            continue
        rejected = _reject_approval_in_db(db_path, approval_id)
        if rejected:
            return rejected
    return None


def update_approval_status(
    session_id: str,
    data_dir: str,
    approval_id: str,
    status: str,
    *,
    result: Optional[dict[str, Any]] = None,
) -> Optional[ApprovalRecord]:
    if status not in APPROVAL_STATUSES:
        raise ValueError(f"invalid approval status: {status}")
    init_control_store(session_id, data_dir)
    decided_at = _now() if status in {"executing", "approved", "rejected", "executed", "failed"} else None
    result_json = json.dumps(result, ensure_ascii=False, default=str) if result is not None else None
    with _conn(pm_db_path(session_id, data_dir)) as conn:
        conn.execute(
            """
            UPDATE approval_requests
            SET status = ?, decided_at = COALESCE(?, decided_at),
                result_json = COALESCE(?, result_json)
            WHERE id = ?
            """,
            (status, decided_at, result_json, approval_id),
        )
    return get_approval_request(session_id, data_dir, approval_id)


def _trunc(value: Any, max_len: int = 500) -> str:
    try:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = repr(value)
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def record_audit_event(
    session_id: str,
    data_dir: str,
    *,
    user_id: str = "",
    event_type: str,
    intent: str = "",
    action_type: str = "",
    payload_summary: Any = "",
    result_summary: Any = "",
    approval_id: Optional[str] = None,
) -> None:
    init_control_store(session_id, data_dir, user_id=user_id)
    with _conn(pm_db_path(session_id, data_dir, user_id=user_id)) as conn:
        conn.execute(
            """
            INSERT INTO audit_events (
                id, session_id, event_type, intent, action_type,
                payload_summary, result_summary, approval_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex[:10],
                session_id,
                event_type,
                intent,
                action_type,
                _trunc(payload_summary),
                _trunc(result_summary),
                approval_id,
                _now(),
            ),
        )


def list_audit_events(session_id: str, data_dir: str, *, user_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    init_control_store(session_id, data_dir, user_id=user_id)
    with _conn(pm_db_path(session_id, data_dir, user_id=user_id)) as conn:
        rows = conn.execute(
            """
            SELECT * FROM audit_events
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (session_id, max(1, min(limit, 200))),
        ).fetchall()
    return _audit_rows_to_dicts(rows)


def list_all_audit_events(user_id: str, data_dir: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """Return all audit events for a user regardless of thread."""
    init_control_store(user_id, data_dir)
    with _conn(pm_db_path(user_id, data_dir)) as conn:
        rows = conn.execute(
            "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        ).fetchall()
    return _audit_rows_to_dicts(rows)


def _audit_rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [
        {
            "id": r["id"],
            "sessionId": r["session_id"],
            "eventType": r["event_type"],
            "intent": r["intent"],
            "actionType": r["action_type"],
            "payloadSummary": r["payload_summary"],
            "resultSummary": r["result_summary"],
            "approvalId": r["approval_id"],
            "createdAt": r["created_at"],
        }
        for r in rows
    ]


# ── Conversation log ───────────────────────────────────────────────────────────

def append_conversation_turn(
    user_id: str,
    thread_id: str,
    data_dir: str,
    human_text: str,
    assistant_text: str,
) -> None:
    """Persist a human+assistant exchange to the conversation log."""
    init_control_store(user_id, data_dir)
    now = _now()
    with _conn(pm_db_path(user_id, data_dir)) as conn:
        conn.executemany(
            "INSERT INTO conversation_log (thread_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            [
                (thread_id, "user", human_text, now),
                (thread_id, "assistant", assistant_text, now),
            ],
        )


def list_conversation_turns(
    user_id: str,
    thread_id: str,
    data_dir: str,
    *,
    limit: int = 500,
) -> list[dict[str, str]]:
    """Return ordered human/assistant messages for a thread."""
    init_control_store(user_id, data_dir)
    with _conn(pm_db_path(user_id, data_dir)) as conn:
        rows = conn.execute(
            "SELECT role, content FROM conversation_log WHERE thread_id = ? ORDER BY id LIMIT ?",
            (thread_id, max(1, min(limit, 1000))),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def delete_thread_data(user_id: str, thread_id: str, data_dir: str) -> None:
    """Delete all thread-scoped rows from pm.db and the LangGraph checkpoint tables.

    Called when a chat thread is deleted so that transcript, approvals, audit
    events, and LangGraph state cannot be fetched after the thread is gone.
    """
    db_path = pm_db_path(user_id, data_dir)
    if not os.path.exists(db_path):
        return
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM conversation_log WHERE thread_id = ?", (thread_id,))
        conn.execute("DELETE FROM approval_requests WHERE session_id = ?", (thread_id,))
        conn.execute("DELETE FROM audit_events WHERE session_id = ?", (thread_id,))
        # turn_decision_log is created lazily; skip if it doesn't exist yet.
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "turn_decision_log" in tables:
            conn.execute("DELETE FROM turn_decision_log WHERE session_id = ?", (thread_id,))

    checkpoints_path = os.path.join(os.path.dirname(db_path), "checkpoints.db")
    if os.path.exists(checkpoints_path):
        with _conn(checkpoints_path) as conn:
            # Both LangGraph checkpoint tables are keyed by thread_id.
            for table in ("writes", "checkpoints"):
                conn.execute(f"DELETE FROM {table} WHERE thread_id = ?", (thread_id,))  # noqa: S608
