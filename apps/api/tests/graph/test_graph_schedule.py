"""The calendar on the agent graph: a hand-off to the schedule agent, its questions, and approvals in plain words."""
from __future__ import annotations

import pytest

from kairo.storage.control_store import list_approval_requests
from kairo.storage.decision_log import list_turn_decisions
from kairo.storage.store import RecurrenceRule, ScheduleData, ScheduleEntry, load_schedule, save_schedule

from tests.graph.conftest import THREAD, USER, call, item, say, todos

TO_SCHEDULE = call("hand_off", domain="schedule")


def event(title, date, start, end=None, recurrence=None):
    return {"title": title, "date": date, "start": start, "end": end, "recurrence": recurrence}


def create(*events):
    return call("create_events", events=list(events))


def calendar(chat):
    return [(e.title, e.date, e.start, e.end) for e in load_schedule(USER, chat.data_dir).entries]


def seed(chat):
    save_schedule(ScheduleData(entries=[
        ScheduleEntry(id="disc0001", title="discovery call", date="2026-11-18", start="11:00", end="12:00"),
        ScheduleEntry(id="gym00001", title="gym", date="2026-11-13", start="06:00", end="07:00",
                      recurrence=RecurrenceRule(freq="weekly", by_day=["MO", "WE", "FR"])),
    ]), USER, chat.data_dir)


# ── A new event ───────────────────────────────────────────────────────────────

def test_an_event_is_handed_to_the_schedule_agent_and_answered_from_the_calendar(chat):
    seed(chat)
    chat.model.extend([TO_SCHEDULE, create(event("board prep session", "2026-11-17", "15:00"))])

    reply = chat("Schedule a board prep session Tuesday at 3pm")

    assert reply == "Added to your calendar: board prep session — Tue 17 Nov, 3 PM–4 PM."
    assert ("board prep session", "2026-11-17", "15:00", "16:00") in calendar(chat)
    coordinator, agent = chat.asked
    assert coordinator["prompt"] == "coordinator" and agent["prompt"] == "schedule_agent"
    assert "[disc0001] 11:00–12:00 discovery call" in agent["system"] and "Thursday 2026-11-12 10:00" in agent["system"]
    assert agent["tools"] == ["create_events", "find_free_time", "suggest_event", "update_event", "remove_events",
                              "skip_occurrence", "change_occurrence", "end_series", "show_events", "answer", "ask_user",
                              "hand_back"]
    assert agent["turns"] == [("user", "Schedule a board prep session Tuesday at 3pm")]


def test_a_date_the_agent_gets_wrong_is_sent_back_to_the_agent_and_the_fix_is_stored(chat):
    chat.model.extend([TO_SCHEDULE, create(event("board prep session", "2026-11-24", "15:00")),
                       create(event("board prep session", "2026-11-17", "15:00"))])

    assert chat("Schedule a board prep session Tuesday at 3pm").startswith("Added to your calendar: board prep session")
    assert calendar(chat) == [("board prep session", "2026-11-17", "15:00", "16:00")]
    assert [asked["prompt"] for asked in chat.asked] == ["coordinator", "schedule_agent", "schedule_agent"]
    assert "2026-11-17" in chat.asked[2]["turns"][-1][1]


def test_an_event_that_is_still_wrong_after_one_retry_is_never_stored(chat):
    wrong = create(event("board prep session", "2026-11-24", "15:00"))
    chat.model.extend([TO_SCHEDULE, wrong, wrong])

    reply = chat("Schedule a board prep session Tuesday at 3pm")

    assert calendar(chat) == [] and reply.endswith("?") and "Nothing was saved" in reply
    asked = chat.question().payload
    assert asked["domain"] == "schedule"
    # What is kept, and offered again if the user moves on, is the question, not the explanation.
    assert asked["prompt"] == "Could you give me the exact day and time for “Schedule a board prep session Tuesday at 3pm”?"


def test_a_clash_is_said_alongside_what_was_stored(chat):
    seed(chat)
    chat.model.extend([TO_SCHEDULE, create(event("dentist", "2026-11-18", "11:30", "12:30"))])

    assert chat("dentist next wednesday 11:30 to 12:30").splitlines() == [
        "Added to your calendar: dentist — Wed 18 Nov, 11:30 AM–12:30 PM.",
        "Note: dentist overlaps with discovery call (11 AM–12 PM) on Wed 18 Nov.",
    ]


# ── A question, asked once ────────────────────────────────────────────────────

def ask_time(chat):
    chat.model.extend([TO_SCHEDULE, call("ask_user", question="What hour on Saturday?")])
    assert chat("schedule a dentist appointment on saturday") == "What hour on Saturday?"


def test_the_agents_question_is_remembered_with_who_asked_and_what_about(chat):
    ask_time(chat)
    asked = chat.question().payload
    assert (asked["domain"], asked["prompt"], asked["request"], asked["parked"]) == (
        "schedule", "What hour on Saturday?", "schedule a dentist appointment on saturday", False)
    assert calendar(chat) == []


def test_the_answer_goes_back_to_the_agent_that_asked_and_completes_the_request(chat):
    ask_time(chat)
    chat.model.extend([TO_SCHEDULE, create(event("dentist appointment", "2026-11-14", "15:00"))])

    assert chat("3pm") == "Added to your calendar: dentist appointment — Sat 14 Nov, 3 PM–4 PM."
    coordinator, agent = chat.asked[2:]
    assert 'The schedule agent just asked the user: "What hour on Saturday?"' in coordinator["system"]
    assert 'You just asked the user: "What hour on Saturday?"' in agent["system"]
    assert "schedule a dentist appointment on saturday" in agent["system"]
    assert chat.question() is None


def test_the_completed_request_is_still_checked_against_the_words_it_started_with(chat):
    ask_time(chat)
    chat.model.extend([TO_SCHEDULE, create(event("dentist appointment", "2026-11-15", "15:00")),
                       create(event("dentist appointment", "2026-11-14", "15:00"))])

    chat("3pm")

    assert calendar(chat) == [("dentist appointment", "2026-11-14", "15:00", "16:00")]
    assert "2026-11-14" in chat.asked[4]["turns"][-1][1]      # "saturday" was said a message ago, and still holds


def test_a_message_about_something_else_is_handled_and_the_question_is_parked_once(chat):
    ask_time(chat)
    chat.model.append(call("add_todos", items=[item("buy milk")]))

    assert chat("add todo buy milk").splitlines() == ["Added task: buy milk — no due date.",
                                                    "Still open: What hour on Saturday?"]
    assert todos(chat) == [("buy milk", None, False)] and chat.question().payload["parked"] is True

    # The parked question is offered once more, and an answer still completes the request.
    chat.model.extend([TO_SCHEDULE, create(event("dentist appointment", "2026-11-14", "15:00"))])
    assert chat("3pm").startswith("Added to your calendar: dentist appointment — Sat 14 Nov")
    assert "asked earlier" in chat.asked[3]["system"] and chat.question() is None


def test_a_parked_question_that_is_passed_over_again_is_dropped(chat):
    ask_time(chat)
    chat.model.extend([call("add_todos", items=[item("buy milk")]), call("add_todos", items=[item("buy eggs")])])
    chat("add todo buy milk")

    assert chat("add todo buy eggs") == "Added task: buy eggs — no due date."
    assert chat.question() is None


def test_a_request_gets_at_most_two_questions_in_a_row(chat):
    ask_time(chat)
    chat.model.extend([TO_SCHEDULE, call("ask_user", question="Morning or afternoon?")])
    assert chat("not sure") == "Morning or afternoon?" and chat.question().payload["round"] == 2

    chat.model.extend([TO_SCHEDULE, call("ask_user", question="Roughly which part of the day?")])
    assert chat("whenever") == "Roughly which part of the day?"
    assert chat.question() is None      # said, but not kept: the next message starts fresh


def test_an_approval_command_leaves_the_question_parked_not_lost(chat):
    ask_time(chat)
    reply = chat("approve")
    assert reply.splitlines() == ["You don't have any pending approvals right now.", "Still open: What hour on Saturday?"]
    assert len(chat.asked) == 2 and chat.question().payload["parked"] is True


# ── Changing what is on the calendar ──────────────────────────────────────────

def move_discovery_call(chat):
    seed(chat)
    chat.model.extend([TO_SCHEDULE, call("update_event", event_id="disc0001", new_title=None, new_date=None,
                                         new_start="16:00", new_end=None)])
    return chat("move the discovery call to 4pm")


def test_moving_an_event_waits_for_approval_and_says_what_will_change(chat):
    prompt = move_discovery_call(chat)

    (pending,) = list_approval_requests(USER, chat.data_dir, status="pending")
    assert pending.action_type == "schedule_update" and pending.payload["updates"][0]["start"] == "16:00"
    assert prompt.startswith(f"Approval required [{pending.id}]: Update schedule event: discovery call — "
                             "4 PM–5 PM (was 11 AM–12 PM)")
    assert ("discovery call", "2026-11-18", "11:00", "12:00") in calendar(chat)


def test_yes_right_after_the_approval_prompt_approves_it(chat):
    move_discovery_call(chat)
    chat.model.append(call("decide_approval", decision="approve"))

    reply = chat("Yes please")

    assert reply == "Updated: discovery call — Wed 18 Nov, 4 PM–5 PM (was Wed 18 Nov, 11 AM–12 PM)."
    assert ("discovery call", "2026-11-18", "16:00", "17:00") in calendar(chat)
    assert "decide_approval" in chat.asked[2]["tools"] and "Update schedule event: discovery call" in chat.asked[2]["system"]


def test_no_right_after_the_approval_prompt_rejects_it(chat):
    move_discovery_call(chat)
    chat.model.append(call("decide_approval", decision="reject"))

    assert "cancelled" in chat("no, leave it").lower()
    assert list_approval_requests(USER, chat.data_dir, status="pending") == []
    assert ("discovery call", "2026-11-18", "11:00", "12:00") in calendar(chat)


def test_approving_in_plain_words_is_only_offered_right_after_the_prompt(chat):
    move_discovery_call(chat)
    chat.model.extend([call("add_todos", items=[item("buy milk")]), call("decide_approval", decision="approve"),
                       say("Yes to what? Say 'approve' to go ahead with the pending change.")])
    chat("add todo buy milk")

    reply = chat("Yes please")

    assert "decide_approval" not in chat.asked[3]["tools"]          # the prompt is no longer the last thing said
    assert reply.startswith("Yes to what?")                         # a call the graph did not offer is not obeyed
    assert len(list_approval_requests(USER, chat.data_dir, status="pending")) == 1


def test_approve_right_after_a_prompt_means_that_prompt_even_if_an_older_one_was_never_answered(chat):
    move_discovery_call(chat)                                   # asked, and never answered
    chat.model.extend([TO_SCHEDULE, call("skip_occurrence", event_id="gym00001", date="2026-11-13")])
    chat("Skip gym this Friday")                                # asked just now

    assert chat("approve") == "Skipping gym on Fri 13 Nov; the rest of the series stays."
    (still_pending,) = list_approval_requests(USER, chat.data_dir, status="pending")
    assert still_pending.action_type == "schedule_update"
    assert ("discovery call", "2026-11-18", "11:00", "12:00") in calendar(chat)

    # With no prompt just shown, a bare approve still has to say which one.
    chat.model.extend([TO_SCHEDULE, call("skip_occurrence", event_id="gym00001", date="2026-11-16"),
                       call("add_todos", items=[item("buy milk")])])
    chat("Skip gym on monday")
    chat("add todo buy milk")
    assert chat("approve").startswith("Multiple pending approvals.")


def test_the_first_message_is_never_offered_an_approval(chat):
    chat.model.append(call("list_todos"))
    chat("what's on my list")
    assert "decide_approval" not in chat.asked[0]["tools"]


def test_skipping_one_day_of_a_repeating_event(chat):
    seed(chat)
    chat.model.extend([TO_SCHEDULE, call("skip_occurrence", event_id="gym00001", date="2026-11-13")])

    prompt = chat("Skip gym this Friday")

    (pending,) = list_approval_requests(USER, chat.data_dir, status="pending")
    assert pending.action_type == "schedule_add_exception" and pending.payload["skip_date"] == "2026-11-13"
    assert "Skip gym on Fri 13 Nov" in prompt
    assert chat("approve") == "Skipping gym on Fri 13 Nov; the rest of the series stays."


def test_an_event_id_that_is_not_on_the_calendar_is_sent_back(chat):
    seed(chat)
    chat.model.extend([TO_SCHEDULE, call("remove_events", event_ids=["made-up1"]), call("remove_events", event_ids=["disc0001"])])

    assert "Remove schedule event: discovery call" in chat("Cancel the discovery call")
    assert "made-up1" in chat.asked[2]["turns"][-1][1]


# ── Reading and answering ─────────────────────────────────────────────────────

def test_a_request_to_see_the_calendar_is_answered_from_the_calendar(chat):
    seed(chat)
    chat.model.extend([TO_SCHEDULE, call("show_events", from_date="2026-11-18", to_date="2026-11-18")])

    assert chat("what does next wednesday look like").splitlines() == [
        "Wed 18 Nov", "- 6 AM–7 AM gym (repeats)", "- 11 AM–12 PM discovery call"]
    assert len(chat.asked) == 2


def test_a_question_about_the_calendar_is_answered_in_words(chat):
    seed(chat)
    chat.model.extend([TO_SCHEDULE, call("answer", text="Yes, the discovery call is on Wed 18 Nov at 11 AM.")])
    assert chat("did you save my discovery call?") == "Yes, the discovery call is on Wed 18 Nov at 11 AM."


@pytest.mark.parametrize("claim", ["I've scheduled your client call for Thursday at 2 PM.",
                                   "I'll schedule your client call with Northwind for Thursday. Just a moment!",
                                   "Your call has been added to the calendar."])
def test_an_answer_cannot_say_something_was_done(chat, claim):
    chat.model.extend([TO_SCHEDULE, call("answer", text=claim),
                       create(event("client call with Northwind", "2026-11-12", "14:00", "15:00"))])

    reply = chat("client call with Northwind thurs 2-3pm")

    assert reply == "Added to your calendar: client call with Northwind — Thu 12 Nov, 2 PM–3 PM."
    assert "create_events" in chat.asked[2]["turns"][-1][1] or "tool" in chat.asked[2]["turns"][-1][1]


def test_an_answer_that_keeps_claiming_a_change_is_replaced_with_the_truth(chat):
    claim = call("answer", text="I've scheduled your client call for Thursday at 2 PM.")
    chat.model.extend([TO_SCHEDULE, claim, claim])

    reply = chat("client call with Northwind thurs 2-3pm")

    assert "scheduled" not in reply and "haven't changed anything" in reply and calendar(chat) == []


def test_the_agent_can_hand_back_what_is_not_about_the_calendar(chat):
    chat.model.extend([TO_SCHEDULE, call("hand_back"), say("That isn't on your calendar.")])
    assert chat("I did my morning run") == "That isn't on your calendar."
    assert [asked["prompt"] for asked in chat.asked] == ["coordinator", "schedule_agent", "assistant_agent"]


# ── What is recorded ──────────────────────────────────────────────────────────

def test_a_calendar_turn_is_logged_with_the_agent_that_handled_it(chat):
    chat.model.extend([TO_SCHEDULE, create(event("board prep session", "2026-11-17", "15:00"))])
    chat("Schedule a board prep session Tuesday at 3pm")

    (row,) = [r for r in list_turn_decisions(THREAD, chat.data_dir, user_id=USER)
              if r["workingMemory"]["source"] == "orchestrator"]
    assert "create_events" in row["routing"]["reason"] and "by=schedule" in row["routing"]["reason"]
    assert set(row["versions"]["bundles"]) == {"coordinator", "schedule"}
    assert row["versions"]["receipts"] == ["verified"]


def test_the_old_workflow_is_not_confused_by_a_question_the_graph_left_open(chat):
    """If the setting is switched off mid-conversation, the previous path sees a kind of question it does not own."""
    from kairo.agents.personal_manager.agent import PMConfig, run_pm

    ask_time(chat)
    config = PMConfig(user_id=USER, provider="test", model="unused", data_dir=chat.data_dir, session_id=THREAD)
    reply = run_pm("add todo buy milk", config)

    assert "buy milk" in reply and todos(chat) == [("buy milk", None, False)]
