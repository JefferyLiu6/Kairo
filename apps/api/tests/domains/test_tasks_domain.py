"""The tasks domain: what each tool proposes, checks and reads."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from kairo.domains.catalog import DOMAINS
from kairo.domains.registry import Answer, ToolContext
from kairo.storage.store import TodoData, TodoItem, save_todos

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=ZoneInfo("America/Toronto"))
USER = "tasks-user"


def ctx(tmp_path, message="") -> ToolContext:
    return ToolContext(user_id=USER, thread_id="pm-tasks", data_dir=str(tmp_path), now=NOW, message=message)


def call(name: str, tmp_path, message="", **args):
    tool = DOMAINS.tool(name)
    parsed = tool.args.model_validate(args)
    context = ctx(tmp_path, message)
    return tool, parsed, context


def seed(tmp_path, *titles, done=()):
    save_todos(TodoData(items=[TodoItem(id=f"t{i}", title=title, done=title in done)
                               for i, title in enumerate(titles, 1)]), USER, str(tmp_path))


def item(title, due_date=None, due_time=None):
    return {"title": title, "due_date": due_date, "due_time": due_time}


# ── add_todos ─────────────────────────────────────────────────────────────────

def test_a_list_becomes_one_command_per_task(tmp_path):
    tool, args, context = call("add_todos", tmp_path, items=[item("email advisor"), item("buy cat food")])
    actions = tool.propose(args, context)
    assert [(a.action_type, a.payload["title"], a.payload.get("due")) for a in actions] == [
        ("todo_add", "email advisor", None), ("todo_add", "buy cat food", None)]
    assert actions[0].summary == "Add todo: email advisor"


def test_a_due_date_and_time_are_carried_into_the_command(tmp_path):
    tool, args, context = call("add_todos", tmp_path, items=[
        item("fix figure 3", "2026-11-13"), item("Acme logo v2", "2026-11-16", "17:00")])
    assert [a.payload["due"] for a in tool.propose(args, context)] == ["2026-11-13", "2026-11-16T17:00:00"]


def test_a_time_with_no_date_means_today(tmp_path):
    tool, args, context = call("add_todos", tmp_path, items=[item("leave for the dentist", None, "16:00")])
    assert tool.propose(args, context)[0].payload["due"] == "2026-11-12T16:00:00"


@pytest.mark.parametrize("bad", [
    {"items": []},
    {"items": [item("x", "friday")]},
    {"items": [item("x", "2026-11-13", "5pm")]},
    {"items": [{"title": "x"}]},                    # every field must be present, even if null
])
def test_malformed_arguments_do_not_parse(bad):
    with pytest.raises(ValidationError):
        DOMAINS.tool("add_todos").args.model_validate(bad)


@pytest.mark.parametrize("message, due, clue", [
    ("add todo fix figure 3 due friday", "2026-11-20", "2026-11-13"),      # next week's Friday
    ("remind me to call mom tomorrow", None, "no date"),                   # the date word was dropped
    ("renew passport", "2026-01-14", "past"),
])
def test_a_due_date_that_contradicts_the_message_is_sent_back(tmp_path, message, due, clue):
    tool, args, context = call("add_todos", tmp_path, message, items=[item("x", due)])
    assert clue in tool.check(args, context)


@pytest.mark.parametrize("message, due_time, clue", [
    ("remind me at 4pm to leave for the dentist", None, "16:00"),          # the time was dropped
    ("add a deadline: Acme logo v2 due monday 5pm", "05:00", "17:00"),     # the wrong half of the day
    ("submit the form by 11:30 am tomorrow", "23:30", "11:30"),
])
def test_a_clock_time_in_the_message_must_be_carried_into_the_task(tmp_path, message, due_time, clue):
    tool, args, context = call("add_todos", tmp_path, message, items=[item("x", None, due_time)])
    problem = tool.check(args, context)
    assert problem and "time" in problem and clue in problem


@pytest.mark.parametrize("message, due_date, due_time", [
    ("remind me at 4pm to leave for the dentist", None, "16:00"),
    ("add a deadline: Acme logo v2 due monday 5pm", "2026-11-16", "17:00"),
    ("pack swim bag friday night", "2026-11-13", "20:00"),                  # no clock time given: a pick is allowed
    ("call the dentist at noon", None, "12:00"),
])
def test_a_time_that_agrees_with_the_message_passes(tmp_path, message, due_date, due_time):
    tool, args, context = call("add_todos", tmp_path, message, items=[item("x", due_date, due_time)])
    assert tool.check(args, context) is None


@pytest.mark.parametrize("message", [
    "Please remind me to take my blood pressure pill every morning at 8",
    "water the plants each sunday",
    "stretch daily",
])
def test_something_that_repeats_is_not_accepted_as_a_task(tmp_path, message):
    tool, args, context = call("add_todos", tmp_path, message, items=[item("x")])
    assert "repeat" in tool.check(args, context) and "hand" in tool.check(args, context)


@pytest.mark.parametrize("message, items", [
    ("add todo fix figure 3 due friday", [item("fix figure 3", "2026-11-13")]),
    ("add todo read every chapter of the handbook", [item("read every chapter of the handbook")]),
    ("buy milk", [item("buy milk")]),
    ("brain dump: email advisor, call mom tomorrow", [item("email advisor"), item("call mom", "2026-11-13")]),
])
def test_a_due_date_that_agrees_with_the_message_passes(tmp_path, message, items):
    tool, args, context = call("add_todos", tmp_path, message, items=items)
    assert tool.check(args, context) is None


# ── complete_todo and remove_todo ─────────────────────────────────────────────

def test_completing_a_task_names_the_stored_task_by_id(tmp_path):
    seed(tmp_path, "email advisor about committee date", "buy cat food")
    tool, args, context = call("complete_todo", tmp_path, query="cat food")
    (action,) = tool.propose(args, context)
    assert action.action_type == "todo_complete" and action.payload["id"] == "t2"


def test_removing_a_task_names_it_in_the_summary_the_user_approves(tmp_path):
    seed(tmp_path, "rotate API keys")
    tool, args, context = call("remove_todo", tmp_path, query="rotate")
    (action,) = tool.propose(args, context)
    assert action.action_type == "todo_remove" and action.payload["id"] == "t1"
    assert action.summary == "Remove todo: rotate API keys"


def test_a_task_that_is_not_there_is_answered_not_guessed(tmp_path):
    seed(tmp_path, "buy cat food")
    tool, args, context = call("complete_todo", tmp_path, query="expense report")
    answer = tool.propose(args, context)
    assert isinstance(answer, Answer) and "expense report" in answer.text and answer.asks is False
    assert answer.elsewhere      # the task list only knows tasks; it may be on the calendar or a habit


def test_several_matching_tasks_become_a_question(tmp_path):
    seed(tmp_path, "email advisor", "email advisor about committee date")
    tool, args, context = call("complete_todo", tmp_path, query="email advisor")
    answer = tool.propose(args, context)
    assert isinstance(answer, Answer) and answer.asks and "committee date" in answer.text
    assert not answer.elsewhere  # it is on the task list; the only doubt is which one


# ── list_todos ────────────────────────────────────────────────────────────────

def test_the_list_names_every_open_task_with_its_due_date(tmp_path):
    save_todos(TodoData(items=[
        TodoItem(id="t1", title="email advisor"),
        TodoItem(id="t2", title="fix figure 3", due="2026-11-13"),
        TodoItem(id="t3", title="renew library books", done=True),
    ]), USER, str(tmp_path))
    tool, args, context = call("list_todos", tmp_path)

    text = tool.read(args, context)

    assert text.splitlines() == ["Your open tasks:", "- email advisor", "- fix figure 3 — due Fri 13 Nov",
                                 "Done: 1."]


def test_an_empty_list_says_so(tmp_path):
    tool, args, context = call("list_todos", tmp_path)
    assert tool.read(args, context) == "You have no open tasks."
