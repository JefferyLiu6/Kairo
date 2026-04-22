"""Semantic long-term memory for normalized PM recall."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from .control_store import pm_db_path


@dataclass(frozen=True)
class SemanticMemoryRecord:
    id: str
    session_id: str
    memory_type: str
    subject: str
    predicate: str
    object: str
    qualifiers: dict[str, Any]
    polarity: str
    confidence: float
    stability: str
    scheduling_relevance: str
    sensitivity: str
    source: str
    evidence: str
    status: str
    created_at: str
    updated_at: str


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


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def init_semantic_memory_store(session_id: str, data_dir: str) -> None:
    with _conn(session_id, data_dir) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS semantic_memory (
                id                   TEXT PRIMARY KEY,
                session_id           TEXT NOT NULL,
                memory_type          TEXT NOT NULL,
                subject              TEXT NOT NULL,
                predicate            TEXT NOT NULL,
                object               TEXT NOT NULL,
                qualifiers_json      TEXT NOT NULL,
                polarity             TEXT NOT NULL,
                confidence           REAL NOT NULL,
                stability            TEXT NOT NULL,
                scheduling_relevance TEXT NOT NULL,
                sensitivity          TEXT NOT NULL,
                source               TEXT NOT NULL,
                evidence             TEXT NOT NULL,
                status               TEXT NOT NULL DEFAULT 'active',
                created_at           TEXT NOT NULL,
                updated_at           TEXT NOT NULL,
                UNIQUE(session_id, subject, predicate, object, polarity)
            );
            CREATE INDEX IF NOT EXISTS idx_semantic_memory_session
                ON semantic_memory(session_id, status, memory_type, polarity, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_semantic_memory_object
                ON semantic_memory(session_id, status, object);
            """
        )


def _row_to_record(row: sqlite3.Row) -> SemanticMemoryRecord:
    return SemanticMemoryRecord(
        id=row["id"],
        session_id=row["session_id"],
        memory_type=row["memory_type"],
        subject=row["subject"],
        predicate=row["predicate"],
        object=row["object"],
        qualifiers=_json_loads(row["qualifiers_json"]),
        polarity=row["polarity"],
        confidence=float(row["confidence"]),
        stability=row["stability"],
        scheduling_relevance=row["scheduling_relevance"],
        sensitivity=row["sensitivity"],
        source=row["source"],
        evidence=row["evidence"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def upsert_semantic_memory(
    session_id: str,
    data_dir: str,
    *,
    memory_type: str,
    subject: str,
    predicate: str,
    object_value: str,
    qualifiers: Optional[dict[str, Any]] = None,
    polarity: str = "positive",
    confidence: float = 0.75,
    stability: str = "stable",
    scheduling_relevance: str = "none",
    sensitivity: str = "low",
    source: str = "deterministic",
    evidence: str = "",
) -> SemanticMemoryRecord:
    init_semantic_memory_store(session_id, data_dir)
    now = _now()
    normalized_object = " ".join(object_value.strip().split()).lower()
    qualifiers_json = _json_dumps(qualifiers or {})
    with _conn(session_id, data_dir) as conn:
        conn.execute(
            """
            INSERT INTO semantic_memory (
                id, session_id, memory_type, subject, predicate, object,
                qualifiers_json, polarity, confidence, stability,
                scheduling_relevance, sensitivity, source, evidence,
                status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(session_id, subject, predicate, object, polarity)
            DO UPDATE SET
                memory_type = excluded.memory_type,
                qualifiers_json = excluded.qualifiers_json,
                confidence = MAX(semantic_memory.confidence, excluded.confidence),
                stability = excluded.stability,
                scheduling_relevance = excluded.scheduling_relevance,
                sensitivity = excluded.sensitivity,
                source = CASE
                    WHEN excluded.confidence > semantic_memory.confidence
                    THEN excluded.source ELSE semantic_memory.source
                END,
                evidence = CASE
                    WHEN excluded.confidence > semantic_memory.confidence
                    THEN excluded.evidence ELSE semantic_memory.evidence
                END,
                status = 'active',
                updated_at = excluded.updated_at
            """,
            (
                uuid.uuid4().hex[:10],
                session_id,
                memory_type,
                subject,
                predicate,
                normalized_object,
                qualifiers_json,
                polarity,
                _clamp(confidence),
                stability,
                scheduling_relevance,
                sensitivity,
                source,
                evidence,
                now,
                now,
            ),
        )
        row = conn.execute(
            """
            SELECT * FROM semantic_memory
            WHERE session_id = ? AND subject = ? AND predicate = ?
              AND object = ? AND polarity = ?
            """,
            (session_id, subject, predicate, normalized_object, polarity),
        ).fetchone()
    return _row_to_record(row)


def list_semantic_memory(
    session_id: str,
    data_dir: str,
    *,
    memory_types: Optional[set[str]] = None,
    polarity: Optional[str] = None,
    status: str = "active",
) -> list[SemanticMemoryRecord]:
    init_semantic_memory_store(session_id, data_dir)
    with _conn(session_id, data_dir) as conn:
        rows = conn.execute(
            """
            SELECT * FROM semantic_memory
            WHERE session_id = ? AND status = ?
            ORDER BY confidence DESC, updated_at DESC
            """,
            (session_id, status),
        ).fetchall()
    records = [_row_to_record(row) for row in rows]
    if memory_types is not None:
        records = [record for record in records if record.memory_type in memory_types]
    if polarity is not None:
        records = [record for record in records if record.polarity == polarity]
    return records
