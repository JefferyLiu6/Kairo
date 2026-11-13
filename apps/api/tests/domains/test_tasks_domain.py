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


def test_the_coordinator_is_shown_the_open_tasks_so_it_knows_what_is_on_the_list(tmp_path):
    tasks = DOMAINS.domain("tasks")
    assert tasks.context(ctx(tmp_path)) == "The to-do list is empty."
    seed(tmp_path, "email advisor", "buy cat food", "renew passport", done=("renew passport",))
    assert tasks.context(ctx(tmp_path)).splitlines() == ["Open tasks on the to-do list (the id of each task is in brackets):", "- [t1] email advisor", "- [t2] buy cat food"]


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


def test_a_task_that_is_already_on_the_list_with_the_same_due_date_is_not_added_again(tmp_path):
    save_todos(TodoData(items=[TodoItem(id="t1", title="send investor update", due="2026-11-13"),
                               TodoItem(id="t2", title="fix figure 3")]), USER, str(tmp_path))
    # A model sometimes repeats the previous message's task alongside the new one.
    tool, args, context = call("add_todos", tmp_path, "Add todo: review Q4 hiring plan", items=[
        item("Send investor update", "2026-11-13"), item("review Q4 hiring plan")])
    assert [a.payload["title"] for a in tool.propose(args, context)] == ["review Q4 hiring plan"]
    # The same title with a new due date is a new task.
    tool, args, context = call("add_todos", tmp_path, "add todo fix figure 3 due friday", items=[item("fix figure 3", "2026-11-13")])
    assert [a.payload["due"] for a in tool.propose(args, context)] == ["2026-11-13"]
    # Nothing new at all is said, not stored.
    tool, args, context = call("add_todos", tmp_path, "add todo fix figure 3", items=[item("fix figure 3")])
    answer = tool.propose(args, context)
    assert isinstance(answer, Answer) and answer.text == "That's already on your list: fix figure 3."


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
    tool, args, context = call("complete_todo", tmp_path, task_id="t2")
    assert tool.check(args, context) is None
    (action,) = tool.propose(args, context)
    assert action.action_type == "todo_complete" and action.payload["id"] == "t2"
    assert action.summary == "Mark todo complete: buy cat food"


def test_removing_tasks_names_each_in_the_summary_the_user_approves(tmp_path):
    seed(tmp_path, "rotate API keys", "book dentist")
    tool, args, context = call("remove_todos", tmp_path, task_ids=["t1", "t2", "t1"])
    actions = tool.propose(args, context)
    assert [(a.action_type, a.payload["id"], a.summary) for a in actions] == [
        ("todo_remove", "t1", "Remove todo: rotate API keys"), ("todo_remove", "t2", "Remove todo: book dentist")]
    assert all(a.requires_approval for a in actions)


def test_an_id_that_is_not_an_open_task_is_sent_back_and_may_belong_elsewhere(tmp_path):
    seed(tmp_path, "buy cat food", "old thing", done=("old thing",))
    for name, args in (("complete_todo", {"task_id": "nope"}), ("complete_todo", {"task_id": "t2"}),
                       ("remove_todos", {"task_ids": ["t1", "nope"]})):
        tool, parsed, context = call(name, tmp_path, **args)
        problem = tool.check(parsed, context)
        assert "no open task has id" in problem and "a habit, a calendar event" in problem


def test_the_tasks_shown_to_the_model_carry_their_ids_and_due_dates(tmp_path):
    save_todos(TodoData(items=[TodoItem(id="t1", title="rotate API keys", due="2026-11-20"),
                               TodoItem(id="t2", title="book dentist"), TodoItem(id="t3", title="done", done=True)]),
               USER, str(tmp_path))
    shown = DOMAINS.domain("tasks").context(ctx(tmp_path, ""))
    assert shown.splitlines()[1:] == ["- [t1] rotate API keys — due Fri 20 Nov", "- [t2] book dentist"]


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


@pytest.mark.parametrize("message, task_id, problem", [
    ("done email advisor", "t1", None),                                   # named, and one of the two it names
    ("done email advisor", "t3", "does not name 'buy cat food'"),         # the words point elsewhere
    ("done with it", "t3", None),                                         # only pointing: the conversation decides
    ("delete the second one", "t2", None),
    ("the funding one", "t3", "nothing here mentions “funding”"),         # nothing about funding is open
    ("done the dentist thing", "t1", "nothing here mentions “dentist”"),
])
def test_a_task_picked_by_id_is_held_to_the_users_words(tmp_path, message, task_id, problem):
    seed(tmp_path, "email advisor", "email advisor about committee date", "buy cat food")
    tool, args, context = call("complete_todo", tmp_path, message, task_id=task_id)
    found = tool.check(args, context)
    assert found is None if problem is None else problem in found


def test_deleting_everything_is_not_held_to_any_title(tmp_path):
    seed(tmp_path, "email advisor", "buy cat food")
    tool, args, context = call("remove_todos", tmp_path, "Delete all my todos", task_ids=["t1", "t2"])
    assert tool.check(args, context) is None
