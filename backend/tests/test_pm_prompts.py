"""End-to-end prompt tests for Kairo.

Each test sends a real user message through run_pm and asserts both the
reply text and the resulting state (schedule, todos, approvals, journal).
Model extraction is disabled (model="unused") so every case runs through
the deterministic extractor.  For phrases that require model understanding,
_install_fake_model_extractions patches the model call with a realistic
response so the rest of the pipeline runs for real.
"""
from __future__ import annotations

from datetime import date, timedelta

import assistant.personal_manager.workflow as pm_workflow
from assistant.personal_manager.agent import PMConfig, run_pm
from assistant.personal_manager.persistence.control_store import list_approval_requests, pm_db_path
from assistant.personal_manager.persistence.journal import journal_read
from assistant.personal_manager.persistence.store import (
    ScheduleData,
    ScheduleEntry,
    load_schedule,
    load_todos,
    save_schedule,
)
from assistant.personal_manager.domain.types import PMExtraction


# ─── helpers ────────────────────────────────────────────────────────────────

def _cfg(tmp_path, session_id: str = "pm-test") -> PMConfig:
    return PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id=session_id,
    )


def _tomorrow() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


def _fake_model(monkeypatch, responses: dict[str, dict]) -> None:
    """Patch _extract_pm_request_with_model with canned responses."""
    def _extract(message, _config):
        data = responses.get(message)
        if data is None:
            return None
        return PMExtraction(source="model_structured", **data)
    monkeypatch.setattr(pm_workflow, "_extract_pm_request_with_model", _extract)


# ─── schedule: create ───────────────────────────────────────────────────────

def test_prompt_add_breakfast_creates_schedule(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("add breakfast at 8am tomorrow", cfg)

    schedule = load_schedule("pm-test", str(tmp_path))
    assert "Done!" in reply and "breakfast" in reply.lower()
    assert len(schedule.entries) == 1
    entry = schedule.entries[0]
    assert "breakfast" in entry.title.lower()
    assert entry.start == "08:00"
    assert entry.date == _tomorrow()


def test_prompt_informal_caps_creates_schedule_without_clarification(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("I NEED TO EAT LUNCH AT 12PM TMR", cfg)

    schedule = load_schedule("pm-test", str(tmp_path))
    assert "Done!" in reply
    assert len(schedule.entries) == 1
    assert schedule.entries[0].start == "12:00"
    assert schedule.entries[0].date == _tomorrow()
    assert list_approval_requests("pm-test", str(tmp_path), status="pending") == []


def test_prompt_ambiguous_schedule_offers_ranked_choices(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("Schedule dentist appointment", cfg)

    assert load_schedule("pm-test", str(tmp_path)).entries == []
    assert list_approval_requests("pm-test", str(tmp_path), status="pending") == []
    assert "My recommendation is 1" in reply
    assert "Or type another date/time." in reply


def test_prompt_multiple_times_in_one_message(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("add tomorrow 8am and tomorrow 10am to my calendar", cfg)

    schedule = load_schedule("pm-test", str(tmp_path))
    assert "Done! Added 2" in reply
    starts = {e.start for e in schedule.entries}
    assert "08:00" in starts
    assert "10:00" in starts


# ─── schedule: remove ───────────────────────────────────────────────────────

def test_prompt_cancel_event_by_title_creates_approval(tmp_path):
    cfg = _cfg(tmp_path)
    save_schedule(
        ScheduleData(entries=[ScheduleEntry(id="b1", title="Breakfast", date=_tomorrow(), start="08:00", end="09:00")]),
        "pm-test", str(tmp_path),
    )

    reply = run_pm("cancel my breakfast tomorrow", cfg)

    approvals = list_approval_requests("pm-test", str(tmp_path), status="pending")
    schedule = load_schedule("pm-test", str(tmp_path))
    assert "Approval required" in reply
    assert len(approvals) == 1
    assert approvals[0].action_type == "schedule_remove"
    assert len(schedule.entries) == 1  # not deleted yet


def test_prompt_delete_event_by_title_creates_approval(tmp_path):
    cfg = _cfg(tmp_path)
    save_schedule(
        ScheduleData(entries=[ScheduleEntry(id="s1", title="Standup", date=_tomorrow(), start="09:00", end="09:30")]),
        "pm-test", str(tmp_path),
    )

    reply = run_pm("delete my standup tomorrow", cfg)

    approvals = list_approval_requests("pm-test", str(tmp_path), status="pending")
    assert "Approval required" in reply
    assert len(approvals) == 1


def test_prompt_remove_by_time_creates_approval(tmp_path):
    cfg = _cfg(tmp_path)
    save_schedule(
        ScheduleData(entries=[ScheduleEntry(id="m1", title="Meeting", date=_tomorrow(), start="15:00", end="16:00")]),
        "pm-test", str(tmp_path),
    )

    reply = run_pm("remove my 3pm tomorrow", cfg)

    approvals = list_approval_requests("pm-test", str(tmp_path), status="pending")
    assert "Approval required" in reply
    assert len(approvals) == 1


# ─── schedule: approve / reject ─────────────────────────────────────────────

def test_prompt_approve_removes_the_event(tmp_path):
    cfg = _cfg(tmp_path)
    save_schedule(
        ScheduleData(entries=[ScheduleEntry(id="b1", title="Breakfast", date=_tomorrow(), start="08:00", end="09:00")]),
        "pm-test", str(tmp_path),
    )
    run_pm("cancel my breakfast tomorrow", cfg)
    approval = list_approval_requests("pm-test", str(tmp_path), status="pending")[0]

    reply = run_pm(f"approve {approval.id}", cfg)

    assert "Removed" in reply
    assert load_schedule("pm-test", str(tmp_path)).entries == []
    approved = list_approval_requests("pm-test", str(tmp_path))[0]
    assert approved.status == "executed"


def test_prompt_reject_leaves_event_intact(tmp_path):
    cfg = _cfg(tmp_path)
    save_schedule(
        ScheduleData(entries=[ScheduleEntry(id="b1", title="Breakfast", date=_tomorrow(), start="08:00", end="09:00")]),
        "pm-test", str(tmp_path),
    )
    run_pm("cancel my breakfast tomorrow", cfg)
    approval = list_approval_requests("pm-test", str(tmp_path), status="pending")[0]

    reply = run_pm(f"reject {approval.id}", cfg)

    assert "Got it" in reply or "cancelled" in reply
    assert len(load_schedule("pm-test", str(tmp_path)).entries) == 1
    rejected = list_approval_requests("pm-test", str(tmp_path))[0]
    assert rejected.status == "rejected"


# ─── schedule: update ───────────────────────────────────────────────────────

def test_prompt_reschedule_event_creates_update_approval(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    save_schedule(
        ScheduleData(entries=[ScheduleEntry(id="b1", title="Breakfast", date=_tomorrow(), start="08:00", end="09:00")]),
        "pm-test", str(tmp_path),
    )
    _fake_model(monkeypatch, {
        "reschedule my breakfast to 9am": {
            "intent": "UPDATE_SCHEDULE_EVENT",
            "entities": {"query": "breakfast", "start": "09:00", "end": "10:00"},
            "confidence": 0.9,
            "missing_fields": [],
            "reasoning_summary": "Reschedule breakfast to 09:00",
        }
    })

    reply = run_pm("reschedule my breakfast to 9am", cfg)

    approvals = list_approval_requests("pm-test", str(tmp_path), status="pending")
    assert "Approval required" in reply
    assert approvals[0].action_type == "schedule_update"
    assert load_schedule("pm-test", str(tmp_path)).entries[0].start == "08:00"  # not changed yet


def test_prompt_approve_update_changes_time(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    save_schedule(
        ScheduleData(entries=[ScheduleEntry(id="b1", title="Breakfast", date=_tomorrow(), start="08:00", end="09:00")]),
        "pm-test", str(tmp_path),
    )
    _fake_model(monkeypatch, {
        "reschedule my breakfast to 9am": {
            "intent": "UPDATE_SCHEDULE_EVENT",
            "entities": {"query": "breakfast", "start": "09:00", "end": "10:00"},
            "confidence": 0.9,
            "missing_fields": [],
            "reasoning_summary": "Reschedule breakfast to 09:00",
        }
    })
    run_pm("reschedule my breakfast to 9am", cfg)
    approval = list_approval_requests("pm-test", str(tmp_path), status="pending")[0]

    reply = run_pm(f"approve {approval.id}", cfg)

    assert "Done!" in reply
    assert load_schedule("pm-test", str(tmp_path)).entries[0].start == "09:00"


def test_prompt_vague_move_without_destination_offers_choices(tmp_path):
    cfg = _cfg(tmp_path)
    save_schedule(
        ScheduleData(entries=[ScheduleEntry(id="b1", title="Breakfast")]),
        "pm-test", str(tmp_path),
    )

    reply = run_pm("move my breakfast", cfg)

    assert list_approval_requests("pm-test", str(tmp_path), status="pending") == []
    assert "My recommendation is 1" in reply
    assert "Or type another date/time." in reply


# ─── todos ───────────────────────────────────────────────────────────────────

def test_prompt_remind_me_creates_todo(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("remind me to call the bank", cfg)

    todos = load_todos("pm-test", str(tmp_path))
    assert "Added" in reply
    assert len(todos.items) == 1
    assert "bank" in todos.items[0].title.lower()
    assert list_approval_requests("pm-test", str(tmp_path), status="pending") == []


def test_prompt_add_task_creates_todo_with_due(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("add task to submit the report by tomorrow", cfg)

    todos = load_todos("pm-test", str(tmp_path))
    assert "Added" in reply
    assert len(todos.items) == 1
    assert todos.items[0].due == _tomorrow()


def test_prompt_complete_todo_marks_it_done(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    run_pm("remind me to call the bank", cfg)
    todo_id = load_todos("pm-test", str(tmp_path)).items[0].id
    _fake_model(monkeypatch, {
        "mark call the bank as done": {
            "intent": "COMPLETE_TODO",
            "entities": {"query": "call the bank"},
            "confidence": 0.9,
            "missing_fields": [],
            "reasoning_summary": "Complete bank call todo",
        }
    })

    reply = run_pm("mark call the bank as done", cfg)

    todos = load_todos("pm-test", str(tmp_path))
    assert "Done!" in reply
    done = next(t for t in todos.items if t.id == todo_id)
    assert done.done is True


def test_prompt_remove_todo_creates_approval(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    run_pm("remind me to call the bank", cfg)
    _fake_model(monkeypatch, {
        "delete the bank call task": {
            "intent": "REMOVE_TODO",
            "entities": {"query": "bank"},
            "confidence": 0.88,
            "missing_fields": [],
            "reasoning_summary": "Remove bank call todo",
        }
    })

    reply = run_pm("delete the bank call task", cfg)

    approvals = list_approval_requests("pm-test", str(tmp_path), status="pending")
    assert "Approval required" in reply
    assert approvals[0].action_type == "todo_remove"
    assert len(load_todos("pm-test", str(tmp_path)).items) == 1


# ─── journal ─────────────────────────────────────────────────────────────────

def test_prompt_journal_append_saves_entry(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("journal: had a productive morning", cfg)

    db = pm_db_path("pm-test", str(tmp_path))
    entries = journal_read(db, limit=5)
    assert "Saved" in reply
    assert "productive" in entries


def test_prompt_journal_read_shows_entries(tmp_path):
    cfg = _cfg(tmp_path)
    run_pm("journal: ran 5km today", cfg)
    run_pm("journal: finished the report", cfg)

    reply = run_pm("show my journal", cfg)

    assert "5km" in reply or "ran" in reply
    assert "report" in reply


def test_prompt_journal_search_returns_matching_entries(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    run_pm("journal: had a productive morning session", cfg)
    run_pm("journal: tired afternoon, skipped workout", cfg)
    _fake_model(monkeypatch, {
        "search my journal for productive": {
            "intent": "JOURNAL_ACTION",
            "entities": {"operation": "search", "query": "productive"},
            "confidence": 0.92,
            "missing_fields": [],
            "reasoning_summary": "FTS search journal for 'productive'",
        }
    })

    reply = run_pm("search my journal for productive", cfg)

    assert "productive" in reply.lower()
    assert "skipped workout" not in reply


# ─── memory ──────────────────────────────────────────────────────────────────

def test_prompt_remember_saves_to_vault(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("remember that I prefer dark roast coffee", cfg)

    profile = (tmp_path / "vault" / "PROFILE.md").read_text(encoding="utf-8")
    assert "Remembered" in reply
    assert "dark roast coffee" in profile


def test_prompt_remember_sensitive_fact_creates_approval(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("remember my bank account password is hunter2", cfg)

    approvals = list_approval_requests("pm-test", str(tmp_path), status="pending")
    assert "Approval required" in reply
    assert len(approvals) == 1


# ─── unknown / negative cases ────────────────────────────────────────────────

def test_prompt_cancel_subscription_does_not_touch_schedule(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("cancel my subscription", cfg)

    # must not create approvals or schedule mutations
    assert list_approval_requests("pm-test", str(tmp_path), status="pending") == []
    assert load_schedule("pm-test", str(tmp_path)).entries == []
    # reply should be non-empty (coaching or clarification)
    assert reply.strip()


def test_prompt_unknown_stateful_phrase_does_not_execute(tmp_path):
    cfg = _cfg(tmp_path)

    run_pm("Write a note that launch prep matters", cfg)

    assert load_todos("pm-test", str(tmp_path)).items == []
    assert load_schedule("pm-test", str(tmp_path)).entries == []
    assert list_approval_requests("pm-test", str(tmp_path), status="pending") == []


# ─── full multi-turn round trip ──────────────────────────────────────────────

def test_prompt_full_schedule_approval_round_trip(tmp_path):
    """Create → list → delete (approval) → approve → verify gone."""
    cfg = _cfg(tmp_path)

    # 1. create
    create_reply = run_pm("add standup at 9am tomorrow", cfg)
    assert "Done!" in create_reply

    # 2. delete triggers approval
    delete_reply = run_pm("delete my standup tomorrow", cfg)
    assert "Approval required" in delete_reply
    assert len(load_schedule("pm-test", str(tmp_path)).entries) == 1  # not yet

    # 3. approve
    approval = list_approval_requests("pm-test", str(tmp_path), status="pending")[0]
    approve_reply = run_pm(f"approve {approval.id}", cfg)
    assert "Removed" in approve_reply
    assert load_schedule("pm-test", str(tmp_path)).entries == []


def test_prompt_full_todo_lifecycle(tmp_path, monkeypatch):
    """Add → complete → remove."""
    cfg = _cfg(tmp_path)
    _fake_model(monkeypatch, {
        "done with the report task": {
            "intent": "COMPLETE_TODO",
            "entities": {"query": "report"},
            "confidence": 0.88,
            "missing_fields": [],
            "reasoning_summary": "Complete report todo",
        },
        "remove the report task": {
            "intent": "REMOVE_TODO",
            "entities": {"query": "report"},
            "confidence": 0.88,
            "missing_fields": [],
            "reasoning_summary": "Remove report todo",
        },
    })

    add_reply = run_pm("add task to finish the report", cfg)
    assert "Added" in add_reply
    assert len(load_todos("pm-test", str(tmp_path)).items) == 1

    done_reply = run_pm("done with the report task", cfg)
    assert "Done!" in done_reply
    assert load_todos("pm-test", str(tmp_path)).items[0].done is True

    remove_reply = run_pm("remove the report task", cfg)
    assert "Approval required" in remove_reply
    approval = list_approval_requests("pm-test", str(tmp_path), status="pending")[0]

    approve_reply = run_pm(f"approve {approval.id}", cfg)
    assert "Removed" in approve_reply
    assert load_todos("pm-test", str(tmp_path)).items == []
