"""The habits, journal and memory domains: what each tool checks, proposes and reads."""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import time_machine

from kairo.domains.catalog import DOMAINS
from kairo.domains.registry import Answer, ToolContext
from kairo.storage.control_store import pm_db_path
from kairo.storage.habits import habit_add, habit_checkin, habit_rows
from kairo.storage.journal import journal_append
from kairo.storage.profile import append_profile_fact
from kairo.storage.store import TodoData, TodoItem, load_private, save_private, save_todos

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=ZoneInfo("America/Toronto"))   # a Thursday
USER = "life-user"


@pytest.fixture(autouse=True)
def thursday():
    with time_machine.travel(NOW, tick=True):
        yield


def ctx(tmp_path, message="") -> ToolContext:
    vault = os.path.dirname(pm_db_path(USER, str(tmp_path)))
    return ToolContext(user_id=USER, thread_id="pm-life", data_dir=str(tmp_path), now=NOW, message=message, vault_dir=vault)


def call(tool_name: str, tmp_path, message="", **args):
    tool = DOMAINS.tool(tool_name)
    return tool, tool.args.model_validate(args), ctx(tmp_path, message)


def db(tmp_path) -> str:
    path = pm_db_path(USER, str(tmp_path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def habit(tmp_path, name) -> str:
    habit_add(name, db(tmp_path))
    return next(row["id"] for row in habit_rows(db(tmp_path)) if row["name"] == name)


# ── Habits ────────────────────────────────────────────────────────────────────

def test_a_new_habit_becomes_a_command_and_one_already_tracked_does_not(tmp_path):
    tool, args, context = call("add_habit", tmp_path, name="morning run")
    (action,) = tool.propose(args, context)
    assert (action.action_type, action.payload["name"], action.summary) == ("habit_add", "morning run", "Track habit: morning run")

    habit(tmp_path, "morning run")
    tool, args, context = call("add_habit", tmp_path, name="Morning Run")
    answer = tool.propose(args, context)
    assert isinstance(answer, Answer) and "already tracking" in answer.text and not answer.asks


@pytest.mark.parametrize("message, belongs_to_memory", [
    ("remember that i sleep until noon on sundays", True),
    ("keep in mind that I walk the dog before work", True),
    ("for future reference, I stretch after every run", True),
    ("Add a habit called morning run", False),
    ("remember to track a habit: floss", False),            # they did ask for a habit
    ("I want to build a reading habit", False),
])
def test_something_the_user_asked_to_be_remembered_is_not_turned_into_a_habit(tmp_path, message, belongs_to_memory):
    tool, args, context = call("add_habit", tmp_path, message, name="x")
    verdict = tool.check(args, context)
    assert (verdict is not None and verdict.to == "memory") if belongs_to_memory else verdict is None


def test_logging_a_habit_is_dated_today_unless_the_user_said_when(tmp_path):
    run = habit(tmp_path, "morning run")
    tool, args, context = call("log_habit", tmp_path, "I did my morning run", habit_id=run, date=None)
    assert tool.check(args, context) is None
    (action,) = tool.propose(args, context)
    assert action.action_type == "habit_checkin" and action.payload == {
        "operation": "checkin", "id": run, "name": "morning run", "checkin_date": "2026-11-12"}

    tool, args, context = call("log_habit", tmp_path, "I ran yesterday", habit_id=run, date="2026-11-11")
    assert tool.check(args, context) is None and tool.propose(args, context)[0].payload["checkin_date"] == "2026-11-11"


@pytest.mark.parametrize("message, args, clue", [
    ("I did my morning run", dict(habit_id="nope0000", date=None), "nope0000"),
    ("I ran yesterday", dict(date="2026-11-10"), "2026-11-11"),          # the wrong day
    ("I'll run tomorrow", dict(date="2026-11-13"), "has not happened"),
])
def test_a_log_that_cannot_be_right_is_refused_with_the_reason(tmp_path, message, args, clue):
    run = habit(tmp_path, "morning run")
    tool, parsed, context = call("log_habit", tmp_path, message, **{"habit_id": run, **args})
    assert clue in tool.check(parsed, context)


def test_logging_a_habit_twice_in_a_day_says_so_instead_of_logging_again(tmp_path):
    run = habit(tmp_path, "morning run")
    habit_checkin(run, db(tmp_path), "2026-11-12")
    tool, args, context = call("log_habit", tmp_path, "Log morning run for today", habit_id=run, date=None)
    answer = tool.propose(args, context)
    assert isinstance(answer, Answer) and answer.text == "morning run is already logged for Thu 12 Nov. Streak: 1 day."


def test_removing_a_habit_waits_for_approval(tmp_path):
    run = habit(tmp_path, "morning run")
    tool, args, context = call("remove_habit", tmp_path, habit_id=run)
    (action,) = tool.propose(args, context)
    assert action.action_type == "habit_remove" and action.payload["id"] == run and action.requires_approval
    assert action.summary == "Stop tracking habit: morning run"


def test_habits_are_shown_with_their_streaks_and_listed_for_the_coordinator_with_ids(tmp_path):
    habits = DOMAINS.domain("habits")
    tool, args, context = call("show_habits", tmp_path)
    assert tool.read(args, context) == "You are not tracking any habits yet."
    assert habits.context(context) == "No habits are tracked yet."

    run, stretch = habit(tmp_path, "morning run"), habit(tmp_path, "stretch")
    habit_checkin(run, db(tmp_path), "2026-11-11")
    habit_checkin(run, db(tmp_path), "2026-11-12")
    assert tool.read(args, context).splitlines() == [
        "Your habits:", "- morning run — 2-day streak, last logged Thu 12 Nov", "- stretch — not logged yet"]
    assert habits.context(context).splitlines() == [
        "Habits being tracked (id in brackets):", f"- [{run}] morning run (logged today)", f"- [{stretch}] stretch"]


# ── Journal ───────────────────────────────────────────────────────────────────

def test_a_journal_entry_is_the_users_own_words(tmp_path):
    said = "journal: rough day, but I finally shipped the eval and I feel lighter"
    tool, args, context = call("write_journal", tmp_path, said, body="rough day, but I finally shipped the eval and I feel lighter")
    assert tool.check(args, context) is None
    (action,) = tool.propose(args, context)
    assert action.action_type == "journal_append" and action.payload["body"].startswith("rough day")

    tool, args, context = call("write_journal", tmp_path, said, body="The user had a difficult day but completed their evaluation work.")
    assert "own words" in tool.check(args, context)


def test_the_journal_is_shown_newest_first_and_can_be_searched(tmp_path):
    tool, args, context = call("show_journal", tmp_path, query=None)
    assert tool.read(args, context) == "Your journal is empty."
    journal_append("shipped the eval", db(tmp_path))
    journal_append("long walk by the lake", db(tmp_path))

    assert tool.read(args, context).splitlines() == ["Your latest journal entries:", "- Thu 12 Nov: long walk by the lake",
                                                    "- Thu 12 Nov: shipped the eval"]
    tool, args, context = call("show_journal", tmp_path, query="lake")
    assert tool.read(args, context).splitlines() == ["Journal entries matching “lake”:", "- Thu 12 Nov: long walk by the lake"]
    tool, args, context = call("show_journal", tmp_path, query="taxes")
    assert tool.read(args, context) == "No journal entries match “taxes”."


# ── Memory ────────────────────────────────────────────────────────────────────

def test_a_fact_is_remembered_and_a_sensitive_one_waits_for_approval_as_a_private_note(tmp_path):
    tool, args, context = call("remember", tmp_path, "remember that i prefer meetings after 10am", fact="I prefer meetings after 10am")
    (action,) = tool.propose(args, context)
    assert (action.action_type, action.payload["fact"], action.requires_approval) == ("remember", "I prefer meetings after 10am", False)
    # A full stop the model added is not part of the fact.
    tool, args, context = call("remember", tmp_path, "remember that I read before bed", fact="I read before bed.")
    assert tool.propose(args, context)[0].payload["fact"] == "I read before bed"

    tool, args, context = call("remember", tmp_path, "Remember that my passport number is X1234567",
                               fact="my passport number is X1234567")
    (action,) = tool.propose(args, context)
    assert action.action_type == "private_note_append" and action.payload["note"] == "my passport number is X1234567"
    assert action.requires_approval and action.risk_level == "high"
    assert "X1234567" not in action.summary          # the approval prompt does not repeat the secret


def test_a_fact_already_remembered_is_not_saved_twice(tmp_path):
    context = ctx(tmp_path)
    append_profile_fact(context.vault_dir, context.data_dir, USER, "I prefer meetings after 10am")
    tool, args, context = call("remember", tmp_path, fact="i prefer meetings after 10am")
    answer = tool.propose(args, context)
    assert isinstance(answer, Answer) and "already" in answer.text


def test_what_is_remembered_is_shown_without_revealing_private_notes(tmp_path):
    memory = DOMAINS.domain("memory")
    tool, args, context = call("show_memory", tmp_path)
    assert tool.read(args, context) == "I haven't been asked to remember anything about you yet."

    append_profile_fact(context.vault_dir, context.data_dir, USER, "I prefer meetings after 10am")
    meta = load_private(USER, str(tmp_path))
    meta.notes_private.append("my passport number is X1234567")
    save_private(meta, USER, str(tmp_path))

    shown = tool.read(args, context)
    assert shown.splitlines() == ["What I remember about you:", "- I prefer meetings after 10am",
                                  "Plus 1 private note, which I keep but do not repeat here."]
    assert "X1234567" not in shown and "X1234567" not in memory.context(context)
    assert "- I prefer meetings after 10am" in memory.context(context)


def test_exporting_private_data_always_waits_for_approval(tmp_path):
    tool, args, context = call("export_data", tmp_path)
    (action,) = tool.propose(args, context)
    assert action.action_type == "private_export" and action.requires_approval and action.risk_level == "high"


def test_everything_that_is_stored_can_be_summarised(tmp_path):
    save_todos(TodoData(items=[TodoItem(id="t1", title="rotate API keys")]), USER, str(tmp_path))
    habit(tmp_path, "morning run")
    journal_append("shipped the eval", db(tmp_path))
    tool, args, context = call("show_stored_data", tmp_path)
    assert tool.read(args, context).splitlines() == [
        "Here is everything I store for you:",
        "- Tasks: 1",
        "- Calendar events: 0",
        "- Habits: 1",
        "- Journal entries: 1",
        "- Things I remember about you: 0",
        "- Private notes: 0",
        "Changes that need approval, and what was done, are kept in an audit log. Say “export my data” for a copy "
        "of the private notes.",
    ]


def test_the_catalog_runs_habits_and_journal_inline_and_memory_as_an_agent():
    assert [d.name for d in DOMAINS.inline_domains()] == ["tasks", "habits", "journal"]
    assert [d.name for d in DOMAINS.agent_domains()] == ["schedule", "memory"]
    assert all(d.guidance for d in DOMAINS.inline_domains())
