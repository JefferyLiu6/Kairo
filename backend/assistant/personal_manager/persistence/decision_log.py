"""Per-turn decision log for the personal-manager typed workflow.

Records routing and interpretation decisions — not just executed actions.
Complements audit_events (which only covers action outcomes) by capturing:
  - What working memory held at turn start and how the turn resolved it
  - Candidate intents with confidence scores and extraction source
  - Why a particular execution mode was chosen
  - Which memory stores were read / written
  - Field-completion candidate rankings and signals
  - Final working-memory state transition

One row per turn. Designed for human debugging, not aggregated metrics.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .control_store import pm_db_path, _conn as _ctrl_conn


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turn_decision_log (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    duration_ms     INTEGER NOT NULL,
    message_preview TEXT NOT NULL,

    -- Working memory state at turn start
    wm_mode         TEXT,           -- awaiting_clarification | awaiting_choice | awaiting_freeform | null
    wm_source       TEXT,           -- plan_clarification | field_completion | time_slot_recommendation | …
    wm_stale        INTEGER,        -- 0 | 1
    wm_outcome      TEXT NOT NULL,  -- none | kept | replaced_new_request | replaced_stale |
                                    --   cancelled | resolved | more_options

    -- Intent extraction
    task_intents_json   TEXT NOT NULL,  -- [{intent, confidence, source, missing:[]}]
    plan_confidence     REAL NOT NULL,
    extraction_source   TEXT NOT NULL,  -- deterministic | model | pending_resolution

    -- Routing decision
    execution_mode  TEXT NOT NULL,  -- approve | reject | fallback | time_slot |
                                    --   field_choices | clarification | lookup_error | executed
    routing_reason  TEXT NOT NULL,

    -- Blocker / field completion
    blocker_type    TEXT,           -- missing_fields | low_confidence | lookup_failure | time_slot | null
    blocker_missing TEXT NOT NULL DEFAULT '[]',  -- JSON array of missing field names
    fc_candidates   TEXT NOT NULL DEFAULT '[]',  -- top 3 candidates with scores + signals

    -- Memory I/O
    memory_read     TEXT NOT NULL DEFAULT '[]',   -- ["working_memory", "preferences", "patterns", …]
    memory_written  TEXT NOT NULL DEFAULT '[]',   -- ["working_memory:awaiting_choice", "profile_md", …]

    -- Final
    reply_preview   TEXT NOT NULL,
    wm_after        TEXT NOT NULL   -- active:awaiting_choice | active:awaiting_clarification |
                                    --   resolved | cancelled | replaced | none
);
CREATE INDEX IF NOT EXISTS idx_tdl_session_created
    ON turn_decision_log(session_id, created_at DESC);
"""


def init_decision_log(session_id: str, data_dir: str) -> None:
    with _ctrl_conn(pm_db_path(session_id, data_dir)) as conn:
        conn.executescript(_SCHEMA)


# ── Accumulator dataclass ─────────────────────────────────────────────────────

@dataclass
class TurnDecision:
    """Mutable accumulator populated throughout run_typed_pm_turn.

    Call .mark_start() once at the beginning of the turn, then update fields
    as each decision is made. Call .persist() at the end (ideally in a
    try/finally so it fires even on exception).
    """
    session_id: str
    message_preview: str

    # Working memory
    wm_mode: Optional[str] = None
    wm_source: Optional[str] = None
    wm_stale: bool = False
    wm_outcome: str = "none"

    # Intent
    task_intents: list[dict[str, Any]] = field(default_factory=list)
    plan_confidence: float = 0.0
    extraction_source: str = ""

    # Routing
    execution_mode: str = "fallback"
    routing_reason: str = ""

    # Blocker
    blocker_type: Optional[str] = None
    blocker_missing: list[str] = field(default_factory=list)
    fc_candidates: list[dict[str, Any]] = field(default_factory=list)

    # Memory I/O
    memory_read: list[str] = field(default_factory=list)
    memory_written: list[str] = field(default_factory=list)

    # Final
    reply_preview: str = ""
    wm_after: str = "none"

    _started_at: float = field(default_factory=time.monotonic, repr=False)

    # ── Convenience mutators ──────────────────────────────────────────────────

    def set_working_memory(self, pending: dict[str, Any]) -> None:
        meta = pending.get("_working_memory") or {}
        self.wm_mode = str(meta.get("mode") or pending.get("type") or "")
        self.wm_source = str(meta.get("source") or "")
        self.wm_stale = bool(meta.get("stale"))
        self.memory_read.append("working_memory")

    def set_plan(self, plan: Any) -> None:
        self.plan_confidence = float(plan.confidence)
        self.extraction_source = str(plan.source)
        self.task_intents = [
            {
                "intent": task.intent.value,
                "confidence": round(float(task.confidence), 4),
                "source": str(task.source),
                "missing": list(task.missing_fields),
            }
            for task in plan.tasks
        ]

    def set_fc_candidates(self, choices: list[dict[str, Any]]) -> None:
        self.fc_candidates = [
            {
                "id": c.get("id"),
                "label": c.get("label"),
                "score": c.get("score"),
                "candidate_confidence": c.get("candidate_confidence"),
                "source": c.get("source"),
                "reason": c.get("reason"),
                "signals": c.get("signals", {}),
                "scope": c.get("scope", {}),
            }
            for c in choices[:3]
        ]
        for tag in ("preferences", "patterns", "field_choices", "calendar"):
            if tag not in self.memory_read:
                self.memory_read.append(tag)

    def route(self, mode: str, reason: str) -> None:
        self.execution_mode = mode
        self.routing_reason = reason

    def set_reply(self, reply: str) -> None:
        self.reply_preview = reply[:300]

    def persist(self, data_dir: str) -> None:
        try:
            duration_ms = int((time.monotonic() - self._started_at) * 1000)
            _write_decision(self, data_dir, duration_ms)
        except Exception:
            pass  # Never crash the turn just because logging failed


# ── Persistence ───────────────────────────────────────────────────────────────

def _write_decision(d: TurnDecision, data_dir: str, duration_ms: int) -> None:
    init_decision_log(d.session_id, data_dir)
    now = datetime.now(timezone.utc).isoformat()
    with _ctrl_conn(pm_db_path(d.session_id, data_dir)) as conn:
        conn.execute(
            """
            INSERT INTO turn_decision_log (
                id, session_id, created_at, duration_ms, message_preview,
                wm_mode, wm_source, wm_stale, wm_outcome,
                task_intents_json, plan_confidence, extraction_source,
                execution_mode, routing_reason,
                blocker_type, blocker_missing, fc_candidates,
                memory_read, memory_written,
                reply_preview, wm_after
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?
            )
            """,
            (
                uuid.uuid4().hex[:10],
                d.session_id,
                now,
                duration_ms,
                d.message_preview[:300],
                d.wm_mode or None,
                d.wm_source or None,
                int(d.wm_stale),
                d.wm_outcome,
                json.dumps(d.task_intents, ensure_ascii=False),
                d.plan_confidence,
                d.extraction_source,
                d.execution_mode,
                d.routing_reason,
                d.blocker_type,
                json.dumps(d.blocker_missing, ensure_ascii=False),
                json.dumps(d.fc_candidates, ensure_ascii=False),
                json.dumps(d.memory_read, ensure_ascii=False),
                json.dumps(d.memory_written, ensure_ascii=False),
                d.reply_preview,
                d.wm_after,
            ),
        )


# ── Orchestrator turn logger ──────────────────────────────────────────────────

def log_orchestrator_turn(
    session_id: str,
    data_dir: str,
    *,
    message: str,
    route: str,                  # "DIRECT" | "DELEGATE"
    route_reason: str,
    intent: str,                 # structured action intent or "direct"
    confidence: float,
    pm_prompt: str,
    is_write: bool,
    harness_verdict: str,        # "pass" | "retry" | "fallback" | "n/a"
    harness_reason: str,
    retry_count: int,
    reply: str,
    duration_ms: int,
) -> None:
    """Write one orchestrator turn into the same turn_decision_log table."""
    try:
        init_decision_log(session_id, data_dir)
        now = datetime.now(timezone.utc).isoformat()
        task_intents = [{"intent": intent, "confidence": round(confidence, 4), "source": "orchestrator", "missing": []}]
        routing_reason = (
            f"[orchestrator] route={route} | harness={harness_verdict} retries={retry_count} | {route_reason}"
            if route == "DELEGATE"
            else f"[orchestrator] route={route} | {route_reason}"
        )
        memory_written = ["profile"] if is_write else []
        with _ctrl_conn(pm_db_path(session_id, data_dir)) as conn:
            conn.execute(
                """
                INSERT INTO turn_decision_log (
                    id, session_id, created_at, duration_ms, message_preview,
                    wm_mode, wm_source, wm_stale, wm_outcome,
                    task_intents_json, plan_confidence, extraction_source,
                    execution_mode, routing_reason,
                    blocker_type, blocker_missing, fc_candidates,
                    memory_read, memory_written,
                    reply_preview, wm_after
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?
                )
                """,
                (
                    uuid.uuid4().hex[:10],
                    session_id,
                    now,
                    duration_ms,
                    message[:300],
                    None,           # wm_mode
                    "orchestrator", # wm_source
                    0,              # wm_stale
                    "resolved" if route == "DELEGATE" else "none",
                    json.dumps(task_intents, ensure_ascii=False),
                    confidence,
                    "orchestrator",
                    "EXECUTED" if harness_verdict in ("pass", "n/a") else harness_verdict.upper(),
                    routing_reason,
                    None,           # blocker_type
                    "[]",           # blocker_missing
                    json.dumps([{"id": "pm_prompt", "label": pm_prompt[:200]}], ensure_ascii=False),
                    json.dumps(["orchestrator_memory"], ensure_ascii=False),
                    json.dumps(memory_written, ensure_ascii=False),
                    reply[:300],
                    "none",
                ),
            )
    except Exception:
        pass  # Never crash the turn because logging failed


# ── Query helpers ─────────────────────────────────────────────────────────────

def list_turn_decisions(
    session_id: str,
    data_dir: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    init_decision_log(session_id, data_dir)
    with _ctrl_conn(pm_db_path(session_id, data_dir)) as conn:
        rows = conn.execute(
            """
            SELECT * FROM turn_decision_log
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (session_id, max(1, min(limit, 200))),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: Any) -> dict[str, Any]:
    def _loads(v: Any) -> Any:
        try:
            return json.loads(v) if v else []
        except Exception:
            return v

    return {
        "id": row["id"],
        "sessionId": row["session_id"],
        "createdAt": row["created_at"],
        "durationMs": row["duration_ms"],
        "messagePreview": row["message_preview"],
        "workingMemory": {
            "mode": row["wm_mode"],
            "source": row["wm_source"],
            "stale": bool(row["wm_stale"]),
            "outcome": row["wm_outcome"],
        },
        "intentTrace": {
            "tasks": _loads(row["task_intents_json"]),
            "planConfidence": row["plan_confidence"],
            "extractionSource": row["extraction_source"],
        },
        "routing": {
            "mode": row["execution_mode"],
            "reason": row["routing_reason"],
        },
        "blocker": {
            "type": row["blocker_type"],
            "missing": _loads(row["blocker_missing"]),
            "fcCandidates": _loads(row["fc_candidates"]),
        },
        "memoryIO": {
            "read": _loads(row["memory_read"]),
            "written": _loads(row["memory_written"]),
        },
        "replyPreview": row["reply_preview"],
        "wmAfter": row["wm_after"],
    }
