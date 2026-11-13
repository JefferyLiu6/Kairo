"""A day but no time: the calendar agent looks at the day, suggests a free time, and the user approves it."""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from kairo.domains.registry import ToolContext
from kairo.domains.schedule import FindFreeTime, _read_free_time
from kairo.storage.control_store import list_approval_requests, pm_db_path
from kairo.storage.store import RecurrenceRule, ScheduleData, ScheduleEntry, load_schedule, save_schedule

from tests.graph.conftest import THURSDAY, USER, call

TO_SCHEDULE = call("hand_off", domain="schedule")
SATURDAY = "2026-11-14"
DENTIST = "schedule a dentist appointment on saturday"


def look(day=SATURDAY, minutes=60, part="any"):
    return call("find_free_time", date=day, minutes=minutes, part_of_day=part)


def suggest(title, day, start, end=None):
    return call("suggest_event", title=title, date=day, limits="Nothing remembered limits it", start=start, end=end)


def calendar(chat):
    return [(e.title, e.date, e.start, e.end) for e in load_schedule(USER, chat.data_dir).entries]


def night_shifts(chat):
    """A nurse's weekend: 7 PM to 7:30 AM on Friday and Saturday, and a lunch on Saturday."""
    save_schedule(ScheduleData(entries=[
        ScheduleEntry(id="shift001", title="night shift", date="2026-11-13", start="19:00", end="07:30"),
        ScheduleEntry(id="shift002", title="night shift", date=SATURDAY, start="19:00", end="07:30"),
        ScheduleEntry(id="lunch001", title="lunch with Sam", date=SATURDAY, start="12:00", end="13:00"),
    ]), USER, chat.data_dir)


def pending(chat):
    return list_approval_requests(USER, chat.data_dir, status="pending", limit=10)


# ── The turn ──────────────────────────────────────────────────────────────────

def test_a_day_with_no_time_is_looked_at_and_a_free_time_is_suggested_for_approval(chat):
    night_shifts(chat)
    chat.model.extend([TO_SCHEDULE, look(), suggest("dentist appointment", SATURDAY, "14:00")])

    reply = chat(DENTIST)

    assert "?" not in reply.splitlines()[0]                       # no question about the time
    assert "Add schedule event: dentist appointment — Sat 14 Nov, 2 PM–3 PM" in reply
    assert "you gave no time, so this one is suggested" in reply
    assert len(calendar(chat)) == 3 and len(pending(chat)) == 1   # nothing is stored until the user says so
    seen = chat.asked[2]["turns"][-1][1]                          # what the agent was shown before it suggested
    assert "Free on Sat 2026-11-14: 07:30–12:00, 13:00–19:00" in seen and "19:00–00:00 night shift" in seen

    assert chat("approve") == "Added to your calendar: dentist appointment — Sat 14 Nov, 2 PM–3 PM."
    assert ("dentist appointment", SATURDAY, "14:00", "15:00") in calendar(chat) and pending(chat) == []


def test_a_rejected_suggestion_stores_nothing(chat):
    chat.model.extend([TO_SCHEDULE, look(), suggest("dentist appointment", SATURDAY, "10:00")])
    chat(DENTIST)

    chat("reject")

    assert calendar(chat) == [] and pending(chat) == []


def test_a_suggested_time_that_is_taken_is_sent_back_and_the_fix_is_what_is_proposed(chat):
    night_shifts(chat)
    chat.model.extend([TO_SCHEDULE, look(), suggest("dentist appointment", SATURDAY, "12:30"),
                       suggest("dentist appointment", SATURDAY, "15:00")])

    reply = chat(DENTIST)

    assert "lunch with Sam (12:00–13:00) is booked then" in chat.asked[3]["turns"][-1][1]
    assert "Sat 14 Nov, 3 PM–4 PM" in reply and len(pending(chat)) == 1


def test_the_morning_after_a_night_shift_is_not_free(chat):
    night_shifts(chat)
    chat.model.extend([TO_SCHEDULE, look(), suggest("dentist appointment", SATURDAY, "07:00"),
                       suggest("dentist appointment", SATURDAY, "07:00")])

    reply = chat(DENTIST)

    assert "night shift (00:00–07:30) is booked then" in chat.asked[3]["turns"][-1][1]
    assert pending(chat) == [] and reply.endswith("?")            # still wrong after one retry: asked, nothing proposed


def test_a_time_the_user_gave_is_never_treated_as_a_suggestion(chat):
    chat.model.extend([TO_SCHEDULE, suggest("dentist", SATURDAY, "15:00"),
                       call("create_events", events=[{"title": "dentist", "date": SATURDAY, "start": "15:00",
                                                      "end": None, "recurrence": None}])])

    reply = chat("dentist saturday at 3pm")

    assert "the message gives a time" in chat.asked[2]["turns"][-1][1]
    assert reply == "Added to your calendar: dentist — Sat 14 Nov, 3 PM–4 PM." and pending(chat) == []


def test_a_part_of_the_day_to_hold_is_not_a_suggestion_but_a_length_in_it_is(chat):
    friday = "2026-11-13"
    chat.model.extend([TO_SCHEDULE, suggest("Acme logo work", friday, "09:00"),
                       call("create_events", events=[{"title": "Acme logo work", "date": friday, "start": "09:00",
                                                      "end": "12:00", "recurrence": None}])])
    reply = chat("block friday morning for the Acme logo work")
    assert "holds the morning" in chat.asked[2]["turns"][-1][1]
    assert reply == "Added to your calendar: Acme logo work — Fri 13 Nov, 9 AM–12 PM." and pending(chat) == []

    chat.model.extend([TO_SCHEDULE, look(friday, 60, "afternoon"), suggest("the report", friday, "14:00")])
    assert "Fri 13 Nov, 2 PM–3 PM" in chat("find me an hour for the report on friday afternoon") and len(pending(chat)) == 1


def test_a_question_about_free_time_is_answered_from_the_lookup_and_nothing_is_proposed(chat):
    night_shifts(chat)
    chat.model.extend([TO_SCHEDULE, look(SATURDAY, 90, "afternoon"), suggest("workshop", SATURDAY, "13:00"),
                       call("answer", text="Yes. Saturday afternoon is free from 1 PM to 5 PM.")])

    reply = chat("can i fit a 90 min workshop on saturday afternoon?")

    assert "the message asks a question" in chat.asked[3]["turns"][-1][1]
    assert reply == "Yes. Saturday afternoon is free from 1 PM to 5 PM." and pending(chat) == []

    chat.model.extend([TO_SCHEDULE, look(), suggest("dentist appointment", SATURDAY, "14:00")])
    assert "Sat 14 Nov, 2 PM–3 PM" in chat("can you schedule a dentist appointment on saturday?")     # a request


def test_a_time_nobody_gave_cannot_be_stored_through_create_events(chat):
    tomorrow = "2026-11-13"
    made_up = call("create_events", events=[{"title": "pharmacy pickup", "date": tomorrow, "start": "09:00",
                                             "end": None, "recurrence": None}])
    chat.model.extend([TO_SCHEDULE, made_up, look(tomorrow), suggest("pharmacy pickup", tomorrow, "10:00")])

    reply = chat("pharmacy pickup tmrw")

    assert "the message gives no time, and one is never made up here" in chat.asked[2]["turns"][-1][1]
    assert calendar(chat) == [] and "Fri 13 Nov, 10 AM–11 AM (you gave no time" in reply


def test_a_suggestion_has_to_be_on_the_day_the_user_named(chat):
    chat.model.extend([TO_SCHEDULE, look("2026-11-15"), suggest("dentist appointment", "2026-11-15", "10:00"),
                       suggest("dentist appointment", SATURDAY, "10:00")])

    reply = chat(DENTIST)

    assert "does not match the message" in chat.asked[3]["turns"][-1][1] and "Sat 14 Nov, 10 AM–11 AM" in reply


def test_the_agent_can_look_at_two_days_and_still_put_one_proposal_right(chat):
    night_shifts(chat)
    chat.model.extend([TO_SCHEDULE, look("2026-11-16"), look("2026-11-17"),
                       suggest("report writing", "2026-11-17", "23:00"), suggest("report writing", "2026-11-17", "09:00")])

    reply = chat("find me an hour for report writing early next week")

    assert "Tue 17 Nov, 9 AM–10 AM" in reply and len(chat.asked) == 5     # the coordinator's call and the agent's four
    assert "outside the hours a time may be suggested in" in chat.asked[4]["turns"][-1][1]


def test_what_the_user_asked_to_be_remembered_is_shown_to_the_calendar_agent(chat):
    profile = os.path.join(os.path.dirname(pm_db_path(USER, chat.data_dir)), "PROFILE.md")
    os.makedirs(os.path.dirname(profile), exist_ok=True)
    with open(profile, "w", encoding="utf-8") as f:
        f.write("# User Profile\n- i sleep until 2pm after night shifts\n")
    chat.model.extend([TO_SCHEDULE, look(), suggest("dentist appointment", SATURDAY, "15:00")])

    chat(DENTIST)

    shown = chat.asked[1]["system"]
    assert "What the user asked to be remembered about them:\n- i sleep until 2pm after night shifts" in shown
    assert chat.asked[1]["tools"][:3] == ["create_events", "find_free_time", "suggest_event"]


# ── The lookup ────────────────────────────────────────────────────────────────

def _ctx(chat, now=THURSDAY):
    return ToolContext(user_id=USER, thread_id="t", data_dir=chat.data_dir, now=now)


def test_free_time_is_worked_out_by_code_within_the_part_of_the_day_asked_for(chat):
    night_shifts(chat)
    afternoon = _read_free_time(FindFreeTime(date=SATURDAY, minutes=90, part_of_day="afternoon"), _ctx(chat))
    assert afternoon.startswith("Free on Sat 2026-11-14 in the afternoon: 13:00–17:00.")
    assert "The usual ones first: 13:00, 14:00, 15:00, 13:30, 14:30, 15:30." in afternoon

    all_day = _read_free_time(FindFreeTime(date=SATURDAY, minutes=60, part_of_day="any"), _ctx(chat))
    # On the hour and in the middle of the day first; lunch (12:00–13:00) is booked, so 12:00 is not offered.
    assert "The usual ones first: 09:00, 10:00, 11:00, 13:00, 14:00, 15:00, 16:00, 17:00." in all_day

    evening = _read_free_time(FindFreeTime(date=SATURDAY, minutes=180, part_of_day="evening"), _ctx(chat))
    assert evening.startswith("No free 180 minutes on Sat 2026-11-14 in the evening between 17:00 and 21:00.")


def test_what_is_remembered_is_said_again_beside_the_free_times(chat):
    night_shifts(chat)
    profile = os.path.join(os.path.dirname(pm_db_path(USER, chat.data_dir)), "PROFILE.md")
    with open(profile, "w", encoding="utf-8") as f:
        f.write("# User Profile\n- i sleep until 2pm after night shifts\n")
    vault = os.path.dirname(profile)
    ctx = ToolContext(user_id=USER, thread_id="t", data_dir=chat.data_dir, now=THURSDAY, vault_dir=vault)

    result = _read_free_time(FindFreeTime(date=SATURDAY, minutes=60, part_of_day="any"), ctx)

    assert result.endswith("rule out any start that goes against what the user asked to be remembered: "
                           "i sleep until 2pm after night shifts.")


def test_an_update_that_changes_nothing_is_sent_back_before_anyone_is_asked_to_approve_it(chat):
    night_shifts(chat)
    chat.model.extend([TO_SCHEDULE,
                       call("update_event", event_id="shift001", new_title=None, new_date=None, new_start="19:00",
                            new_end="07:30"),
                       call("create_events", events=[{"title": "shift", "date": "2026-11-13", "start": "19:00",
                                                      "end": "07:30", "recurrence": None}])])

    reply = chat("add shift tomorrow 7pm to 7:30am")

    assert "is already like that, so this changes nothing" in chat.asked[2]["turns"][-1][1]
    assert reply == "That's already on your calendar: shift." and pending(chat) == []


def test_free_time_today_starts_from_now_and_a_repeating_event_counts(chat):
    save_schedule(ScheduleData(entries=[
        ScheduleEntry(id="stand001", title="standup", date="2026-11-09", start="11:00", end="11:30",
                      recurrence=RecurrenceRule(freq="daily")),
    ]), USER, chat.data_dir)
    now = datetime(2026, 11, 12, 10, 10, tzinfo=ZoneInfo("America/Toronto"))

    today = _read_free_time(FindFreeTime(date="2026-11-12", minutes=30, part_of_day="morning"), _ctx(chat, now))

    assert today.startswith("Free on Thu 2026-11-12 in the morning: 10:30–11:00, 11:30–12:00.")
    assert "Booked that day: 11:00–11:30 standup." in today
