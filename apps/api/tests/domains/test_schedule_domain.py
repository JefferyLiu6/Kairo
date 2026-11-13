"""The schedule domain: what each calendar tool checks, proposes and reads."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from kairo.domains.catalog import DOMAINS
from kairo.domains.registry import Proposed, ToolContext
from kairo.storage.store import RecurrenceRule, ScheduleData, ScheduleEntry, save_schedule

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=ZoneInfo("America/Toronto"))   # a Thursday
USER = "schedule-user"
SCHEDULE = DOMAINS.domain("schedule")


def call(name: str, tmp_path, message="", request="", **args):
    tool = DOMAINS.tool(name)
    context = ToolContext(user_id=USER, thread_id="pm-schedule", data_dir=str(tmp_path), now=NOW,
                          message=message, request=request)
    return tool, tool.args.model_validate(args), context


def event(title, date, start, end=None, recurrence=None):
    return {"title": title, "date": date, "start": start, "end": end, "recurrence": recurrence}


def weekly(*days, interval=1):
    return {"freq": "weekly", "interval": interval, "by_day": list(days)}


DAILY = {"freq": "daily", "interval": 1, "by_day": []}


def seed(tmp_path):
    """A one-off call, a half-hour call, and gym every Monday, Wednesday and Friday at 6."""
    save_schedule(ScheduleData(entries=[
        ScheduleEntry(id="disc0001", title="discovery call", date="2026-11-18", start="11:00", end="12:00"),
        ScheduleEntry(id="dana0001", title="call with Dana at Contoso", date="2026-11-13", start="14:00", end="14:30"),
        ScheduleEntry(id="gym00001", title="gym", date="2026-11-13", start="06:00", end="07:00",
                      recurrence=RecurrenceRule(freq="weekly", by_day=["MO", "WE", "FR"])),
    ]), USER, str(tmp_path))


def only(proposal):
    actions = proposal.actions if isinstance(proposal, Proposed) else proposal
    assert len(actions) == 1
    return actions[0]


# ── create_events ─────────────────────────────────────────────────────────────

def test_an_event_becomes_one_command_with_a_default_length_when_none_was_given(tmp_path):
    tool, args, ctx = call("create_events", tmp_path, events=[event("board prep session", "2026-11-17", "15:00")])
    action = only(tool.propose(args, ctx))
    assert action.action_type == "schedule_add" and action.summary == "Add schedule event: board prep session"
    (entry,) = action.payload["entries"]
    assert (entry["title"], entry["date"], entry["start"], entry["end"]) == ("board prep session", "2026-11-17", "15:00", "16:00")
    assert entry["recurrence"] is None


def test_an_end_time_and_a_repeat_are_carried_into_the_command(tmp_path):
    tool, args, ctx = call("create_events", tmp_path, events=[
        event("call with Dana at Contoso", "2026-11-13", "14:00", "14:30"),
        event("pipeline review", "2026-11-16", "09:00", recurrence=weekly("MO"))])
    first, second = only(tool.propose(args, ctx)).payload["entries"]
    assert first["end"] == "14:30"
    assert second["recurrence"] == {"freq": "weekly", "interval": 1, "by_day": ["MO"]}


@pytest.mark.parametrize("message, proposed, starts", [
    # It is Thursday 10:00. A repeat starts the next time it falls, whatever first day the model gave.
    ("Schedule gym every Monday, Wednesday and Friday at 6am",
     event("gym", "2026-11-16", "06:00", recurrence=weekly("MO", "WE", "FR")), "2026-11-13"),
    ("standup every weekday at 9:15am", event("standup", "2026-11-12", "09:15",
                                             recurrence=weekly("MO", "TU", "WE", "TH", "FR")), "2026-11-13"),
    ("team lunch every thursday at noon", event("team lunch", "2026-11-19", "12:00", recurrence=weekly("TH")), "2026-11-12"),
    ("remind me to take my pill every morning at 8", event("take my pill", "2026-11-12", "08:00", recurrence=DAILY), "2026-11-13"),
    ("remind me to stretch every evening at 9pm", event("stretch", "2026-11-01", "21:00", recurrence=DAILY), "2026-11-12"),
    ("yoga every monday at 7am starting dec 1", event("yoga", "2026-12-01", "07:00", recurrence=weekly("MO")), "2026-12-07"),
])
def test_a_repeating_event_starts_the_next_time_it_falls(tmp_path, message, proposed, starts):
    tool, args, ctx = call("create_events", tmp_path, message, events=[proposed])
    assert tool.check(args, ctx) is None
    assert only(tool.propose(args, ctx)).payload["entries"][0]["date"] == starts


@pytest.mark.parametrize("message, proposed, clue", [
    ("Schedule a board prep session Tuesday at 3pm", event("board prep", "2026-11-24", "15:00"), "2026-11-17"),
    ("Schedule a board prep session Tuesday at 3pm", event("board prep", "2026-11-17", "03:00"), "15:00"),
    ("client call with Northwind thurs 2-3pm", event("client call", "2026-11-12", "15:00", "16:00"), "14:00"),
    ("lunch with Sam yesterday at noon", event("lunch with Sam", "2026-11-11", "12:00"), "past"),
    ("standup today at 9am", event("standup", "2026-11-12", "09:00"), "passed"),
    ("Schedule a pipeline review every Monday at 9am", event("pipeline review", "2026-11-16", "09:00"), "repeat"),
    ("Schedule gym every Monday, Wednesday and Friday at 6am",
     event("gym", "2026-11-13", "06:00", recurrence=weekly("MO", "WE")), "FR"),
])
def test_an_event_that_contradicts_the_message_is_refused_with_the_reason(tmp_path, message, proposed, clue):
    tool, args, ctx = call("create_events", tmp_path, message, events=[proposed])
    assert clue in tool.check(args, ctx)


@pytest.mark.parametrize("message, proposed", [
    ("Schedule a board prep session Tuesday at 3pm", event("board prep session", "2026-11-17", "15:00")),
    ("client call with Northwind thurs 2-3pm", event("client call with Northwind", "2026-11-12", "14:00", "15:00")),
    ("block friday morning for the Acme logo work", event("Acme logo work", "2026-11-13", "09:00", "12:00")),
    ("Schedule gym every Monday, Wednesday and Friday at 6am",
     event("gym", "2026-11-13", "06:00", recurrence=weekly("MO", "WE", "FR"))),
    ("明天下午3点和王医生见面", event("和王医生见面", "2026-11-13", "15:00")),      # not readable: not checked
])
def test_an_event_that_agrees_with_the_message_passes(tmp_path, message, proposed):
    tool, args, ctx = call("create_events", tmp_path, message, events=[proposed])
    assert tool.check(args, ctx) is None


def test_days_named_without_a_repeat_are_separate_events_not_a_series(tmp_path):
    said = "dinner service fri and sat 5pm to 11pm"
    invented = [event("dinner service", "2026-11-13", "17:00", "23:00", recurrence=weekly("FR", "SA")),
                event("dinner service", "2026-11-14", "17:00", "23:00")]
    tool, args, ctx = call("create_events", tmp_path, said, events=invented)
    assert "does not ask" in tool.check(args, ctx)
    each_day = [event("dinner service", day, "17:00", "23:00") for day in ("2026-11-13", "2026-11-14")]
    tool, args, ctx = call("create_events", tmp_path, said, events=each_day)
    assert tool.check(args, ctx) is None


def test_one_repeating_event_per_weekday_is_sent_back_to_be_one_event(tmp_path):
    days = [event("gym", "2026-11-16", "06:00", recurrence=weekly(day)) for day in ("MO", "WE", "FR")]
    tool, args, ctx = call("create_events", tmp_path, "Schedule gym every Monday, Wednesday and Friday at 6am", events=days)
    assert "one event" in tool.check(args, ctx) and "by_day" in tool.check(args, ctx)
    # The same goes for one repeating day plus loose copies of the others.
    mixed = [event("gym", "2026-11-16", "06:00", recurrence=weekly("MO")), event("gym", "2026-11-18", "06:00"),
             event("gym", "2026-11-20", "06:00")]
    tool, args, ctx = call("create_events", tmp_path, "Schedule gym every Monday, Wednesday and Friday at 6am", events=mixed)
    assert "one event" in tool.check(args, ctx)
    # Several separate dates with no repeat asked for are several events.
    shifts = [event("night shift", day, "19:00", "07:30") for day in ("2026-11-13", "2026-11-14", "2026-11-15")]
    tool, args, ctx = call("create_events", tmp_path, "night shift 7pm to 7:30am fri sat sun", events=shifts)
    assert tool.check(args, ctx) is None
    # Different things that each repeat are fine.
    tool, args, ctx = call("create_events", tmp_path, "gym every monday at 6am and yoga every friday at 7am", events=[
        event("gym", "2026-11-16", "06:00", recurrence=weekly("MO")), event("yoga", "2026-11-13", "07:00", recurrence=weekly("FR"))])
    assert tool.check(args, ctx) is None


def test_a_refusal_names_the_weekday_so_the_fix_is_a_lookup(tmp_path):
    tool, args, ctx = call("create_events", tmp_path, "Schedule a board prep session Tuesday at 3pm",
                           events=[event("board prep", "2026-11-24", "15:00")])
    assert "2026-11-17 (Tue)" in tool.check(args, ctx)


def test_an_answer_to_a_question_is_checked_against_the_request_it_completes(tmp_path):
    asked_about = "schedule a dentist appointment on saturday"
    tool, args, ctx = call("create_events", tmp_path, "3pm", asked_about,
                           events=[event("dentist appointment", "2026-11-14", "15:00")])
    assert tool.check(args, ctx) is None
    tool, args, ctx = call("create_events", tmp_path, "3pm", asked_about,
                           events=[event("dentist appointment", "2026-11-15", "15:00")])
    assert "2026-11-14" in tool.check(args, ctx)
    # A day in the answer replaces the one in the request.
    tool, args, ctx = call("create_events", tmp_path, "actually sunday at 3pm", asked_about,
                           events=[event("dentist appointment", "2026-11-15", "15:00")])
    assert tool.check(args, ctx) is None


def test_a_clash_with_something_already_on_the_calendar_is_noted_not_hidden(tmp_path):
    seed(tmp_path)
    tool, args, ctx = call("create_events", tmp_path, events=[event("dentist", "2026-11-13", "14:15", "15:00")])
    proposal = tool.propose(args, ctx)
    assert isinstance(proposal, Proposed) and len(proposal.notes) == 1
    assert "call with Dana at Contoso" in proposal.notes[0] and "2 PM" in proposal.notes[0]
    # A repeating event clashes on the days it happens.
    tool, args, ctx = call("create_events", tmp_path, events=[event("run", "2026-11-16", "06:30", "07:30")])
    assert "gym" in tool.propose(args, ctx).notes[0]
    tool, args, ctx = call("create_events", tmp_path, events=[event("run", "2026-11-17", "06:30", "07:30")])
    assert not isinstance(tool.propose(args, ctx), Proposed) or not tool.propose(args, ctx).notes


@pytest.mark.parametrize("bad", [
    {"events": []},
    {"events": [event("x", "tomorrow", "15:00")]},
    {"events": [event("x", "2026-11-13", "3pm")]},
    {"events": [event("x", "2026-11-13", "15:00", recurrence={"freq": "weekly", "interval": 1, "by_day": []})]},
    {"events": [event("x", "2026-11-13", "15:00", recurrence={"freq": "daily", "interval": 1, "by_day": ["MO"]})]},
])
def test_arguments_outside_the_contract_are_rejected(bad):
    with pytest.raises(ValidationError):
        DOMAINS.tool("create_events").args.model_validate(bad)


# ── update_event and remove_event ─────────────────────────────────────────────

def test_moving_an_event_keeps_its_length(tmp_path):
    seed(tmp_path)
    tool, args, ctx = call("update_event", tmp_path, "Push the Contoso call to 3pm", event_id="dana0001",
                           new_title=None, new_date=None, new_start="15:00", new_end=None)
    assert tool.check(args, ctx) is None
    action = only(tool.propose(args, ctx))
    assert action.action_type == "schedule_update" and action.requires_approval
    assert action.payload["updates"] == [{"id": "dana0001", "start": "15:00", "end": "15:30"}]
    assert action.summary == "Update schedule event: call with Dana at Contoso — 3 PM–3:30 PM (was 2 PM–2:30 PM)"


def test_moving_an_event_to_another_day_changes_only_the_day(tmp_path):
    seed(tmp_path)
    tool, args, ctx = call("update_event", tmp_path, "move the discovery call to thursday", event_id="disc0001",
                           new_title=None, new_date="2026-11-19", new_start=None, new_end=None)
    assert only(tool.propose(args, ctx)).payload["updates"] == [{"id": "disc0001", "date": "2026-11-19"}]


@pytest.mark.parametrize("message, args, clue", [
    ("move the discovery call to 4pm", dict(event_id="nope0000", new_start="16:00"), "nope0000"),
    ("move the discovery call to 4pm", dict(event_id="disc0001"), "nothing to change"),
    ("move the discovery call to 4pm", dict(event_id="disc0001", new_start="04:00"), "16:00"),
    ("move the discovery call to last monday", dict(event_id="disc0001", new_date="2026-11-09"), "past"),
    ("move gym to saturday", dict(event_id="gym00001", new_date="2026-11-14"), "change_occurrence"),
])
def test_an_update_that_cannot_be_right_is_refused_with_the_reason(tmp_path, message, args, clue):
    seed(tmp_path)
    full = {"new_title": None, "new_date": None, "new_start": None, "new_end": None, **args}
    tool, parsed, ctx = call("update_event", tmp_path, message, **full)
    assert clue in tool.check(parsed, ctx)


def test_removing_an_event_names_it_and_waits_for_approval(tmp_path):
    seed(tmp_path)
    tool, args, ctx = call("remove_event", tmp_path, "Cancel the discovery call", event_id="disc0001")
    action = only(tool.propose(args, ctx))
    assert action.action_type == "schedule_remove" and action.payload["ids"] == ["disc0001"]
    assert action.requires_approval and action.summary == "Remove schedule event: discovery call"
    tool, args, ctx = call("remove_event", tmp_path, event_id="nope0000")
    assert "nope0000" in tool.check(args, ctx)


# ── One occurrence of a repeating event, and ending a series ──────────────────

def test_skipping_one_occurrence_leaves_the_series(tmp_path):
    seed(tmp_path)
    tool, args, ctx = call("skip_occurrence", tmp_path, "Skip gym this Friday", event_id="gym00001", date="2026-11-13")
    assert tool.check(args, ctx) is None
    action = only(tool.propose(args, ctx))
    assert action.action_type == "schedule_add_exception"
    assert action.payload == {"series_id": "gym00001", "skip_date": "2026-11-13"}
    assert action.summary == "Skip gym on Fri 13 Nov"


@pytest.mark.parametrize("message, args, clue", [
    ("Skip gym this Friday", dict(event_id="gym00001", date="2026-11-20"), "2026-11-13"),      # the wrong Friday
    ("Skip gym on the 17th", dict(event_id="gym00001", date="2026-11-17"), "does not happen"),  # a Tuesday
    ("Skip the discovery call", dict(event_id="disc0001", date="2026-11-18"), "does not repeat"),
])
def test_a_skip_that_cannot_be_right_is_refused_with_the_reason(tmp_path, message, args, clue):
    seed(tmp_path)
    tool, parsed, ctx = call("skip_occurrence", tmp_path, message, **args)
    assert clue in tool.check(parsed, ctx)


def test_changing_one_occurrence_overrides_that_day_only(tmp_path):
    seed(tmp_path)
    tool, args, ctx = call("change_occurrence", tmp_path, "gym at 7am this monday", event_id="gym00001",
                           date="2026-11-16", new_title=None, new_start="07:00", new_end=None)
    assert tool.check(args, ctx) is None
    action = only(tool.propose(args, ctx))
    assert action.action_type == "schedule_add_override" and action.payload["series_id"] == "gym00001"
    assert action.payload["override"] == {"original_date": "2026-11-16", "start": "07:00", "end": "08:00"}


def test_ending_a_series_keeps_what_came_before(tmp_path):
    seed(tmp_path)
    tool, args, ctx = call("end_series", tmp_path, "stop gym from december", event_id="gym00001", from_date="2026-12-01")
    assert tool.check(args, ctx) is None
    action = only(tool.propose(args, ctx))
    assert action.action_type == "schedule_cancel_series_from"
    assert action.payload == {"series_id": "gym00001", "from_date": "2026-12-01"}
    tool, args, ctx = call("end_series", tmp_path, event_id="disc0001", from_date="2026-12-01")
    assert "does not repeat" in tool.check(args, ctx)


# ── Reading ───────────────────────────────────────────────────────────────────

def test_showing_a_range_lists_each_day_with_repeating_events_expanded(tmp_path):
    seed(tmp_path)
    tool, args, ctx = call("show_events", tmp_path, from_date="2026-11-13", to_date="2026-11-16")
    assert tool.read(args, ctx).splitlines() == [
        "Fri 13 Nov",
        "- 6 AM–7 AM gym (repeats)",
        "- 2 PM–2:30 PM call with Dana at Contoso",
        "Mon 16 Nov",
        "- 6 AM–7 AM gym (repeats)",
    ]


def test_a_skipped_occurrence_is_not_shown_and_an_empty_range_says_so(tmp_path):
    seed(tmp_path)
    save_schedule(ScheduleData(entries=[
        ScheduleEntry(id="gym00001", title="gym", date="2026-11-13", start="06:00", end="07:00",
                      recurrence=RecurrenceRule(freq="weekly", by_day=["MO", "WE", "FR"]), exceptions=["2026-11-13"]),
    ]), USER, str(tmp_path))
    tool, args, ctx = call("show_events", tmp_path, from_date="2026-11-13", to_date="2026-11-13")
    assert tool.read(args, ctx) == "Nothing on your calendar on Fri 13 Nov."
    tool, args, ctx = call("show_events", tmp_path, from_date="2026-11-14", to_date="2026-11-15")
    assert tool.read(args, ctx) == "Nothing on your calendar from Sat 14 Nov to Sun 15 Nov."
    tool, args, ctx = call("show_events", tmp_path, from_date="2026-11-15", to_date="2026-11-14")
    assert "before" in tool.check(args, ctx)


def test_the_agent_is_shown_the_calendar_with_the_ids_it_must_use(tmp_path):
    seed(tmp_path)
    _, _, ctx = call("show_events", tmp_path, from_date="2026-11-13", to_date="2026-11-13")
    shown = SCHEDULE.context(ctx)
    assert "Thu 2026-11-12 (today): nothing" in shown
    assert "Fri 2026-11-13 (tomorrow): [gym00001] 06:00–07:00 gym (repeats); [dana0001] 14:00–14:30 call with Dana at Contoso" in shown
    assert "Wed 2026-11-18: [gym00001] 06:00–07:00 gym (repeats); [disc0001] 11:00–12:00 discovery call" in shown
    assert "[gym00001] gym — weekly on MO, WE, FR, 06:00–07:00, from 2026-11-13" in shown


def test_an_empty_calendar_and_later_events_are_both_said(tmp_path):
    _, _, ctx = call("show_events", tmp_path, from_date="2026-11-13", to_date="2026-11-13")
    assert "The calendar is empty." in SCHEDULE.context(ctx)
    save_schedule(ScheduleData(entries=[
        ScheduleEntry(id="reci0001", title="granddaughter's recital", date="2026-12-05", start="18:00", end="19:00"),
        ScheduleEntry(id="old00001", title="dentist", date="2026-11-10", start="09:00", end="10:00"),
    ]), USER, str(tmp_path))
    shown = SCHEDULE.context(ctx)
    assert "Later: [reci0001] Sat 2026-12-05 18:00–19:00 granddaughter's recital" in shown
    assert "Earlier this week: [old00001] Tue 2026-11-10 09:00–10:00 dentist" in shown
