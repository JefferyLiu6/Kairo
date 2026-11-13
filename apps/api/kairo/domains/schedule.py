"""
The schedule domain: the calendar, run by its own agent.

The agent is shown the calendar with each event's id (`calendar_view`) and
refers to events by id, so it never has to describe an event for code to find.
Its tools only propose: a call that contradicts the message's own date, time or
repeat wording is sent back with the reason before anything is stored.

Tool wording follows what docs/MODEL_DATE_TEST.md measured: an event is anything
at a set time, time held for something, or anything that repeats; a bare hour is
read the likelier way and shown; a date or time is never invented.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kairo.core.domain.commands import (
    OccurrenceOverrideCommand,
    ScheduleAddCommand,
    ScheduleCancelSeriesFromCommand,
    ScheduleModifyOccurrenceCommand,
    ScheduleRemoveCommand,
    ScheduleSkipOccurrenceCommand,
    ScheduleUpdateCommand,
    action_from_command,
)
from kairo.core.prompt import prompt_template
from kairo.domains.checks import (
    asks_for_a_repeat,
    dates_the_wording_allows,
    past_problem,
    range_the_wording_gives,
    repeat_problem,
    say_dates,
    times_the_wording_gives,
)
from kairo.domains.registry import DomainSpec, Proposal, Proposed, ToolContext, ToolSpec
from kairo.storage.store import ScheduleEntry, _entry_recurrence, expand_series, load_schedule
from kairo.tools.calendar.service import CalendarService, is_google_calendar_connected
from kairo.tools.resolvers.schedule import _google_event_ref, resolve_schedule_targets
from kairo.utils.formatters import _format_schedule_remove_summary, _format_time_natural
from kairo.utils.parsing.datetime import _smart_end_time

_DATE = r"^\d{4}-\d{2}-\d{2}$"
_TIME = r"^([01]\d|2[0-3]):[0-5]\d$"
Weekday = Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
AGENDA_DAYS = 14          # how far ahead the agent is shown the calendar day by day
MAX_LISTED = 40           # how many later one-off events it is shown beyond that
MAX_RANGE_DAYS = 92       # the longest range show_events will list


# ── The calendar, as the domain reads it ──────────────────────────────────────

@dataclass(frozen=True)
class Event:
    id: str
    title: str
    date: str                                # YYYY-MM-DD; for a repeating event, where the series starts
    start: str
    end: str
    recurrence: Optional[dict[str, Any]] = None
    exceptions: tuple[str, ...] = ()
    entry: Optional[ScheduleEntry] = None    # the stored entry, for an event in Kairo's own calendar
    external: bool = False                   # mirrored from Google Calendar

    @property
    def repeats(self) -> bool:
        return self.recurrence is not None


def load_events(ctx: ToolContext) -> list[Event]:
    """Every event the user has, from Google Calendar when it is connected and Kairo's own store otherwise."""
    if is_google_calendar_connected(ctx.user_id, ctx.data_dir):
        try:
            refs = [_google_event_ref(e) for e in CalendarService(ctx.user_id, ctx.data_dir).list_events(limit=1000)]
        except Exception:
            return []
        return [Event(str(r["id"]), r["title"], r["date"], r["start"], r["end"], external=True) for r in refs]
    events = []
    for entry in load_schedule(ctx.user_id, ctx.data_dir).entries:
        rule = _entry_recurrence(entry)
        events.append(Event(entry.id, entry.title, entry.date or "", entry.start, entry.end,
                            recurrence=rule.model_dump(mode="json") if rule else None,
                            exceptions=tuple(entry.exceptions), entry=entry))
    return events


def _find(ctx: ToolContext, event_id: str) -> Optional[Event]:
    return next((event for event in load_events(ctx) if event.id == event_id), None)


@dataclass(frozen=True)
class Occurrence:
    day: date
    start: str
    end: str
    title: str
    event: Event


def occurrences(events: list[Event], first: date, last: date) -> list[Occurrence]:
    """What actually happens between two dates: one-off events, and each repeating event day by day."""
    found: list[Occurrence] = []
    for event in events:
        if event.repeats and event.entry is not None:
            found += [Occurrence(day, e.start, e.end, e.title, event) for day, e in expand_series(event.entry, first, last)]
        elif event.date and first.isoformat() <= event.date <= last.isoformat():
            found.append(Occurrence(date.fromisoformat(event.date), event.start, event.end, event.title, event))
    return sorted(found, key=lambda o: (o.day, o.start, o.title.lower()))


def _happens_on(event: Event, day: str) -> bool:
    return bool(occurrences([event], date.fromisoformat(day), date.fromisoformat(day)))


# ── Wording ───────────────────────────────────────────────────────────────────

def _day(value: str | date) -> str:
    day = date.fromisoformat(value) if isinstance(value, str) else value
    return f"{day:%a} {day.day} {day:%b}"


def _hours(start: str, end: str) -> str:
    start, end = _format_time_natural(start), _format_time_natural(end)
    return f"{start}–{end}" if start and end else start


def _clock_range(start: str, end: str) -> str:
    return f"{start}–{end}" if start and end else (start or "no time")


def _repeat_label(event: Event) -> str:
    rule = event.recurrence or {}
    label = rule.get("freq") or "weekly"
    if rule.get("interval", 1) > 1:
        label = f"every {rule['interval']} " + {"daily": "days", "weekly": "weeks", "monthly": "months"}.get(label, label)
    if rule.get("by_day"):
        label += " on " + ", ".join(rule["by_day"])
    label += f", {_clock_range(event.start, event.end)}"
    if event.date:
        label += f", from {event.date}"
    if rule.get("until"):
        label += f", until {rule['until']}"
    if event.exceptions:
        label += "; skipped: " + ", ".join(sorted(event.exceptions))
    return label


def calendar_view(ctx: ToolContext) -> str:
    """The calendar as the agent sees it: the next two weeks day by day, then what is later, with ids."""
    events = load_events(ctx)
    if not events:
        return "The calendar is empty."
    today = ctx.now.date()
    horizon = today + timedelta(days=AGENDA_DAYS - 1)

    def listed(occurrence: Occurrence) -> str:
        return (f"[{occurrence.event.id}] {_clock_range(occurrence.start, occurrence.end)} {occurrence.title}"
                + (" (repeats)" if occurrence.event.repeats else ""))

    by_day: dict[date, list[str]] = {}
    for occurrence in occurrences(events, today, horizon):
        by_day.setdefault(occurrence.day, []).append(listed(occurrence))
    lines = ["Calendar (the id of each event is in brackets).", f"The next {AGENDA_DAYS} days:"]
    for offset in range(AGENDA_DAYS):
        day = today + timedelta(days=offset)
        mark = {0: " (today)", 1: " (tomorrow)"}.get(offset, "")
        lines.append(f"{day:%a} {day.isoformat()}{mark}: " + ("; ".join(by_day[day]) if day in by_day else "nothing"))

    def dated(event: Event) -> str:
        day = date.fromisoformat(event.date)
        return f"[{event.id}] {day:%a} {event.date} {_clock_range(event.start, event.end)} {event.title}"

    one_offs = sorted((e for e in events if not e.repeats and e.date), key=lambda e: (e.date, e.start))
    later = [e for e in one_offs if e.date > horizon.isoformat()]
    earlier = [e for e in one_offs if (today - timedelta(days=7)).isoformat() <= e.date < today.isoformat()]
    if later:
        more = f" (and {len(later) - MAX_LISTED} more after these)" if len(later) > MAX_LISTED else ""
        lines.append("Later: " + "; ".join(dated(e) for e in later[:MAX_LISTED]) + more)
    if earlier:
        lines.append("Earlier this week: " + "; ".join(dated(e) for e in earlier))
    repeating = [e for e in events if e.repeats]
    if repeating:
        lines.append("Repeating events:")
        lines += [f"[{e.id}] {e.title} — {_repeat_label(e)}" for e in repeating]
    return "\n".join(lines)


# ── Arguments ─────────────────────────────────────────────────────────────────

class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Recurrence(_Args):
    freq: Literal["daily", "weekly", "monthly"]
    interval: int = Field(ge=1, description="1 for every day, week or month; 2 for every other; and so on.")
    by_day: list[Weekday] = Field(description="The weekdays of a weekly repeat; empty for daily and monthly.")

    @model_validator(mode="after")
    def _days_fit_the_frequency(self) -> "Recurrence":
        if self.freq != "weekly" and self.by_day:
            raise ValueError(f"a {self.freq} repeat lists no weekdays; use weekly with by_day")
        if self.freq == "weekly" and not self.by_day:
            raise ValueError("a weekly repeat needs at least one weekday")
        return self


class EventArgs(_Args):
    title: str = Field(description="What it is, in the user's own words and language, without the date, the time, "
                                   "repeat words, or words like 'schedule' and 'add'.")
    date: str = Field(pattern=_DATE, description="YYYY-MM-DD. For a repeating event, its first occurrence.")
    start: str = Field(pattern=_TIME, description="24-hour HH:MM.")
    end: Optional[str] = Field(pattern=_TIME, description="24-hour HH:MM, only if the user gave an end time or a "
                                                        "length; otherwise null.")
    recurrence: Optional[Recurrence] = Field(description="Only if the user asked for it to repeat; otherwise null.")


class CreateEvents(_Args):
    """Propose one or more new calendar events: something at a set time, time held for something, or
    anything that repeats, including a reminder that repeats. Put every event of one message in one call."""

    events: list[EventArgs] = Field(min_length=1)


class UpdateEvent(_Args):
    """Propose changing an event that is on the calendar: its time, its day or its title. For an event
    that repeats this changes every occurrence; to change one day only, use change_occurrence. The
    user is asked to approve first."""

    event_id: str = Field(description="The id of the event, from the calendar shown.")
    new_title: Optional[str] = Field(description="The new title, or null to keep it.")
    new_date: Optional[str] = Field(pattern=_DATE, description="YYYY-MM-DD if it moves to another day; otherwise null.")
    new_start: Optional[str] = Field(pattern=_TIME, description="24-hour HH:MM if its start changes; otherwise null.")
    new_end: Optional[str] = Field(pattern=_TIME, description="24-hour HH:MM only if the user gave a new end time or "
                                                            "length; otherwise null and the length is kept.")


class RemoveEvent(_Args):
    """Propose removing an event from the calendar. For an event that repeats this removes every
    occurrence; to drop one day only, use skip_occurrence. The user is asked to approve first."""

    event_id: str = Field(description="The id of the event, from the calendar shown.")


class SkipOccurrence(_Args):
    """Propose skipping one day of an event that repeats. The rest of the series stays."""

    event_id: str = Field(description="The id of the repeating event, from the calendar shown.")
    date: str = Field(pattern=_DATE, description="YYYY-MM-DD of the occurrence to skip.")


class ChangeOccurrence(_Args):
    """Propose changing the time or title of one day of an event that repeats. The rest of the series
    stays. The user is asked to approve first."""

    event_id: str = Field(description="The id of the repeating event, from the calendar shown.")
    date: str = Field(pattern=_DATE, description="YYYY-MM-DD of the occurrence to change.")
    new_title: Optional[str] = Field(description="The title for that day only, or null to keep it.")
    new_start: Optional[str] = Field(pattern=_TIME, description="24-hour HH:MM for that day, or null to keep it.")
    new_end: Optional[str] = Field(pattern=_TIME, description="24-hour HH:MM only if the user gave one; otherwise null.")


class EndSeries(_Args):
    """Propose ending an event that repeats from a given day on. Earlier occurrences stay. The user is
    asked to approve first."""

    event_id: str = Field(description="The id of the repeating event, from the calendar shown.")
    from_date: str = Field(pattern=_DATE, description="YYYY-MM-DD: the first day it no longer happens.")


class ShowEvents(_Args):
    """Show what is on the calendar for one day or a range of days. Use it whenever the user wants to
    see their schedule; the list is read from the calendar, so never write one yourself."""

    from_date: str = Field(pattern=_DATE, description="YYYY-MM-DD, the first day to show.")
    to_date: str = Field(pattern=_DATE, description="YYYY-MM-DD, the last day to show; the same day for one day.")


# ── Checks against the message's own wording ──────────────────────────────────

def _first(ctx: ToolContext, read: Callable[[str], Any]) -> Any:
    """What `read` finds in this message or, if it says nothing on the point, in the request it answers."""
    for text in ctx.wordings():
        found = read(text)
        if found:
            return found
    return None


def _date_problem(ctx: ToolContext, proposed: str) -> Optional[str]:
    allowed = _first(ctx, lambda text: dates_the_wording_allows(text, ctx.now))
    if allowed and date.fromisoformat(proposed) not in allowed:
        return f"date {proposed} does not match the message, which points to {say_dates(allowed)}"
    return None


def _start_problem(ctx: ToolContext, start: Optional[str], *, required: bool = True) -> Optional[str]:
    stated = _first(ctx, times_the_wording_gives)
    if not stated or start in stated or (start is None and not required):
        return None
    wanted = " or ".join(sorted(stated))
    return (f"start {start} does not match the message, which says {wanted}" if start
            else f"no new time was set, but the message says {wanted}")


def _unknown(event_id: str) -> str:
    return (f"no event on the calendar has id {event_id}; use an id from the calendar shown, or call ask_user "
            "if the event is not there")


def _check_new_events(args: CreateEvents, ctx: ToolContext) -> Optional[str]:
    # With several events the message's date and time words may each belong to one of them, so only
    # a single event is compared with the wording. A date in the past is refused either way.
    alone = len(args.events) == 1
    same = {(e.title.strip().casefold(), e.start) for e in args.events}
    if not alone and len(same) == 1 and any(asks_for_a_repeat(text) for text in ctx.wordings()):
        return ("the message asks for one thing that repeats, but it was given as several events; make it one "
                "event with every weekday in its by_day")
    asked_to_repeat = _first(ctx, lambda text: text if asks_for_a_repeat(text) else "") or ""
    for event in args.events:
        repeat = event.recurrence.model_dump() if event.recurrence else None
        if repeat and not asked_to_repeat:      # a repeat nobody asked for, however many events there are
            invented = repeat_problem(ctx.message, repeat)
            if invented:
                return f"event '{event.title}': {invented}"
        # A daily or weekly repeat starts when code says it does (_series_start), so its date is not judged.
        starts_itself = bool(repeat) and repeat["freq"] != "monthly"
        problem = None if starts_itself else past_problem(event.date, ctx.now, start=None if repeat else event.start)
        if not problem and alone:
            stated = _first(ctx, range_the_wording_gives)
            if not repeat:
                problem = _date_problem(ctx, event.date)
            if not problem and stated and (event.start != stated[0] or (event.end and event.end != stated[1])):
                problem = (f"{event.start}–{event.end or '?'} does not match the message, which says "
                           f"{stated[0]}–{stated[1]}")
            problem = problem or (None if stated else _start_problem(ctx, event.start))
            problem = problem or (repeat_problem(asked_to_repeat, repeat) if asked_to_repeat else None)
        if problem:
            return f"event '{event.title}': {problem}"
    return None


def _check_update(args: UpdateEvent, ctx: ToolContext) -> Optional[str]:
    event = _find(ctx, args.event_id)
    if event is None:
        return _unknown(args.event_id)
    if not any((args.new_title, args.new_date, args.new_start, args.new_end)):
        return "there is nothing to change: set the new title, date or time, or call ask_user"
    if event.repeats and args.new_date:
        return (f"'{event.title}' repeats, so it has no single day to move; to change one day use "
                "change_occurrence, and to change its time on every day set new_start only")
    # The message may name two days (the old one and the new one), so the day is not compared with it.
    return past_problem(args.new_date, ctx.now) or _start_problem(ctx, args.new_start, required=not args.new_date)


def _check_known(args: RemoveEvent, ctx: ToolContext) -> Optional[str]:
    return None if _find(ctx, args.event_id) else _unknown(args.event_id)


def _occurrence_problem(event_id: str, day: str, ctx: ToolContext) -> Optional[str]:
    event = _find(ctx, event_id)
    if event is None:
        return _unknown(event_id)
    if not event.repeats:
        return f"'{event.title}' does not repeat; use update_event or remove_event for it"
    return (past_problem(day, ctx.now) or _date_problem(ctx, day)
            or (None if _happens_on(event, day) else f"'{event.title}' does not happen on {day}"))


def _check_skip(args: SkipOccurrence, ctx: ToolContext) -> Optional[str]:
    return _occurrence_problem(args.event_id, args.date, ctx)


def _check_change(args: ChangeOccurrence, ctx: ToolContext) -> Optional[str]:
    problem = _occurrence_problem(args.event_id, args.date, ctx)
    if problem:
        return problem
    if not any((args.new_title, args.new_start, args.new_end)):
        return "there is nothing to change: set the new title or time; to drop that day use skip_occurrence"
    return _start_problem(ctx, args.new_start, required=not args.new_title)


def _check_end(args: EndSeries, ctx: ToolContext) -> Optional[str]:
    event = _find(ctx, args.event_id)
    if event is None:
        return _unknown(args.event_id)
    if not event.repeats:
        return f"'{event.title}' does not repeat; use remove_event to cancel it"
    return past_problem(args.from_date, ctx.now)


def _check_range(args: ShowEvents, _ctx: ToolContext) -> Optional[str]:
    first, last = date.fromisoformat(args.from_date), date.fromisoformat(args.to_date)
    if last < first:
        return "to_date is before from_date"
    if (last - first).days > MAX_RANGE_DAYS:
        return f"that is more than {MAX_RANGE_DAYS} days; show a shorter range"
    return None


# ── Proposals ─────────────────────────────────────────────────────────────────

def _minutes(clock: str) -> int:
    return int(clock[:2]) * 60 + int(clock[3:5])


def _end_keeping_length(event_start: str, event_end: str, new_start: str) -> str:
    """The end that keeps an event as long as it was; an hour if its length is not known."""
    length = (_minutes(event_end) - _minutes(event_start)) % (24 * 60) if event_start and event_end else 60
    total = _minutes(new_start) + (length or 60)
    return f"{(total // 60) % 24:02d}:{total % 60:02d}"


def _overlaps(start: str, end: str, other: Occurrence) -> bool:
    return bool(start and end and other.start and other.end and start < other.end and other.start < end)


_DAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


def _series_start(event: EventArgs, ctx: ToolContext) -> str:
    """The first day a daily or weekly repeat happens: the next day it falls on, counted from the day
    the message names if it names one, and from now otherwise. Worked out here, not by the model."""
    rule = event.recurrence
    if rule.freq == "monthly":
        return event.date       # the day of the month is the rule itself
    today = ctx.now.date()
    stated = _first(ctx, lambda text: dates_the_wording_allows(text, ctx.now))
    day = max(min(stated), today) if stated else today
    for _ in range(15):
        falls = rule.freq == "daily" or _DAY_CODES[day.weekday()] in rule.by_day
        if falls and not (day == today and event.start < f"{ctx.now:%H:%M}"):
            return day.isoformat()
        day += timedelta(days=1)
    return event.date


def _propose_new_events(args: CreateEvents, ctx: ToolContext) -> Proposal:
    existing = load_events(ctx)
    entries, notes = [], []
    for event in args.events:
        title = event.title.strip() or "Scheduled block"
        end = event.end or _smart_end_time(event.start, title)
        starts = _series_start(event, ctx) if event.recurrence else event.date
        entries.append({"title": title, "date": starts, "start": event.start, "end": end,
                        "recurrence": event.recurrence.model_dump() if event.recurrence else None})
        day = date.fromisoformat(starts)
        for other in occurrences(existing, day, day):
            if _overlaps(event.start, end, other):
                notes.append(f"Note: {title} overlaps with {other.title} ({_hours(other.start, other.end)}) "
                             f"on {_day(day)}.")
    summary = f"Add {len(entries)} schedule events" if len(entries) > 1 else f"Add schedule event: {entries[0]['title']}"
    return Proposed([action_from_command("schedule_add", ScheduleAddCommand(entries=entries), summary=summary)],
                    notes=tuple(notes))


def _external_refs(event: Event, ctx: ToolContext) -> dict[str, Any]:
    """The Google Calendar references a command needs to change an event that lives there."""
    if not event.external:
        return {}
    resolved = resolve_schedule_targets(ctx.user_id, ctx.data_dir, {"id": event.id})
    return {"googleEvents": resolved.google_events} if resolved.ok and resolved.google_events else {}


def _propose_update(args: UpdateEvent, ctx: ToolContext) -> Proposal:
    event = _find(ctx, args.event_id)
    patch: dict[str, Any] = {"id": event.id}
    if args.new_title:
        patch["title"] = args.new_title.strip()
    if args.new_date:
        patch["date"] = args.new_date
    if args.new_start or args.new_end:
        start = args.new_start or event.start
        if args.new_start:
            patch["start"] = start
        patch["end"] = args.new_end or _end_keeping_length(event.start, event.end, start)
    new_when = ", ".join(part for part in (
        _day(patch["date"]) if "date" in patch else "",
        _hours(patch.get("start", event.start), patch["end"]) if "end" in patch else "") if part)
    old_when = ", ".join(part for part in (
        _day(event.date) if "date" in patch and event.date else "",
        _hours(event.start, event.end) if "end" in patch else "") if part)
    change = f"{new_when} (was {old_when})" if new_when else f"rename to {patch.get('title')}"
    if new_when and "title" in patch:
        change = f"rename to {patch['title']}, {change}"
    command = ScheduleUpdateCommand.model_validate({"updates": [patch], **_external_refs(event, ctx)})
    return [action_from_command("schedule_update", command, risk_level="medium", requires_approval=True,
                                summary=f"Update schedule event: {event.title} — {change}")]


def _propose_remove(args: RemoveEvent, ctx: ToolContext) -> Proposal:
    event = _find(ctx, args.event_id)
    command = ScheduleRemoveCommand.model_validate({"ids": [event.id], **_external_refs(event, ctx)})
    return [action_from_command("schedule_remove", command, risk_level="medium", requires_approval=True,
                                summary=_format_schedule_remove_summary([event.title]))]


def _series_id(event: Event) -> str:
    return (event.entry.series_id if event.entry else "") or event.id


def _propose_skip(args: SkipOccurrence, ctx: ToolContext) -> Proposal:
    event = _find(ctx, args.event_id)
    command = ScheduleSkipOccurrenceCommand(series_id=_series_id(event), skip_date=args.date)
    return [action_from_command("schedule_add_exception", command, risk_level="medium", requires_approval=True,
                                summary=f"Skip {event.title} on {_day(args.date)}")]


def _propose_change(args: ChangeOccurrence, ctx: ToolContext) -> Proposal:
    event = _find(ctx, args.event_id)
    override: dict[str, Any] = {"original_date": args.date}
    if args.new_start or args.new_end:
        start = args.new_start or event.start
        if args.new_start:
            override["start"] = start
        override["end"] = args.new_end or _end_keeping_length(event.start, event.end, start)
    if args.new_title:
        override["title"] = args.new_title.strip()
    command = ScheduleModifyOccurrenceCommand(series_id=_series_id(event),
                                              override=OccurrenceOverrideCommand.model_validate(override))
    what = _hours(override.get("start", event.start), override["end"]) if "end" in override else f"“{override['title']}”"
    return [action_from_command("schedule_add_override", command, risk_level="medium", requires_approval=True,
                                summary=f"Change {event.title} on {_day(args.date)} only — {what}")]


def _propose_end(args: EndSeries, ctx: ToolContext) -> Proposal:
    event = _find(ctx, args.event_id)
    command = ScheduleCancelSeriesFromCommand(series_id=_series_id(event), from_date=args.from_date)
    return [action_from_command("schedule_cancel_series_from", command, risk_level="medium", requires_approval=True,
                                summary=f"End {event.title}: nothing from {_day(args.from_date)} on")]


def _read_range(args: ShowEvents, ctx: ToolContext) -> str:
    first, last = date.fromisoformat(args.from_date), date.fromisoformat(args.to_date)
    found = occurrences(load_events(ctx), first, last)
    if not found:
        span = f"on {_day(first)}" if first == last else f"from {_day(first)} to {_day(last)}"
        return f"Nothing on your calendar {span}."
    lines: list[str] = []
    current: Optional[date] = None
    for occurrence in found:
        if occurrence.day != current:
            current = occurrence.day
            lines.append(_day(current))
        hours = _hours(occurrence.start, occurrence.end)
        lines.append(f"- {hours + ' ' if hours else ''}{occurrence.title}" + (" (repeats)" if occurrence.event.repeats else ""))
    return "\n".join(lines)


# ── The agent ─────────────────────────────────────────────────────────────────

SCHEDULE_AGENT = prompt_template("schedule_agent", """\
You are Kairo's calendar agent. You look after one user's calendar: events at a set time, time held
for something, and anything that repeats.
$clock

$context

For each message, call exactly one tool. You never change anything yourself: a tool call is a
proposal that is checked before it is stored, and the user is shown exactly what was stored.

Which tool:
- create_events for anything new. Put every event of the message in one call. One thing that
  repeats on several weekdays is one event with those weekdays in by_day, not one event per day.
- update_event, remove_event, skip_occurrence, change_occurrence and end_series act on an event that
  is already on the calendar. Give its id from the calendar above. "Move", "push" and "change" one
  event are update_event; "cancel" and "delete" are remove_event. For an event that repeats, use
  skip_occurrence or change_occurrence when the user means one day, and end_series when they mean
  from some day on.
- show_events when the user wants to see what is on the calendar for a day or a range. When they
  give no range ("what do I have coming up?"), show the next 30 days.
- answer for any other question about the calendar: whether something is on it, whether there is
  room, how many hours. Use only the calendar above. An answer never says that something was added,
  changed or removed, and never promises to do it; to change the calendar, call the tool that does.
- ask_user when a new event has no day at all or no time at all, or when you cannot tell which event
  is meant. Never invent a day or a time. Ask for one thing. Never ask how long an event is or what
  to call it: with no end time given, leave end null, and the title is the user's own words for it.
- hand_back when the message is not about the calendar.

Dates and times:
- Dates are YYYY-MM-DD and times are 24-hour HH:MM, worked out from the dates above.
- A time that has already passed today means the next time that day comes around.
- Read the time of day from context ("dinner at 7" is 19:00, "tonight at 8" is 20:00, "6 in the
  evening" is 18:00). For a bare hour with nothing to settle morning or evening ("meeting at 7"),
  pick the likelier reading; the user is shown the time and can correct it.
- "Friday morning" with no hour is a time held in the morning: pick 09:00 to 12:00.
- Only set an end time or a repeat when the user asked for one.
$open_question""")


SCHEDULE = DomainSpec(
    name="schedule",
    summary="The calendar: anything at a set time, time held for something, anything that repeats (also a "
            "reminder that repeats, like a pill every morning), moving, cancelling or skipping an event, and "
            "questions about the schedule or what is coming up.",
    mode="agent",
    restate="Could you give me the exact day and time for “{message}”?",
    prompt=SCHEDULE_AGENT,
    context=calendar_view,
    tools=(
        ToolSpec("create_events", "propose", CreateEvents, check=_check_new_events, propose=_propose_new_events,
                 examples=("Schedule a board prep session Tuesday at 3pm", "client call with Northwind thurs 2-3pm",
                           "remind me to take my pill every morning at 8")),
        ToolSpec("update_event", "propose", UpdateEvent, check=_check_update, propose=_propose_update,
                 examples=("move the discovery call to 4pm", "Push the Contoso call to 3pm")),
        ToolSpec("remove_event", "propose", RemoveEvent, check=_check_known, propose=_propose_remove,
                 examples=("Cancel the board prep session",)),
        ToolSpec("skip_occurrence", "propose", SkipOccurrence, check=_check_skip, propose=_propose_skip,
                 examples=("Skip gym this Friday",)),
        ToolSpec("change_occurrence", "propose", ChangeOccurrence, check=_check_change, propose=_propose_change,
                 examples=("gym at 7am this monday only",)),
        ToolSpec("end_series", "propose", EndSeries, check=_check_end, propose=_propose_end,
                 examples=("stop the weekly 1:1 from december",)),
        ToolSpec("show_events", "read", ShowEvents, check=_check_range, read=_read_range,
                 examples=("What's on my schedule tomorrow?", "what does thursday look like")),
    ),
)
