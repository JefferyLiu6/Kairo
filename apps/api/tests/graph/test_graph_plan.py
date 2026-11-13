"""A message that asks for several separate things: planned once, done one part at a time, answered once."""
from __future__ import annotations

from kairo.agents.graph import nodes
from kairo.storage.control_store import list_approval_requests, pm_db_path
from kairo.storage.decision_log import list_turn_decisions
from kairo.storage.habits import habit_rows
from kairo.storage.store import ScheduleData, ScheduleEntry, load_schedule, save_schedule

from tests.graph.conftest import THREAD, USER, call, item, seed, todos


def plan(*parts):
    return call("do_several", steps=[{"domain": domain, "request": request} for domain, request in parts])


def standup(chat):
    save_schedule(ScheduleData(entries=[
        ScheduleEntry(id="stand001", title="standup", date="2026-11-13", start="09:00", end="09:15"),
        ScheduleEntry(id="dent0001", title="dentist", date="2026-11-13", start="15:00", end="16:00"),
    ]), USER, chat.data_dir)


def events(chat):
    return [(e.title, e.start) for e in load_schedule(USER, chat.data_dir).entries]


def pending(chat):
    return list_approval_requests(USER, chat.data_dir, status="pending", limit=10)


def logged(chat):
    return [row["routing"]["reason"] for row in list_turn_decisions(THREAD, chat.data_dir, user_id=USER)
            if row["workingMemory"]["source"] == "orchestrator"][0]


def move_standup(start="10:00"):
    return call("update_event", event_id="stand001", new_title=None, new_date=None, new_start=start, new_end=None)


# ── Two things in one message ─────────────────────────────────────────────────

def test_two_separate_things_are_planned_done_in_turn_and_answered_once(chat):
    chat.model.extend([plan(("tasks", "add a task to email Sam"), ("habits", "start tracking stretching")),
                       call("add_todos", items=[item("email Sam")]), call("add_habit", name="stretching")])

    reply = chat("add a task to email Sam and start tracking stretching")

    assert reply.splitlines() == ["Added task: email Sam — no due date.", "Now tracking: stretching."]
    assert todos(chat) == [("email Sam", None, False)]
    assert [h["name"] for h in habit_rows(pm_db_path(USER, chat.data_dir))] == ["stretching"]
    assert "tool=plan(tasks, habits)" in logged(chat) and "model_calls=3" in logged(chat)


def test_each_part_is_shown_the_whole_message_and_told_which_part_is_its_own(chat):
    chat.model.extend([plan(("tasks", "add a task to email Sam"), ("habits", "start tracking stretching")),
                       call("add_todos", items=[item("email Sam")]), call("add_habit", name="stretching")])
    message = "add a task to email Sam and start tracking stretching"

    chat(message)

    first, second = chat.asked[1], chat.asked[2]
    assert first["turns"][-2:] == [("user", message), ("user", nodes._FOCUS.format(part="add a task to email Sam"))]
    assert second["turns"][-1] == ("user", nodes._FOCUS.format(part="start tracking stretching"))
    assert "do_several" in chat.asked[0]["tools"] and "do_several" not in first["tools"]       # a part is not planned again


def test_a_part_for_the_calendar_goes_straight_to_its_agent(chat):
    standup(chat)
    chat.model.extend([plan(("tasks", "add a task to email Sam"), ("schedule", "what's on friday")),
                       call("add_todos", items=[item("email Sam")]),
                       call("show_events", from_date="2026-11-13", to_date="2026-11-13")])

    reply = chat("add a task to email Sam and tell me what's on friday")

    assert reply.splitlines()[0] == "Added task: email Sam — no due date." and "- 9 AM–9:15 AM standup" in reply
    assert [a["prompt"] for a in chat.asked] == ["coordinator", "coordinator", "schedule_agent"]


# ── Approval ──────────────────────────────────────────────────────────────────

def test_what_needs_approval_in_a_plan_is_asked_for_once_after_the_rest_is_done(chat):
    standup(chat)
    seed(chat, "rotate API keys")
    chat.model.extend([plan(("schedule", "move the standup to 10am"), ("tasks", "delete the rotate task"),
                            ("tasks", "add a task to email Sam")),
                       move_standup(), call("remove_todos", task_ids=["t1"]), call("add_todos", items=[item("email Sam")])])

    reply = chat("move the standup to 10am, delete the rotate task, and add a task to email Sam")

    (batch,) = pending(chat)
    assert batch.action_type == "batch" and [a["action_type"] for a in batch.payload["actions"]] == [
        "schedule_update", "todo_remove"]
    lines = reply.splitlines()
    assert lines[0] == "Added task: email Sam — no due date."                 # what needed no approval is done
    assert lines[1:4] == [f"Approval required [{batch.id}]: 2 changes:",
                          "- Update schedule event: standup — 10 AM–10:15 AM (was 9 AM–9:15 AM)",
                          "- Remove todo: rotate API keys"]
    assert ("standup", "09:00") in events(chat) and len(todos(chat)) == 2     # nothing risky has run

    chat("approve")
    assert ("standup", "10:00") in events(chat) and todos(chat) == [("email Sam", None, False)]


def test_one_risky_part_among_safe_ones_is_an_ordinary_approval(chat):
    standup(chat)
    chat.model.extend([plan(("schedule", "cancel the dentist"), ("tasks", "add a task to rebook it")),
                       call("remove_events", event_ids=["dent0001"]), call("add_todos", items=[item("rebook the dentist")])])

    reply = chat("cancel the dentist and add a task to rebook it")

    (one,) = pending(chat)
    assert one.action_type == "schedule_remove" and "Remove schedule event: dentist" in reply
    assert reply.splitlines()[0] == "Added task: rebook the dentist — no due date."


# ── When a part goes wrong ────────────────────────────────────────────────────

def test_a_part_that_is_sent_back_is_retried_without_disturbing_the_others(chat):
    standup(chat)
    chat.model.extend([plan(("tasks", "add a task to email Sam by friday"), ("habits", "start tracking stretching")),
                       call("add_todos", items=[item("email Sam", "2026-11-20")]),      # the wrong Friday
                       call("add_todos", items=[item("email Sam", "2026-11-13")]),
                       call("add_habit", name="stretching")])

    reply = chat("add a task to email Sam by friday and start tracking stretching")

    assert reply.splitlines() == ["Added task: email Sam — due Fri 13 Nov.", "Now tracking: stretching."]
    assert "model_calls=4" in logged(chat)


def test_a_part_that_needs_a_question_does_not_stop_the_other_parts(chat):
    chat.model.extend([plan(("tasks", "delete that thing"), ("habits", "start tracking stretching")),
                       call("ask_user", question="Which task do you mean?"), call("add_habit", name="stretching")])

    reply = chat("delete that thing and start tracking stretching")

    assert reply.splitlines() == ["Now tracking: stretching.", "Which task do you mean?"]
    assert chat.question().summary == "Which task do you mean?"


def test_only_one_question_is_left_open_and_the_other_part_is_named_as_not_done(chat):
    chat.model.extend([plan(("tasks", "delete that thing"), ("habits", "log the usual")),
                       call("ask_user", question="Which task do you mean?"),
                       call("ask_user", question="Which habit do you mean?")])

    reply = chat("delete that thing and log the usual")

    assert reply.splitlines() == ["Which task do you mean?",
                                  "Not done yet: “log the usual”. Ask me for it again once the question above is settled."]
    assert chat.question().summary == "Which task do you mean?"


# ── What is not a plan ────────────────────────────────────────────────────────

def test_a_message_for_one_area_is_not_split(chat):
    standup(chat)
    one_area = plan(("schedule", "move the standup"), ("schedule", "to 10am"))
    chat.model.extend([one_area, call("hand_off", domain="schedule"), move_standup()])

    assert "Update schedule event: standup — 10 AM–10:15 AM" in chat("move the standup to 10am")
    assert "this message is for one area, so it is not split" in chat.asked[1]["turns"][-1][1]

    chat.model.extend([one_area, one_area, move_standup("11:00")])        # it insists: the area takes the whole message
    assert "11 AM–11:15 AM" in chat("move the standup to 11am") and chat.asked[-1]["prompt"] == "schedule_agent"


def test_a_step_that_is_not_the_users_own_words_is_not_acted_on(chat):
    """"Plan my day" asks for advice. A split that makes up a task for it stores nothing."""
    made_up = plan(("tasks", "review the hiring plan"), ("schedule", "plan my day tomorrow"))
    chat.model.extend([made_up, call("hand_off", domain="assistant"),
                       call("answer", text="Tomorrow is clear. Start with your open tasks.")])

    assert chat("Plan my day tomorrow") == "Tomorrow is clear. Start with your open tasks."
    assert "is not the user's own words (the message does not say “review”)" in chat.asked[1]["turns"][-1][1]
    assert todos(chat) == []


def test_a_split_is_cut_off_at_four_parts_and_steps_for_unknown_areas_are_dropped(chat):
    message = "track habit one, add todo two, track habit three, add todo four, track habit five"
    chat.model.extend([plan(("nowhere", "track habit one"), ("habits", "track habit one"), ("tasks", "add todo two"),
                            ("habits", "track habit three"), ("tasks", "add todo four"), ("habits", "track habit five")),
                       call("add_habit", name="one"), call("add_todos", items=[item("two")]),
                       call("add_habit", name="three"), call("add_todos", items=[item("four")])])

    chat(message)

    assert [h["name"] for h in habit_rows(pm_db_path(USER, chat.data_dir))] == ["one", "three"]
    assert [title for title, *_ in todos(chat)] == ["two", "four"]


def test_a_part_is_checked_against_its_own_words_unless_the_model_added_a_time_to_them(chat):
    standup(chat)
    # "by friday" belongs to the task, "10am" to the standup: neither is held against the other.
    chat.model.extend([plan(("schedule", "move the standup to 10am"), ("tasks", "add a task to email Sam by friday")),
                       move_standup(), call("add_todos", items=[item("email Sam", "2026-11-13")])])
    reply = chat("move the standup to 10am and add a task to email Sam by friday")
    assert reply.splitlines()[0] == "Added task: email Sam — due Fri 13 Nov." and len(chat.asked) == 3

    # A part that says "at 5pm" when the message never did is not trusted: the task is checked
    # against the whole message, which gives no time for it.
    state = {"message": "add a task to email Sam and start tracking stretching", "focus": "email Sam at 5pm"}
    assert nodes._wording(state) == state["message"]
    assert nodes._wording({**state, "focus": "add a task to email Sam"}) == "add a task to email Sam"
