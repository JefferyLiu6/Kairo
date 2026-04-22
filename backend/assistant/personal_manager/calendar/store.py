"""Google Calendar account and mirror storage for Kairo."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from ..persistence.control_store import pm_db_path


@dataclass(frozen=True)
class CalendarAccount:
    id: str
    session_id: str
    provider: str
    account_email: str
    calendar_id: str
    access_token: str
    refresh_token: str
    token_expiry: Optional[str]
    scopes: str
    sync_token: Optional[str]
    status: str
    last_sync_at: Optional[str]
    next_sync_after: Optional[str]
    sync_status: str
    last_sync_error: str
    last_sync_error_at: Optional[str]
    created_at: str
    updated_at: str

    def model_dump(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sessionId": self.session_id,
            "provider": self.provider,
            "accountEmail": self.account_email,
            "calendarId": self.calendar_id,
            "scopes": self.scopes.split(),
            "syncTokenPresent": bool(self.sync_token),
            "status": self.status,
            "lastSyncAt": self.last_sync_at,
            "nextSyncAfter": self.next_sync_after,
            "syncStatus": self.sync_status,
            "lastSyncError": self.last_sync_error,
            "lastSyncErrorAt": self.last_sync_error_at,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


@dataclass(frozen=True)
class CalendarMirrorEvent:
    id: str
    session_id: str
    account_id: str
    provider: str
    provider_event_id: str
    provider_etag: str
    ical_uid: str
    title: str
    start_at: str
    end_at: str
    timezone: str
    status: str
    notes: str
    location: str
    raw: dict[str, Any]
    deleted_at: Optional[str]
    updated_at: str

    def model_dump(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sessionId": self.session_id,
            "accountId": self.account_id,
            "provider": self.provider,
            "providerEventId": self.provider_event_id,
            "providerEtag": self.provider_etag,
            "icalUid": self.ical_uid,
            "title": self.title,
            "startAt": self.start_at,
            "endAt": self.end_at,
            "timezone": self.timezone,
            "status": self.status,
            "notes": self.notes,
            "location": self.location,
            "raw": self.raw,
            "deletedAt": self.deleted_at,
            "updatedAt": self.updated_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn(session_id: str, data_dir: str) -> sqlite3.Connection:
    path = pm_db_path(session_id, data_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_calendar_store(session_id: str, data_dir: str) -> None:
    with _conn(session_id, data_dir) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS calendar_accounts (
                id            TEXT PRIMARY KEY,
                session_id    TEXT NOT NULL,
                provider      TEXT NOT NULL,
                account_email TEXT NOT NULL,
                calendar_id   TEXT NOT NULL,
                access_token  TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                token_expiry  TEXT,
                scopes        TEXT NOT NULL,
                sync_token    TEXT,
                status        TEXT NOT NULL DEFAULT 'active',
                last_sync_at      TEXT,
                next_sync_after   TEXT,
                sync_status       TEXT NOT NULL DEFAULT 'idle',
                last_sync_error   TEXT NOT NULL DEFAULT '',
                last_sync_error_at TEXT,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_calendar_accounts_session_provider
                ON calendar_accounts(session_id, provider, status);

            CREATE TABLE IF NOT EXISTS calendar_event_mirror (
                id                TEXT PRIMARY KEY,
                session_id        TEXT NOT NULL,
                account_id        TEXT NOT NULL,
                provider          TEXT NOT NULL,
                provider_event_id TEXT NOT NULL,
                provider_etag     TEXT NOT NULL,
                ical_uid          TEXT NOT NULL,
                title             TEXT NOT NULL,
                start_at          TEXT NOT NULL,
                end_at            TEXT NOT NULL,
                timezone          TEXT NOT NULL,
                status            TEXT NOT NULL,
                notes             TEXT NOT NULL,
                location          TEXT NOT NULL,
                raw_json          TEXT NOT NULL,
                deleted_at        TEXT,
                updated_at        TEXT NOT NULL,
                UNIQUE(account_id, provider_event_id)
            );
            CREATE INDEX IF NOT EXISTS idx_calendar_mirror_session_start
                ON calendar_event_mirror(session_id, start_at);
            CREATE INDEX IF NOT EXISTS idx_calendar_mirror_account_provider_event
                ON calendar_event_mirror(account_id, provider_event_id);
            """
        )
        _migrate_calendar_accounts(conn)


def _migrate_calendar_accounts(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(calendar_accounts)").fetchall()
    }
    columns = {
        "last_sync_at": "TEXT",
        "next_sync_after": "TEXT",
        "sync_status": "TEXT NOT NULL DEFAULT 'idle'",
        "last_sync_error": "TEXT NOT NULL DEFAULT ''",
        "last_sync_error_at": "TEXT",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE calendar_accounts ADD COLUMN {name} {definition}")


def _row_to_account(row: sqlite3.Row) -> CalendarAccount:
    return CalendarAccount(
        id=row["id"],
        session_id=row["session_id"],
        provider=row["provider"],
        account_email=row["account_email"],
        calendar_id=row["calendar_id"],
        access_token=row["access_token"],
        refresh_token=row["refresh_token"],
        token_expiry=row["token_expiry"],
        scopes=row["scopes"],
        sync_token=row["sync_token"],
        status=row["status"],
        last_sync_at=row["last_sync_at"],
        next_sync_after=row["next_sync_after"],
        sync_status=row["sync_status"] or "idle",
        last_sync_error=row["last_sync_error"] or "",
        last_sync_error_at=row["last_sync_error_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_event(row: sqlite3.Row) -> CalendarMirrorEvent:
    raw_text = row["raw_json"] or "{}"
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError:
        raw = {}
    return CalendarMirrorEvent(
        id=row["id"],
        session_id=row["session_id"],
        account_id=row["account_id"],
        provider=row["provider"],
        provider_event_id=row["provider_event_id"],
        provider_etag=row["provider_etag"],
        ical_uid=row["ical_uid"],
        title=row["title"],
        start_at=row["start_at"],
        end_at=row["end_at"],
        timezone=row["timezone"],
        status=row["status"],
        notes=row["notes"],
        location=row["location"],
        raw=raw,
        deleted_at=row["deleted_at"],
        updated_at=row["updated_at"],
    )


def upsert_calendar_account(
    session_id: str,
    data_dir: str,
    *,
    provider: str,
    account_email: str = "",
    calendar_id: str = "primary",
    access_token: str,
    refresh_token: str,
    token_expiry: Optional[str],
    scopes: list[str],
) -> CalendarAccount:
    init_calendar_store(session_id, data_dir)
    now = _now()
    with _conn(session_id, data_dir) as conn:
        existing = conn.execute(
            """
            SELECT * FROM calendar_accounts
            WHERE session_id = ? AND provider = ? AND calendar_id = ? AND status = 'active'
            ORDER BY created_at DESC LIMIT 1
            """,
            (session_id, provider, calendar_id),
        ).fetchone()
        if existing:
            account_id = existing["id"]
            conn.execute(
                """
                UPDATE calendar_accounts
                SET account_email = ?, access_token = ?,
                    refresh_token = COALESCE(NULLIF(?, ''), refresh_token),
                    token_expiry = ?, scopes = ?, sync_status = 'idle',
                    last_sync_error = '', last_sync_error_at = NULL,
                    next_sync_after = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    account_email,
                    access_token,
                    refresh_token,
                    token_expiry,
                    " ".join(scopes),
                    now,
                    account_id,
                ),
            )
        else:
            account_id = uuid.uuid4().hex[:10]
            conn.execute(
                """
                INSERT INTO calendar_accounts (
                    id, session_id, provider, account_email, calendar_id,
                    access_token, refresh_token, token_expiry, scopes,
                    status, sync_status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 'idle', ?, ?)
                """,
                (
                    account_id,
                    session_id,
                    provider,
                    account_email,
                    calendar_id,
                    access_token,
                    refresh_token,
                    token_expiry,
                    " ".join(scopes),
                    now,
                    now,
                ),
            )
    account = get_calendar_account(session_id, data_dir, account_id)
    if account is None:
        raise RuntimeError("calendar account was not persisted")
    return account


def list_calendar_accounts(
    session_id: str,
    data_dir: str,
    *,
    provider: Optional[str] = None,
    active_only: bool = True,
) -> list[CalendarAccount]:
    init_calendar_store(session_id, data_dir)
    sql = "SELECT * FROM calendar_accounts WHERE session_id = ?"
    params: list[Any] = [session_id]
    if provider:
        sql += " AND provider = ?"
        params.append(provider)
    if active_only:
        sql += " AND status = 'active'"
    sql += " ORDER BY created_at DESC"
    with _conn(session_id, data_dir) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_account(r) for r in rows]


def get_calendar_account(
    session_id: str,
    data_dir: str,
    account_id: str,
) -> Optional[CalendarAccount]:
    init_calendar_store(session_id, data_dir)
    with _conn(session_id, data_dir) as conn:
        row = conn.execute(
            "SELECT * FROM calendar_accounts WHERE session_id = ? AND id = ?",
            (session_id, account_id),
        ).fetchone()
    return _row_to_account(row) if row else None


def update_calendar_tokens(
    session_id: str,
    data_dir: str,
    account_id: str,
    *,
    access_token: str,
    refresh_token: Optional[str] = None,
    token_expiry: Optional[str] = None,
) -> None:
    init_calendar_store(session_id, data_dir)
    with _conn(session_id, data_dir) as conn:
        conn.execute(
            """
            UPDATE calendar_accounts
            SET access_token = ?,
                refresh_token = COALESCE(NULLIF(?, ''), refresh_token),
                token_expiry = ?,
                updated_at = ?
            WHERE id = ? AND session_id = ?
            """,
            (access_token, refresh_token or "", token_expiry, _now(), account_id, session_id),
        )


def save_sync_token(
    session_id: str,
    data_dir: str,
    account_id: str,
    sync_token: Optional[str],
) -> None:
    init_calendar_store(session_id, data_dir)
    with _conn(session_id, data_dir) as conn:
        conn.execute(
            """
            UPDATE calendar_accounts
            SET sync_token = ?, updated_at = ?
            WHERE id = ? AND session_id = ?
            """,
            (sync_token, _now(), account_id, session_id),
        )


def mark_calendar_sync_started(session_id: str, data_dir: str, account_id: str) -> None:
    init_calendar_store(session_id, data_dir)
    with _conn(session_id, data_dir) as conn:
        conn.execute(
            """
            UPDATE calendar_accounts
            SET sync_status = 'syncing', updated_at = ?
            WHERE id = ? AND session_id = ?
            """,
            (_now(), account_id, session_id),
        )


def mark_calendar_sync_finished(session_id: str, data_dir: str, account_id: str) -> None:
    init_calendar_store(session_id, data_dir)
    now = _now()
    with _conn(session_id, data_dir) as conn:
        conn.execute(
            """
            UPDATE calendar_accounts
            SET last_sync_at = ?, next_sync_after = NULL, sync_status = 'idle',
                last_sync_error = '', last_sync_error_at = NULL, updated_at = ?
            WHERE id = ? AND session_id = ?
            """,
            (now, now, account_id, session_id),
        )


def mark_calendar_sync_failed(
    session_id: str,
    data_dir: str,
    account_id: str,
    *,
    error: str,
    next_sync_after: Optional[str],
) -> None:
    init_calendar_store(session_id, data_dir)
    now = _now()
    with _conn(session_id, data_dir) as conn:
        conn.execute(
            """
            UPDATE calendar_accounts
            SET sync_status = 'error', last_sync_error = ?, last_sync_error_at = ?,
                next_sync_after = ?, updated_at = ?
            WHERE id = ? AND session_id = ?
            """,
            (error[:1000], now, next_sync_after, now, account_id, session_id),
        )


def disconnect_calendar_account(session_id: str, data_dir: str, account_id: str) -> bool:
    init_calendar_store(session_id, data_dir)
    with _conn(session_id, data_dir) as conn:
        cur = conn.execute(
            """
            UPDATE calendar_accounts
            SET status = 'disconnected', updated_at = ?
            WHERE id = ? AND session_id = ? AND status = 'active'
            """,
            (_now(), account_id, session_id),
        )
    return cur.rowcount == 1


def list_calendar_account_sessions(data_dir: str, *, provider: str = "google") -> list[str]:
    root = os.path.join(data_dir, "personal-manager")
    if not os.path.isdir(root):
        return []
    session_ids: set[str] = set()
    for name in os.listdir(root):
        db_path = os.path.join(root, name, "pm.db")
        if not os.path.isfile(db_path):
            continue
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT DISTINCT session_id FROM calendar_accounts
                WHERE provider = ? AND status = 'active'
                """,
                (provider,),
            ).fetchall()
        except sqlite3.Error:
            continue
        finally:
            if conn is not None:
                conn.close()
        for row in rows:
            session_id = str(row["session_id"] or "").strip()
            if session_id:
                session_ids.add(session_id)
    return sorted(session_ids)


def upsert_mirror_event(
    session_id: str,
    data_dir: str,
    *,
    account_id: str,
    provider: str,
    provider_event_id: str,
    provider_etag: str = "",
    ical_uid: str = "",
    title: str,
    start_at: str = "",
    end_at: str = "",
    timezone_name: str = "",
    status: str = "confirmed",
    notes: str = "",
    location: str = "",
    raw: Optional[dict[str, Any]] = None,
) -> CalendarMirrorEvent:
    init_calendar_store(session_id, data_dir)
    now = _now()
    raw_json = json.dumps(raw or {}, ensure_ascii=False, default=str)
    with _conn(session_id, data_dir) as conn:
        row = conn.execute(
            """
            SELECT id FROM calendar_event_mirror
            WHERE account_id = ? AND provider_event_id = ?
            """,
            (account_id, provider_event_id),
        ).fetchone()
        if row:
            event_id = row["id"]
            conn.execute(
                """
                UPDATE calendar_event_mirror
                SET provider_etag = ?, ical_uid = ?, title = ?, start_at = ?,
                    end_at = ?, timezone = ?, status = ?, notes = ?,
                    location = ?, raw_json = ?, deleted_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    provider_etag,
                    ical_uid,
                    title,
                    start_at,
                    end_at,
                    timezone_name,
                    status,
                    notes,
                    location,
                    raw_json,
                    now,
                    event_id,
                ),
            )
        else:
            event_id = uuid.uuid4().hex[:10]
            conn.execute(
                """
                INSERT INTO calendar_event_mirror (
                    id, session_id, account_id, provider, provider_event_id,
                    provider_etag, ical_uid, title, start_at, end_at,
                    timezone, status, notes, location, raw_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    session_id,
                    account_id,
                    provider,
                    provider_event_id,
                    provider_etag,
                    ical_uid,
                    title,
                    start_at,
                    end_at,
                    timezone_name,
                    status,
                    notes,
                    location,
                    raw_json,
                    now,
                ),
            )
    event = get_mirror_event(session_id, data_dir, event_id)
    if event is None:
        raise RuntimeError("calendar mirror event was not persisted")
    return event


def get_mirror_event(
    session_id: str,
    data_dir: str,
    event_id: str,
) -> Optional[CalendarMirrorEvent]:
    init_calendar_store(session_id, data_dir)
    with _conn(session_id, data_dir) as conn:
        row = conn.execute(
            """
            SELECT * FROM calendar_event_mirror
            WHERE session_id = ? AND id = ?
            """,
            (session_id, event_id),
        ).fetchone()
    return _row_to_event(row) if row else None


def mark_mirror_deleted(
    session_id: str,
    data_dir: str,
    *,
    account_id: str,
    provider_event_id: str,
) -> None:
    init_calendar_store(session_id, data_dir)
    now = _now()
    with _conn(session_id, data_dir) as conn:
        conn.execute(
            """
            UPDATE calendar_event_mirror
            SET status = 'cancelled', deleted_at = COALESCE(deleted_at, ?), updated_at = ?
            WHERE session_id = ? AND account_id = ? AND provider_event_id = ?
            """,
            (now, now, session_id, account_id, provider_event_id),
        )


def wipe_account_mirror(session_id: str, data_dir: str, account_id: str) -> None:
    init_calendar_store(session_id, data_dir)
    with _conn(session_id, data_dir) as conn:
        conn.execute(
            "DELETE FROM calendar_event_mirror WHERE session_id = ? AND account_id = ?",
            (session_id, account_id),
        )


def list_mirror_events(
    session_id: str,
    data_dir: str,
    *,
    start: Optional[date | datetime] = None,
    end: Optional[date | datetime] = None,
    include_deleted: bool = False,
    limit: int = 200,
) -> list[CalendarMirrorEvent]:
    init_calendar_store(session_id, data_dir)
    with _conn(session_id, data_dir) as conn:
        sql = "SELECT * FROM calendar_event_mirror WHERE session_id = ?"
        params: list[Any] = [session_id]
        if not include_deleted:
            sql += " AND deleted_at IS NULL AND status != 'cancelled'"
        sql += " ORDER BY start_at ASC LIMIT ?"
        params.append(max(1, min(limit, 1000)))
        rows = conn.execute(sql, params).fetchall()
    events = [_row_to_event(row) for row in rows]
    if start is None and end is None:
        return events
    return [event for event in events if _event_overlaps(event, start=start, end=end)]


def _event_overlaps(
    event: CalendarMirrorEvent,
    *,
    start: Optional[date | datetime],
    end: Optional[date | datetime],
) -> bool:
    event_start = _parse_event_date_or_datetime(event.start_at)
    event_end = _parse_event_date_or_datetime(event.end_at)
    if event_start is None:
        return False
    if event_end is None:
        event_end = event_start
    start_dt = _coerce_boundary(start, is_end=False)
    end_dt = _coerce_boundary(end, is_end=True)
    if event_start.tzinfo is not None:
        if start_dt is not None and start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=event_start.tzinfo)
        if end_dt is not None and end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=event_start.tzinfo)
    if start_dt is not None and event_end < start_dt:
        return False
    if end_dt is not None and event_start > end_dt:
        return False
    return True


def _parse_event_date_or_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    cleaned = value.replace("Z", "+00:00")
    try:
        if "T" in cleaned:
            return datetime.fromisoformat(cleaned)
        return datetime.combine(date.fromisoformat(cleaned), datetime.min.time())
    except ValueError:
        return None


def _coerce_boundary(value: Optional[date | datetime], *, is_end: bool) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    base = datetime.combine(value, datetime.min.time())
    return base + timedelta(days=1) if is_end else base
