"""Several changes that each need approval are asked for once, and approving runs every one of them."""
from __future__ import annotations

from kairo.core.domain.approval_response import has_pending_approval
from kairo.storage.control_store import list_all_audit_events, list_approval_requests
from kairo.storage.store import RecurrenceRule, ScheduleData, ScheduleEntry, load_schedule, save_schedule

from tests.graph.conftest import USER, call, seed, todos

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
    chat.model.append(call("remove_todos", queries=["rotate", "dentist", "passport"]))

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
    chat.model.append(call("remove_todos", queries=["rotate", "dentist"]))
    chat("Delete all my todos")

    chat("reject")

    assert len(todos(chat)) == 2 and pending(chat) == []


def test_one_task_is_still_an_approval_of_its_own(chat):
    seed(chat, "rotate API keys", "book dentist")
    chat.model.append(call("remove_todos", queries=["rotate", "rotate API"]))      # the same task, named twice

    chat("delete the rotate api keys todo")

    (one,) = pending(chat)
    assert one.action_type == "todo_remove" and one.summary == "Remove todo: rotate API keys"


def test_a_task_that_cannot_be_found_stops_the_whole_request(chat):
    seed(chat, "rotate API keys")
    chat.model.extend([call("remove_todos", queries=["rotate", "expense report"]),
                       call("remove_todos", queries=["rotate", "expense report"])])

    chat("delete rotate and the expense report")

    assert pending(chat) == [] and len(todos(chat)) == 1


def test_a_batch_approved_twice_runs_once(chat):
    seed(chat, "rotate API keys", "book dentist")
    chat.model.append(call("remove_todos", queries=["rotate", "dentist"]))
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
