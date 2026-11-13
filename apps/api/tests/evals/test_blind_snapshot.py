"""The snapshot reads what Kairo really has on disk, in one shape whatever path wrote it."""
from __future__ import annotations

import json
import os

from evals.blind.snapshot import read_snapshot
from kairo.agents.personal_manager.application import clarification
from kairo.core.domain.types import PMIntent, PMPlanExtraction, PMTaskExtraction
from kairo.storage.control_store import create_approval_request, pm_db_path, reject_approval_request
from kairo.storage.habits import habit_add, habit_checkin
from kairo.storage.journal import journal_append
from kairo.storage.profile import append_profile_fact
from kairo.storage.store import load_private, save_private, schedule_add, todo_add

USER, THREAD = "blind-user", "pm-blind-thread"


def _read(tmp_path):
    return read_snapshot(USER, THREAD, str(tmp_path), vault_dir=os.path.dirname(pm_db_path(USER, str(tmp_path))))


def test_a_new_user_has_an_empty_snapshot(tmp_path):
    snapshot = _read(tmp_path)
    assert all(not snapshot.items(store) for store in ("schedule", "todos", "habits", "journal", "memory"))
    assert snapshot.approvals == {} and snapshot.question is None


def test_schedule_and_todos_come_back_as_plain_items(tmp_path):
    data = str(tmp_path)
    schedule_add(json.dumps([{
        "title": "gym", "date": "2026-11-13", "start": "06:00", "end": "07:00",
        "recurrence": {"freq": "weekly", "by_day": ["MO", "WE", "FR"]},
    }]), USER, data)
    todo_add("renew passport", "2026-12-01", USER, data)

    snapshot = _read(tmp_path)

    (event,) = snapshot.items("schedule")
    assert event["title"] == "gym" and event["start"] == "06:00"
    assert event["recurrence"]["by_day"] == ["MO", "WE", "FR"] and event["exceptions"] == []
    (todo,) = snapshot.items("todos")
    assert todo["title"] == "renew passport" and todo["due"] == "2026-12-01" and todo["done"] is False


def test_habits_carry_their_checkins_and_journal_entries_their_text(tmp_path):
    db = pm_db_path(USER, str(tmp_path))
    os.makedirs(os.path.dirname(db), exist_ok=True)
    habit_id = habit_add("morning run", db).split("[")[1].split("]")[0]
    habit_checkin(habit_id, db, "2026-11-12")
    journal_append("slept badly, skipped the gym", db)

    snapshot = _read(tmp_path)

    (habit,) = snapshot.items("habits")
    assert habit["name"] == "morning run" and habit["checkins"] == ["2026-11-12"]
    (entry,) = snapshot.items("journal")
    assert entry["body"] == "slept badly, skipped the gym"


def test_memory_holds_profile_facts_and_private_notes(tmp_path):
    data = str(tmp_path)
    vault = os.path.dirname(pm_db_path(USER, data))
    append_profile_fact(vault, data, USER, "Leo is allergic to peanuts")
    private = load_private(USER, data)
    private.notes_private.append("my therapist is Dr. Alvarez")
    save_private(private, USER, data)

    memory = {item["text"]: item["kind"] for item in _read(tmp_path).items("memory")}

    assert memory == {"Leo is allergic to peanuts": "fact", "my therapist is Dr. Alvarez": "private_note"}


def test_only_pending_approvals_are_listed(tmp_path):
    data = str(tmp_path)
    waiting = create_approval_request(USER, data, action_type="schedule_update",
                                      payload={"updates": [{"id": "e1", "start": "16:00"}]},
                                      summary="Update schedule event: dentist", risk_level="medium")
    declined = create_approval_request(USER, data, action_type="todo_remove", payload={"id": "t1"},
                                       summary="Remove todo", risk_level="medium")
    reject_approval_request(data, declined.id, session_id=USER)

    approvals = _read(tmp_path).approvals

    assert list(approvals) == [waiting.id]
    assert approvals[waiting.id]["action"] == "schedule_update"
    assert approvals[waiting.id]["payload"]["updates"][0]["start"] == "16:00"


def test_an_open_question_has_a_topic_that_survives_being_asked_again(tmp_path):
    data = str(tmp_path)
    plan = PMPlanExtraction(tasks=[PMTaskExtraction(
        task_id="task-1", intent=PMIntent.CREATE_SCHEDULE_EVENT,
        entities={"title": "dentist appointment", "date": "2026-11-14"}, missing_fields=["start"])])
    choices = [{"id": "1", "label": "Saturday at 10 AM", "values": {"start": "10:00"}}]

    def ask():
        clarification._save_pending_field_choices(THREAD, data, plan, user_id=USER,
                                                  blocking_task_id="task-1", missing=["start"], choices=choices)
        return _read(tmp_path).question

    first, again = ask(), ask()

    assert first is not None and "dentist appointment" in first.topic
    assert again.topic == first.topic and again.stamp != first.stamp
