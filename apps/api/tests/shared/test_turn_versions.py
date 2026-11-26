"""Each web-chat turn records which prompts, models and receipts produced it."""
from __future__ import annotations

import os
import sqlite3


from kairo.core.prompt import Prompt, collecting_prompts, record_prompt
from kairo.storage.control_store import pm_db_path
from kairo.storage.decision_log import list_turn_decisions, log_orchestrator_turn

THREAD = "pm-turn-versions"


def _log(tmp_path, **extra):
    log_orchestrator_turn(
        THREAD, str(tmp_path), message="add milk", route="DELEGATE", route_reason="intent=add_todo",
        intent="add_todo", confidence=0.9, pm_prompt="add todo milk", is_write=True,
        harness_verdict="pass", harness_reason="", retry_count=0, reply="Added task: milk.", duration_ms=5, **extra)


# ── Which prompts a turn used ─────────────────────────────────────────────────

def test_prompts_sent_inside_a_collector_are_noted_by_name_and_version():
    route, judge = Prompt("route", "Decide."), Prompt("judge", "Judge.")
    with collecting_prompts() as used:
        record_prompt(route)
        record_prompt(judge)
        record_prompt(route)                      # the same prompt twice is one entry
        record_prompt("plain text is not a versioned prompt")
    assert used == {"route": route.version, "judge": judge.version}


def test_recording_a_prompt_outside_a_collector_is_harmless():
    record_prompt(Prompt("route", "Decide."))
    with collecting_prompts() as used:
        pass
    assert used == {}


# ── The decision log row ──────────────────────────────────────────────────────

def test_an_orchestrator_row_keeps_the_versions_it_was_given(tmp_path):
    versions = {"prompts": {"translate": "1a478512"}, "models": {"orchestrator": "gpt-5.4-mini", "pm": "gpt-5.4-mini"},
                "receipts": ["verified"]}
    _log(tmp_path, versions=versions)
    (row,) = list_turn_decisions(THREAD, str(tmp_path))
    assert row["versions"] == versions


def test_a_row_written_without_versions_reads_back_as_empty(tmp_path):
    _log(tmp_path)
    assert list_turn_decisions(THREAD, str(tmp_path))[0]["versions"] == {}


# The table as it was before versions were recorded (no versions_json column).
_OLD_TABLE = """
CREATE TABLE turn_decision_log (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, created_at TEXT NOT NULL, duration_ms INTEGER NOT NULL,
    message_preview TEXT NOT NULL, wm_mode TEXT, wm_source TEXT, wm_stale INTEGER, wm_outcome TEXT NOT NULL,
    task_intents_json TEXT NOT NULL, plan_confidence REAL NOT NULL, extraction_source TEXT NOT NULL,
    execution_mode TEXT NOT NULL, routing_reason TEXT NOT NULL, blocker_type TEXT,
    blocker_missing TEXT NOT NULL DEFAULT '[]', fc_candidates TEXT NOT NULL DEFAULT '[]',
    memory_read TEXT NOT NULL DEFAULT '[]', memory_written TEXT NOT NULL DEFAULT '[]',
    reply_preview TEXT NOT NULL, wm_after TEXT NOT NULL
)"""


def test_a_decision_log_created_before_versions_existed_is_upgraded_in_place(tmp_path):
    db = pm_db_path(THREAD, str(tmp_path))
    os.makedirs(os.path.dirname(db))
    with sqlite3.connect(db) as conn:
        conn.execute(_OLD_TABLE)
        conn.execute(
            "INSERT INTO turn_decision_log (id, session_id, created_at, duration_ms, message_preview, wm_outcome,"
            " task_intents_json, plan_confidence, extraction_source, execution_mode, routing_reason, reply_preview,"
            " wm_after) VALUES ('old1', ?, '2026-01-01T00:00:00+00:00', 1, 'old turn', 'none', '[]', 1.0,"
            " 'deterministic', 'executed', 'before versions', 'ok', 'none')", (THREAD,))

    _log(tmp_path, versions={"prompts": {"route": "ff6b3b60"}})

    newest, oldest = list_turn_decisions(THREAD, str(tmp_path))
    assert newest["versions"] == {"prompts": {"route": "ff6b3b60"}}
    assert oldest["messagePreview"] == "old turn" and oldest["versions"] == {}


# ── A real turn ───────────────────────────────────────────────────────────────
