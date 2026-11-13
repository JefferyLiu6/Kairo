"""Several changes that each need approval are asked for once, and approving runs every one of them."""
from __future__ import annotations

from kairo.core.domain.approval_response import has_pending_approval
from kairo.storage.control_store import list_all_audit_events, list_approval_requests
from kairo.storage.store import RecurrenceRule, ScheduleData, ScheduleEntry, load_schedule, save_schedule

from tests.graph.conftest import USER, call, say, seed, todos

TO_SCHEDULE = call("hand_off", domain="schedule")


def pending(chat):
    return list_approval_requests(USER, chat.data_dir, status="pending", limit=10)


def events(chat):
    return [e.title for e in load_schedule(USER, chat.data_dir).entries]


def calendar(chat):
    save_schedule(ScheduleData(entries=[
        ScheduleEntry(id="disc0001", title="discovery call", date="2026-11-13", start="11:00", end="12:00"),
        ScheduleEntry(id="lunch001", title="lunch with Sam", date="2026-11-13", start="12:30", end="13:30"),
        ScheduleEntry(id="gym00001", title="gym", date="2026-11-13", start="06:00", end="07:00",
                      recurrence=RecurrenceRule(freq="weekly", by_day=["MO", "WE", "FR"])),
    ]), USER, chat.data_dir)


# ── Tasks ─────────────────────────────────────────────────────────────────────

def test_deleting_every_task_is_one_approval_that_lists_each_and_one_yes_removes_them_all(chat):
    seed(chat, "rotate API keys", "book dentist", "renew passport")
    chat.model.append(call("remove_todos", task_ids=["t1", "t2", "t3"]))

    prompt = chat("Delete all my todos")

    (batch,) = pending(chat)
    assert batch.action_type == "batch" and batch.risk_level == "medium"
    assert prompt.splitlines()[:5] == [
        f"Approval required [{batch.id}]: 3 changes:", "- Remove todo: rotate API keys", "- Remove todo: book dentist",
        "- Remove todo: renew passport", "Risk: medium."]
    assert has_pending_approval(prompt) and len(todos(chat)) == 3          # nothing is removed yet

    reply = chat("approve")

    assert reply.splitlines() == ["Removed task: rotate API keys.", "Removed task: book dentist.",
                                  "Removed task: renew passport."]
    assert todos(chat) == [] and pending(chat) == [] and len(chat.asked) == 1    # one model call for both turns
    logged = [e["eventType"] for e in list_all_audit_events(USER, chat.data_dir)]
    assert logged.count("approval_created") == 1 and logged.count("approval_executed") == 1


def test_rejecting_a_batch_removes_nothing(chat):
    seed(chat, "rotate API keys", "book dentist")
    chat.model.append(call("remove_todos", task_ids=["t1", "t2"]))
    chat("Delete all my todos")

    chat("reject")

    assert len(todos(chat)) == 2 and pending(chat) == []


def test_one_task_is_still_an_approval_of_its_own(chat):
    seed(chat, "rotate API keys", "book dentist")
    chat.model.append(call("remove_todos", task_ids=["t1", "t1"]))      # the same task, named twice

    chat("delete the rotate api keys todo")

    (one,) = pending(chat)
    assert one.action_type == "todo_remove" and one.summary == "Remove todo: rotate API keys"


def test_a_task_that_cannot_be_found_stops_the_whole_request(chat):
    seed(chat, "rotate API keys")
    made_up = call("remove_todos", task_ids=["t1", "nope"])
    chat.model.extend([made_up, made_up, say("There's no expense report on your list.")])

    chat("delete rotate and the expense report")

    assert pending(chat) == [] and len(todos(chat)) == 1
    assert "no open task has id nope" in chat.asked[1]["turns"][-1][1]


def test_a_batch_approved_twice_runs_once(chat):
    seed(chat, "rotate API keys", "book dentist")
    chat.model.append(call("remove_todos", task_ids=["t1", "t2"]))
    prompt = chat("Delete all my todos")
    approve = f"approve {prompt.split('[')[1].split(']')[0]}"

    chat(approve)
    again = chat(approve)

    assert todos(chat) == [] and "Removed task: rotate API keys." in again     # said again from what was read back


# ── Calendar ──────────────────────────────────────────────────────────────────

def test_deleting_everything_on_the_calendar_is_one_approval_naming_every_event(chat):
    calendar(chat)
    chat.model.extend([TO_SCHEDULE, call("remove_events", event_ids=["disc0001", "lunch001", "gym00001"])])

    prompt = chat("delete everything on my calendar")

    (asked,) = pending(chat)
    assert asked.action_type == "schedule_remove" and asked.payload["ids"] == ["disc0001", "lunch001", "gym00001"]
    assert prompt.splitlines()[:4] == [
        f"Approval required [{asked.id}]: Remove 3 schedule events:", "- discovery call — Fri 13 Nov, 11 AM–12 PM",
        "- lunch with Sam — Fri 13 Nov, 12:30 PM–1:30 PM", "- gym — every occurrence"]
    assert has_pending_approval(prompt) and len(events(chat)) == 3

    chat("approve")
    assert events(chat) == []


def test_a_days_events_leave_a_repeating_event_alone(chat):
    calendar(chat)
    chat.model.extend([TO_SCHEDULE, call("remove_events", event_ids=["disc0001", "lunch001", "gym00001"]),
                       call("remove_events", event_ids=["disc0001", "lunch001"])])

    prompt = chat("cancel everything on friday")

    assert "'gym' repeats, and removing it removes every occurrence" in chat.asked[2]["turns"][-1][1]
    assert "gym" not in prompt and pending(chat)[0].payload["ids"] == ["disc0001", "lunch001"]


# ── A plan put on the calendar ────────────────────────────────────────────────

TO_ASSISTANT = call("hand_off", domain="assistant")


def block(title, start, end, day="2026-11-13"):
    return {"title": title, "date": day, "start": start, "end": end}


def test_a_plan_is_put_on_the_calendar_as_one_approval_that_lists_every_block(chat):
    calendar(chat)                                           # Friday: discovery call 11–12, lunch 12:30–13:30
    seed(chat, "rotate API keys", "write the report")
    chat.model.extend([TO_ASSISTANT, call("block_time", blocks=[block("rotate API keys", "09:00", "10:00"),
                                                              block("write the report", "14:00", "16:00")])])

    prompt = chat("block time tomorrow for my open tasks")

    (asked,) = pending(chat)
    assert asked.action_type == "schedule_add" and asked.risk_level == "low"
    assert prompt.splitlines()[:3] == [
        f"Approval required [{asked.id}]: Add 2 schedule events, at suggested times:",
        "- rotate API keys — Fri 13 Nov, 9 AM–10 AM", "- write the report — Fri 13 Nov, 2 PM–4 PM"]
    assert has_pending_approval(prompt) and len(events(chat)) == 3       # nothing is on the calendar yet

    reply = chat("approve")
    assert "Added to your calendar: rotate API keys — Fri 13 Nov, 9 AM–10 AM." in reply
    assert {"rotate API keys", "write the report"} <= set(events(chat))


def test_a_block_on_top_of_something_is_sent_back_with_what_is_free(chat):
    calendar(chat)
    clash = call("block_time", blocks=[block("write the report", "11:30", "12:30")])
    chat.model.extend([TO_ASSISTANT, clash, call("block_time", blocks=[block("write the report", "14:00", "15:00")])])

    reply = chat("block an hour tomorrow for the report, you pick when")

    sent_back = chat.asked[2]["turns"][-1][1]
    assert "is not free: discovery call (11:00–12:00) is booked then" in sent_back
    assert "free that day: 07:00–11:00, 12:00–12:30, 13:30–21:00" in sent_back
    assert "Fri 13 Nov, 2 PM–3 PM" in reply and len(pending(chat)) == 1


def test_blocks_may_not_overlap_each_other_or_lie_in_the_past_or_the_night(chat):
    from kairo.domains.assistant import BlockTime, _check_blocks
    from kairo.domains.registry import ToolContext
    from tests.graph.conftest import THURSDAY

    ctx = ToolContext(user_id=USER, thread_id="t", data_dir=chat.data_dir, now=THURSDAY, message="block time for these")

    def problem(*blocks):
        return _check_blocks(BlockTime(blocks=list(blocks)), ctx)

    assert problem(block("a", "09:00", "10:00"), block("b", "10:00", "11:00")) is None
    assert "overlaps your own block 'a'" in problem(block("a", "09:00", "10:30"), block("b", "10:00", "11:00"))
    assert "outside the hours" in problem(block("a", "22:00", "23:00"))
    assert "outside the hours" in problem(block("a", "10:00", "09:00"))            # ends before it starts
    assert problem(block("a", "09:00", "10:00", day="2026-11-11")) is not None      # yesterday


def test_a_request_for_a_plan_is_not_turned_into_events(chat):
    one = call("block_time", blocks=[block("write the report", "09:00", "10:00")])
    chat.model.extend([TO_ASSISTANT, one, call("answer", text="Tomorrow is clear. Start with the report.")])

    assert chat("plan my day tomorrow") == "Tomorrow is clear. Start with the report."
    assert "asks for a plan, not for time on the calendar" in chat.asked[2]["turns"][-1][1] and pending(chat) == []

    chat.model.extend([call("hand_off", domain="schedule"),
                       call("suggest_event", title="write the report", date="2026-11-13", limits="none", start="09:00", end=None),
                       call("show_events", from_date="2026-11-13", to_date="2026-11-13")])
    chat("plan my day tomorrow")
    assert "the message does not ask for 'write the report'" in chat.asked[-1]["turns"][-1][1] and pending(chat) == []


def test_removing_an_event_that_is_not_there_removes_nothing_else(chat):
    calendar(chat)                                           # discovery call, lunch with Sam, gym: no standup
    wrong = call("remove_events", event_ids=["lunch001"])
    chat.model.extend([TO_SCHEDULE, wrong, wrong])

    reply = chat("delete the standup")

    assert "nothing here mentions “standup”, so nothing is picked for it" in chat.asked[2]["turns"][-1][1]
    assert pending(chat) == [] and len(events(chat)) == 3 and reply.endswith("?")

    chat.model.extend([TO_SCHEDULE, call("remove_events", event_ids=["disc0001"]), call("remove_events", event_ids=["lunch001"])])
    assert "Remove schedule event: lunch with Sam" in chat("cancel lunch with Sam")
    assert "the message does not name 'discovery call'" in chat.asked[-2 + 1]["turns"][-1][1]
