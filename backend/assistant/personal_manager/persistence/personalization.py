"""Preference and pattern storage for personal-manager field completion."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .control_store import pm_db_path


@dataclass(frozen=True)
class PreferenceRecord:
    id: str
    session_id: str
    scope_type: str
    scope_key: str
    rule_type: str
    value: dict[str, Any]
    confidence: float
    source: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class BehavioralPatternRecord:
    id: str
    session_id: str
    scope_type: str
    scope_key: str
    pattern_type: str
    rule_type: str
    value: dict[str, Any]
    confidence: float
    sample_count: int
    distinct_days: int
    selected_count: int
    shown_count: int
    last_observed_at: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class FieldChoiceRecord:
    id: str
    session_id: str
    intent: str
    field_name: str
    value: dict[str, Any]
    value_key: str
    label: str
    scope_type: str
    scope_key: str
    source: str
    confidence: float
    shown_count: int
    selected_count: int
    created_at: str
    updated_at: str
    last_selected_at: Optional[str]
    last_shown_at: Optional[str]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _clamp(value: float, lo: float = 0.0, hi: float = 0.95) -> float:
    return max(lo, min(hi, value))


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


def _decay_penalty(last_selected_at: Optional[str]) -> float:
    parsed = _parse_iso(last_selected_at)
    if parsed is None:
        return 0.0
    inactive_days = (datetime.now(timezone.utc) - parsed).days
    if inactive_days <= 30:
        return 0.0
    return min(0.45, 0.05 * ((inactive_days - 1) // 30))


def choice_confidence(
    *,
    selected_count: int,
    shown_count: int,
    last_selected_at: Optional[str],
) -> float:
    selection_rate = selected_count / max(shown_count, 1)
    return _clamp(
        0.20
        + (0.12 * selected_count)
        + (0.04 * selection_rate)
        - _decay_penalty(last_selected_at)
    )


def init_personalization_store(session_id: str, data_dir: str) -> None:
    with _conn(session_id, data_dir) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                scope_type  TEXT NOT NULL,
                scope_key   TEXT NOT NULL,
                rule_type   TEXT NOT NULL,
                value_json  TEXT NOT NULL,
                confidence  REAL NOT NULL,
                source      TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'active',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                UNIQUE(session_id, scope_type, scope_key, rule_type, value_json)
            );
            CREATE INDEX IF NOT EXISTS idx_user_preferences_scope
                ON user_preferences(session_id, status, scope_type, scope_key);

            CREATE TABLE IF NOT EXISTS field_choice_memory (
                id               TEXT PRIMARY KEY,
                session_id       TEXT NOT NULL,
                intent           TEXT NOT NULL,
                field_name       TEXT NOT NULL,
                value_json       TEXT NOT NULL,
                value_key        TEXT NOT NULL,
                label            TEXT NOT NULL,
                scope_type       TEXT NOT NULL,
                scope_key        TEXT NOT NULL,
                source           TEXT NOT NULL,
                confidence       REAL NOT NULL,
                shown_count      INTEGER NOT NULL DEFAULT 0,
                selected_count   INTEGER NOT NULL DEFAULT 0,
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL,
                last_selected_at TEXT,
                last_shown_at    TEXT,
                UNIQUE(session_id, intent, field_name, value_key, scope_type, scope_key)
            );
            CREATE INDEX IF NOT EXISTS idx_field_choice_scope
                ON field_choice_memory(session_id, intent, field_name, scope_type, scope_key);
            CREATE INDEX IF NOT EXISTS idx_field_choice_shown
                ON field_choice_memory(session_id, last_shown_at);

            CREATE TABLE IF NOT EXISTS behavioral_patterns (
                id               TEXT PRIMARY KEY,
                session_id       TEXT NOT NULL,
                scope_type       TEXT NOT NULL,
                scope_key        TEXT NOT NULL,
                pattern_type     TEXT NOT NULL,
                rule_type        TEXT NOT NULL,
                value_json       TEXT NOT NULL,
                confidence       REAL NOT NULL,
                sample_count     INTEGER NOT NULL,
                distinct_days    INTEGER NOT NULL,
                selected_count   INTEGER NOT NULL,
                shown_count      INTEGER NOT NULL,
                last_observed_at TEXT NOT NULL,
                status           TEXT NOT NULL DEFAULT 'active',
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL,
                UNIQUE(session_id, scope_type, scope_key, pattern_type, rule_type, value_json)
            );
            CREATE INDEX IF NOT EXISTS idx_behavioral_patterns_scope
                ON behavioral_patterns(session_id, status, scope_type, scope_key);
            """
        )


def _row_to_preference(row: sqlite3.Row) -> PreferenceRecord:
    return PreferenceRecord(
        id=row["id"],
        session_id=row["session_id"],
        scope_type=row["scope_type"],
        scope_key=row["scope_key"],
        rule_type=row["rule_type"],
        value=_json_loads(row["value_json"]),
        confidence=float(row["confidence"]),
        source=row["source"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_pattern(row: sqlite3.Row) -> BehavioralPatternRecord:
    return BehavioralPatternRecord(
        id=row["id"],
        session_id=row["session_id"],
        scope_type=row["scope_type"],
        scope_key=row["scope_key"],
        pattern_type=row["pattern_type"],
        rule_type=row["rule_type"],
        value=_json_loads(row["value_json"]),
        confidence=float(row["confidence"]),
        sample_count=int(row["sample_count"]),
        distinct_days=int(row["distinct_days"]),
        selected_count=int(row["selected_count"]),
        shown_count=int(row["shown_count"]),
        last_observed_at=row["last_observed_at"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_choice(row: sqlite3.Row) -> FieldChoiceRecord:
    return FieldChoiceRecord(
        id=row["id"],
        session_id=row["session_id"],
        intent=row["intent"],
        field_name=row["field_name"],
        value=_json_loads(row["value_json"]),
        value_key=row["value_key"],
        label=row["label"],
        scope_type=row["scope_type"],
        scope_key=row["scope_key"],
        source=row["source"],
        confidence=float(row["confidence"]),
        shown_count=int(row["shown_count"]),
        selected_count=int(row["selected_count"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_selected_at=row["last_selected_at"],
        last_shown_at=row["last_shown_at"],
    )


def upsert_user_preference(
    session_id: str,
    data_dir: str,
    *,
    scope_type: str,
    scope_key: str,
    rule_type: str,
    value: dict[str, Any],
    confidence: float = 0.9,
    source: str = "explicit",
) -> PreferenceRecord:
    init_personalization_store(session_id, data_dir)
    now = _now()
    value_json = _json_dumps(value)
    with _conn(session_id, data_dir) as conn:
        conn.execute(
            """
            INSERT INTO user_preferences (
                id, session_id, scope_type, scope_key, rule_type, value_json,
                confidence, source, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(session_id, scope_type, scope_key, rule_type, value_json)
            DO UPDATE SET
                confidence = excluded.confidence,
                source = excluded.source,
                status = 'active',
                updated_at = excluded.updated_at
            """,
            (
                uuid.uuid4().hex[:10],
                session_id,
                scope_type,
                scope_key,
                rule_type,
                value_json,
                _clamp(confidence, 0.0, 1.0),
                source,
                now,
                now,
            ),
        )
        row = conn.execute(
            """
            SELECT * FROM user_preferences
            WHERE session_id = ? AND scope_type = ? AND scope_key = ?
              AND rule_type = ? AND value_json = ?
            """,
            (session_id, scope_type, scope_key, rule_type, value_json),
        ).fetchone()
    return _row_to_preference(row)


def list_user_preferences(session_id: str, data_dir: str) -> list[PreferenceRecord]:
    init_personalization_store(session_id, data_dir)
    with _conn(session_id, data_dir) as conn:
        rows = conn.execute(
            """
            SELECT * FROM user_preferences
            WHERE session_id = ? AND status = 'active'
            ORDER BY updated_at DESC
            """,
            (session_id,),
        ).fetchall()
    return [_row_to_preference(row) for row in rows]


def list_behavioral_patterns(session_id: str, data_dir: str) -> list[BehavioralPatternRecord]:
    init_personalization_store(session_id, data_dir)
    with _conn(session_id, data_dir) as conn:
        rows = conn.execute(
            """
            SELECT * FROM behavioral_patterns
            WHERE session_id = ? AND status = 'active'
            ORDER BY confidence DESC, updated_at DESC
            """,
            (session_id,),
        ).fetchall()
    return [_row_to_pattern(row) for row in rows]


def upsert_behavioral_pattern(
    session_id: str,
    data_dir: str,
    *,
    scope_type: str,
    scope_key: str,
    pattern_type: str,
    rule_type: str,
    value: dict[str, Any],
    confidence: float,
    sample_count: int,
    distinct_days: int,
    selected_count: int,
    shown_count: int,
    observed_at: Optional[str] = None,
) -> BehavioralPatternRecord:
    init_personalization_store(session_id, data_dir)
    now = _now()
    value_json = _json_dumps(value)
    last_observed_at = observed_at or now
    with _conn(session_id, data_dir) as conn:
        conn.execute(
            """
            INSERT INTO behavioral_patterns (
                id, session_id, scope_type, scope_key, pattern_type, rule_type,
                value_json, confidence, sample_count, distinct_days, selected_count,
                shown_count, last_observed_at, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(session_id, scope_type, scope_key, pattern_type, rule_type, value_json)
            DO UPDATE SET
                confidence = excluded.confidence,
                sample_count = excluded.sample_count,
                distinct_days = excluded.distinct_days,
                selected_count = excluded.selected_count,
                shown_count = excluded.shown_count,
                last_observed_at = excluded.last_observed_at,
                status = 'active',
                updated_at = excluded.updated_at
            """,
            (
                uuid.uuid4().hex[:10],
                session_id,
                scope_type,
                scope_key,
                pattern_type,
                rule_type,
                value_json,
                _clamp(confidence, 0.0, 1.0),
                sample_count,
                distinct_days,
                selected_count,
                shown_count,
                last_observed_at,
                now,
                now,
            ),
        )
        row = conn.execute(
            """
            SELECT * FROM behavioral_patterns
            WHERE session_id = ? AND scope_type = ? AND scope_key = ?
              AND pattern_type = ? AND rule_type = ? AND value_json = ?
            """,
            (session_id, scope_type, scope_key, pattern_type, rule_type, value_json),
        ).fetchone()
    return _row_to_pattern(row)


def list_field_choices(
    session_id: str,
    data_dir: str,
    *,
    intent: Optional[str] = None,
    field_name: Optional[str] = None,
) -> list[FieldChoiceRecord]:
    init_personalization_store(session_id, data_dir)
    sql = "SELECT * FROM field_choice_memory WHERE session_id = ?"
    params: list[Any] = [session_id]
    if intent:
        sql += " AND intent = ?"
        params.append(intent)
    if field_name:
        sql += " AND field_name = ?"
        params.append(field_name)
    sql += " ORDER BY updated_at DESC"
    with _conn(session_id, data_dir) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_choice(row) for row in rows]


def record_choice_shown(
    session_id: str,
    data_dir: str,
    *,
    intent: str,
    field_name: str,
    value: dict[str, Any],
    label: str,
    scope_type: str,
    scope_key: str,
    source: str,
) -> FieldChoiceRecord:
    init_personalization_store(session_id, data_dir)
    now = _now()
    value_json = _json_dumps(value)
    with _conn(session_id, data_dir) as conn:
        row = conn.execute(
            """
            SELECT * FROM field_choice_memory
            WHERE session_id = ? AND intent = ? AND field_name = ?
              AND value_key = ? AND scope_type = ? AND scope_key = ?
            """,
            (session_id, intent, field_name, value_json, scope_type, scope_key),
        ).fetchone()
        if row:
            shown = int(row["shown_count"]) + 1
            selected = int(row["selected_count"])
            confidence = choice_confidence(
                selected_count=selected,
                shown_count=shown,
                last_selected_at=row["last_selected_at"],
            )
            conn.execute(
                """
                UPDATE field_choice_memory
                SET label = ?, source = ?, confidence = ?, shown_count = ?,
                    updated_at = ?, last_shown_at = ?
                WHERE id = ?
                """,
                (label, source, confidence, shown, now, now, row["id"]),
            )
            row = conn.execute("SELECT * FROM field_choice_memory WHERE id = ?", (row["id"],)).fetchone()
        else:
            confidence = choice_confidence(
                selected_count=0,
                shown_count=1,
                last_selected_at=None,
            )
            choice_id = uuid.uuid4().hex[:10]
            conn.execute(
                """
                INSERT INTO field_choice_memory (
                    id, session_id, intent, field_name, value_json, value_key,
                    label, scope_type, scope_key, source, confidence, shown_count,
                    selected_count, created_at, updated_at, last_shown_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?)
                """,
                (
                    choice_id,
                    session_id,
                    intent,
                    field_name,
                    value_json,
                    value_json,
                    label,
                    scope_type,
                    scope_key,
                    source,
                    confidence,
                    now,
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM field_choice_memory WHERE id = ?", (choice_id,)).fetchone()
    return _row_to_choice(row)


def record_choice_selected(
    session_id: str,
    data_dir: str,
    *,
    intent: str,
    field_name: str,
    value: dict[str, Any],
    label: str,
    scope_type: str,
    scope_key: str,
    source: str,
    selected_at: Optional[str] = None,
) -> FieldChoiceRecord:
    init_personalization_store(session_id, data_dir)
    selected_time = selected_at or _now()
    value_json = _json_dumps(value)
    with _conn(session_id, data_dir) as conn:
        row = conn.execute(
            """
            SELECT * FROM field_choice_memory
            WHERE session_id = ? AND intent = ? AND field_name = ?
              AND value_key = ? AND scope_type = ? AND scope_key = ?
            """,
            (session_id, intent, field_name, value_json, scope_type, scope_key),
        ).fetchone()
        if row:
            shown = max(int(row["shown_count"]), 1)
            selected = int(row["selected_count"]) + 1
            confidence = choice_confidence(
                selected_count=selected,
                shown_count=shown,
                last_selected_at=selected_time,
            )
            conn.execute(
                """
                UPDATE field_choice_memory
                SET label = ?, source = ?, confidence = ?, selected_count = ?,
                    updated_at = ?, last_selected_at = ?,
                    last_shown_at = COALESCE(last_shown_at, ?)
                WHERE id = ?
                """,
                (label, source, confidence, selected, selected_time, selected_time, selected_time, row["id"]),
            )
            row = conn.execute("SELECT * FROM field_choice_memory WHERE id = ?", (row["id"],)).fetchone()
        else:
            confidence = choice_confidence(
                selected_count=1,
                shown_count=1,
                last_selected_at=selected_time,
            )
            choice_id = uuid.uuid4().hex[:10]
            conn.execute(
                """
                INSERT INTO field_choice_memory (
                    id, session_id, intent, field_name, value_json, value_key,
                    label, scope_type, scope_key, source, confidence, shown_count,
                    selected_count, created_at, updated_at, last_selected_at, last_shown_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?, ?)
                """,
                (
                    choice_id,
                    session_id,
                    intent,
                    field_name,
                    value_json,
                    value_json,
                    label,
                    scope_type,
                    scope_key,
                    source,
                    confidence,
                    selected_time,
                    selected_time,
                    selected_time,
                    selected_time,
                ),
            )
            row = conn.execute("SELECT * FROM field_choice_memory WHERE id = ?", (choice_id,)).fetchone()
    return _row_to_choice(row)


def recent_choice_penalty(
    session_id: str,
    data_dir: str,
    *,
    intent: str,
    field_name: str,
    value: dict[str, Any],
) -> float:
    init_personalization_store(session_id, data_dir)
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    date_value = str(value.get("date") or "")
    start_value = str(value.get("start") or "")
    with _conn(session_id, data_dir) as conn:
        rows = conn.execute(
            """
            SELECT value_json, last_shown_at FROM field_choice_memory
            WHERE session_id = ? AND intent = ? AND field_name = ?
              AND last_shown_at IS NOT NULL
            """,
            (session_id, intent, field_name),
        ).fetchall()
    worst = 0.0
    for row in rows:
        shown_at = _parse_iso(row["last_shown_at"])
        if shown_at is None or shown_at < cutoff:
            continue
        old_value = _json_loads(row["value_json"])
        if str(old_value.get("date") or "") != date_value:
            continue
        old_start = str(old_value.get("start") or "")
        if old_start == start_value:
            worst = min(worst, -12.0)
        elif old_start[:2] and old_start[:2] == start_value[:2]:
            worst = min(worst, -6.0)
    return worst


def decay_behavioral_patterns(session_id: str, data_dir: str) -> None:
    init_personalization_store(session_id, data_dir)
    now = datetime.now(timezone.utc)
    with _conn(session_id, data_dir) as conn:
        rows = conn.execute(
            "SELECT id, confidence, last_observed_at FROM behavioral_patterns WHERE status = 'active'"
        ).fetchall()
        for row in rows:
            observed = _parse_iso(row["last_observed_at"])
            if observed is None:
                continue
            inactive_days = (now - observed).days
            if inactive_days <= 30:
                continue
            periods = inactive_days // 30
            confidence = float(row["confidence"]) * (0.90 ** periods)
            status = "archived" if confidence < 0.25 else "active"
            conn.execute(
                """
                UPDATE behavioral_patterns
                SET confidence = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (confidence, status, _now(), row["id"]),
            )


def promote_preference_from_repeat_selects(
    session_id: str,
    data_dir: str,
    *,
    intent: str,
    field_name: str,
    scope_type: str,
    scope_key: str,
    threshold: int = 3,
) -> Optional[PreferenceRecord]:
    """Promote a recommendation-scope preference once the user has picked
    enough suggestions in the same scope, regardless of which specific
    suggestion each pick was.

    The semantic: a user who keeps selecting top picks for "evening" is
    telling us they trust recommendations in that time band; surface that
    as a preference so downstream ranking can amplify it.
    """
    init_personalization_store(session_id, data_dir)
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    with _conn(session_id, data_dir) as conn:
        rows = conn.execute(
            """
            SELECT selected_count, last_selected_at FROM field_choice_memory
            WHERE session_id = ? AND intent = ? AND field_name = ?
              AND scope_type = ? AND scope_key = ?
              AND selected_count > 0 AND last_selected_at IS NOT NULL
            """,
            (session_id, intent, field_name, scope_type, scope_key),
        ).fetchall()
    total_selected = 0
    for row in rows:
        selected_at = _parse_iso(row["last_selected_at"])
        if selected_at is None or selected_at < cutoff:
            continue
        total_selected += int(row["selected_count"])
    if total_selected < threshold:
        return None
    confidence = min(0.85, 0.55 + (0.05 * total_selected))
    return upsert_user_preference(
        session_id,
        data_dir,
        scope_type=scope_type,
        scope_key=scope_key,
        rule_type="time_band_engagement",
        value={
            "intent": intent,
            "field_name": field_name,
            "selected_count": total_selected,
        },
        confidence=confidence,
        source="repeat_select_promotion",
    )


def promote_patterns_from_choice_memory(
    session_id: str,
    data_dir: str,
    *,
    intent: str,
    field_name: str,
    scope_type: str,
    scope_key: str,
) -> list[BehavioralPatternRecord]:
    """Promote repeated selected field choices into generalized patterns.

    Thresholds:
    - at least 4 selected samples
    - at least 3 distinct selection days
    - at least 0.55 selection rate
    - only choices selected within the last 90 days
    """
    init_personalization_store(session_id, data_dir)
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    with _conn(session_id, data_dir) as conn:
        rows = conn.execute(
            """
            SELECT * FROM field_choice_memory
            WHERE session_id = ? AND intent = ? AND field_name = ?
              AND scope_type = ? AND scope_key = ?
              AND selected_count > 0 AND last_selected_at IS NOT NULL
            """,
            (session_id, intent, field_name, scope_type, scope_key),
        ).fetchall()

    samples: list[tuple[int, int, int, str]] = []
    shown_count = 0
    selected_count = 0
    distinct_days: set[str] = set()
    latest = ""
    for row in rows:
        selected_at = _parse_iso(row["last_selected_at"])
        if selected_at is None or selected_at < cutoff:
            continue
        value = _json_loads(row["value_json"])
        start = str(value.get("start") or "")
        if not _valid_time(start):
            continue
        row_selected = int(row["selected_count"])
        row_shown = int(row["shown_count"])
        selected_count += row_selected
        shown_count += row_shown
        distinct_days.add(selected_at.date().isoformat())
        latest = max(latest, selected_at.isoformat())
        minute_value = _time_to_minutes(start)
        for _ in range(row_selected):
            samples.append((minute_value, row_selected, row_shown, selected_at.isoformat()))

    if selected_count < 4 or len(distinct_days) < 3:
        return []
    selection_rate = selected_count / max(shown_count, 1)
    if selection_rate < 0.55:
        return []

    sample_minutes = sorted(item[0] for item in samples)
    if not sample_minutes:
        return []
    center = sample_minutes[len(sample_minutes) // 2]
    initial_confidence = min(0.85, 0.45 + (0.08 * selected_count))
    window_start = _minutes_to_time(max(0, center - 30))
    window_end = _minutes_to_time(min((23 * 60) + 59, center + 30))
    promoted: list[BehavioralPatternRecord] = []

    if scope_type == "category" and scope_key == "meeting" and center >= 10 * 60:
        promoted.append(
            upsert_behavioral_pattern(
                session_id,
                data_dir,
                scope_type=scope_type,
                scope_key=scope_key,
                pattern_type="time_preference",
                rule_type="start_after",
                value={"time": "10:00"},
                confidence=initial_confidence,
                sample_count=selected_count,
                distinct_days=len(distinct_days),
                selected_count=selected_count,
                shown_count=shown_count,
                observed_at=latest or None,
            )
        )
    else:
        promoted.append(
            upsert_behavioral_pattern(
                session_id,
                data_dir,
                scope_type=scope_type,
                scope_key=scope_key,
                pattern_type="time_preference",
                rule_type="preferred_window",
                value={"start": window_start, "end": window_end, "center": _minutes_to_time(center)},
                confidence=initial_confidence,
                sample_count=selected_count,
                distinct_days=len(distinct_days),
                selected_count=selected_count,
                shown_count=shown_count,
                observed_at=latest or None,
            )
        )
    return promoted


def _valid_time(value: str) -> bool:
    try:
        hour, minute = value.split(":", 1)
        return 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59
    except (ValueError, AttributeError):
        return False


def _time_to_minutes(value: str) -> int:
    hour, minute = [int(part) for part in value.split(":", 1)]
    return hour * 60 + minute


def _minutes_to_time(value: int) -> str:
    value = max(0, min(23 * 60 + 59, value))
    return f"{value // 60:02d}:{value % 60:02d}"
