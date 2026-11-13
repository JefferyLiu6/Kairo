"""The sweep: a request in the message that the turn left untouched is found and done, or left alone if in doubt."""
from __future__ import annotations

from kairo.agents.graph import nodes
from kairo.storage.control_store import list_approval_requests
from kairo.storage.store import ScheduleData, ScheduleEntry, load_schedule, save_schedule

from tests.graph.conftest import USER, call, item, seed, todos

TO_SCHEDULE = call("hand_off", domain="schedule")
MESSAGE = "push the team lunch to 12:30 and add a todo to tell everyone"


def lunch(chat):
    save_schedule(ScheduleData(entries=[
        ScheduleEntry(id="lunch001", title="team lunch", date="2026-11-13", start="12:00", end="13:00"),
    ]), USER, chat.data_dir)


def move_lunch():
    return call("update_event", event_id="lunch001", new_title=None, new_date=None, new_start="12:30", new_end=None)


def pending(chat):
    return list_approval_requests(USER, chat.data_dir, status="pending", limit=10)


def test_a_request_the_turn_left_out_is_found_and_done(chat):
    lunch(chat)
    seen = []
    chat.leftover = lambda shown: seen.append(shown) or "add a todo to tell everyone"
    chat.model.extend([TO_SCHEDULE, move_lunch(), call("add_todos", items=[item("tell everyone")])])

    reply = chat(MESSAGE)

    assert reply.splitlines()[0] == "Added task: tell everyone — no due date."
    assert "Update schedule event: team lunch — 12:30 PM–1:30 PM" in reply and len(pending(chat)) == 1
    assert seen[0]["message"] == MESSAGE and seen[0]["done"].startswith("Approval required [")
    # The part was done like a part of a plan: placed by the coordinator and checked against its own words.
    assert chat.asked[2]["turns"][-1] == ("user", nodes._FOCUS.format(part="add a todo to tell everyone"))


def test_what_the_sweep_names_is_only_believed_if_it_is_the_users_words_and_was_not_touched(chat):
    lunch(chat)
    for said in ("none", "Everything was done.", "add a task to email the whole team",      # not in the message
                 "push the team lunch to 12:30",                                           # shares words with what was done
                 MESSAGE,                                                                  # the whole message
                 "everyone"):                                                              # one word
        chat.leftover = lambda shown, said=said: said
        chat.model.extend([TO_SCHEDULE, move_lunch()])
        chat(MESSAGE)
        chat("reject")
    assert todos(chat) == [] and len(chat.asked) == 12                                     # two model calls a turn, no more


def test_only_a_message_that_joins_things_is_swept_and_only_once(chat):
    asked = []
    chat.leftover = lambda shown: asked.append(shown["message"]) or "none"
    chat.model.extend([call("add_todos", items=[item("buy milk")]),
                       call("add_todos", items=[item("buy milk"), item("call the bank")])])

    chat("add todo buy milk")
    chat("add todos: buy milk, call the bank")

    assert asked == ["add todos: buy milk, call the bank"]


def test_a_turn_that_asked_a_question_or_was_split_is_not_swept(chat):
    asked = []
    chat.leftover = lambda shown: asked.append(shown) or "none"
    chat.model.extend([call("ask_user", question="Which list, and which day?"),
                       call("do_several", steps=[{"domain": "tasks", "request": "add todo buy milk"},
                                                 {"domain": "habits", "request": "track stretching"}]),
                       call("add_todos", items=[item("buy milk")]), call("add_habit", name="stretching")])

    chat("clear it and then redo it")
    chat("add todo buy milk and track stretching")

    assert asked == []


def test_a_left_out_request_that_needs_approval_waits_when_one_was_already_asked_for(chat):
    lunch(chat)
    seed(chat, "rotate API keys")
    chat.leftover = lambda shown: "delete the rotate todo"
    chat.model.extend([TO_SCHEDULE, move_lunch(), call("remove_todos", task_ids=["t1"])])

    reply = chat("push the team lunch to 12:30 and delete the rotate todo")

    (one,) = pending(chat)                                   # one approval in a reply, never two
    assert one.action_type == "schedule_update" and len(todos(chat)) == 1
    assert reply.splitlines()[-1] == ("Not done yet: “delete the rotate todo”. Ask me for it again once the approval "
                                      "above is settled.")


def test_a_sweep_model_that_fails_costs_the_reply_nothing(chat):
    lunch(chat)

    def down(_shown):
        raise TimeoutError("no answer")

    chat.leftover = down
    chat.model.extend([TO_SCHEDULE, move_lunch()])

    assert "Update schedule event: team lunch" in chat(MESSAGE) and load_schedule(USER, chat.data_dir).entries[0].start == "12:00"
