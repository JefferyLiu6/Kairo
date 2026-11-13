"""
The candidate tool contract for date-bearing proposals, and what the model is told.

These are the model-facing argument shapes Kairo 2.0 would use for
`create_event` and `add_todo`: every field typed, dates as YYYY-MM-DD, times as
24-hour HH:MM, weekdays as an enum, and an explicit `ask_user` so "I am not
sure" is something the model can say. They live here until the tool registry
exists (docs/ideas/kairo-2.0-agent-architecture.md, section 8).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Literal, Optional

from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

_DATE = r"^\d{4}-\d{2}-\d{2}$"
_TIME = r"^([01]\d|2[0-3]):[0-5]\d$"
Weekday = Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"]

# date_only: today's date only. calendar: weekday plus the next two weeks spelt out.
# calendar_single: the calendar prompt, with the model allowed one tool call per message.
ARMS = ("date_only", "calendar", "calendar_single")


def single_call(arm: str) -> bool:
    """Whether this arm limits the model to one tool call per message."""
    return arm == "calendar_single"


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Recurrence(_Args):
    freq: Literal["daily", "weekly"]
    interval: int = Field(ge=1, description="1 for every week or day, 2 for every other, and so on.")
    by_day: list[Weekday] = Field(description="Weekdays for a weekly repeat; empty for daily.")

    @model_validator(mode="after")
    def _days_fit_the_frequency(self) -> "Recurrence":
        if self.freq == "daily" and self.by_day:
            raise ValueError("a daily repeat lists no weekdays; use weekly with by_day")
        if self.freq == "weekly" and not self.by_day:
            raise ValueError("a weekly repeat needs at least one weekday")
        return self


class CreateEvent(_Args):
    """Propose a calendar event: something at a set time. That includes a reminder that repeats
    ("every morning at 8") and time held for something ("block Friday morning for the report")."""

    title: str = Field(description="What it is, in the user's words, without the date or time.")
    date: str = Field(pattern=_DATE, description="YYYY-MM-DD. For a repeating event, the first occurrence.")
    start: str = Field(pattern=_TIME, description="24-hour HH:MM.")
    end: Optional[str] = Field(pattern=_TIME, description="24-hour HH:MM, only if the user gave an end time or "
                                                        "a duration; otherwise null.")
    recurrence: Optional[Recurrence] = Field(description="Only if the user asked for it to repeat; otherwise null.")


class AddTodo(_Args):
    """Propose a task: one thing to get done, optionally by a deadline. A task never repeats and
    never holds time on the calendar; for either of those, propose an event instead."""

    title: str = Field(description="What to do, in the user's words, without the due date.")
    due_date: Optional[str] = Field(pattern=_DATE, description="YYYY-MM-DD if the user gave a deadline; "
                                                             "otherwise null.")
    due_time: Optional[str] = Field(pattern=_TIME, description="24-hour HH:MM if the user gave a time; "
                                                             "otherwise null.")


class AskUser(_Args):
    """Ask the user for the one thing that is missing or unclear, instead of guessing it."""

    question: str


TOOLS: dict[str, type[_Args]] = {"create_event": CreateEvent, "add_todo": AddTodo, "ask_user": AskUser}


def openai_tools() -> list[dict[str, Any]]:
    """The tools as strict function definitions, so a model cannot omit a field or change its type."""
    tools = []
    for name, model in TOOLS.items():
        tool = convert_to_openai_tool(model, strict=True)
        tool["function"]["name"] = name
        tools.append(tool)
    return tools


def validate_call(name: str, args: dict[str, Any], *, now: Optional[datetime] = None) -> Optional[str]:
    """None if the call fits the contract; otherwise why not. The gate would reject a call that does not.

    With `now`, a date before today is rejected, and so is a one-off event today whose start has already
    passed: nobody schedules or sets a deadline in the past. A repeating event may still begin today.
    """
    model = TOOLS.get(name)
    if model is None:
        return f"unknown tool {name!r}"
    try:
        model.model_validate(args)
    except ValidationError as exc:
        error = exc.errors()[0]
        return f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
    if now is not None:
        today = now.date().isoformat()
        for field in ("date", "due_date"):
            if args.get(field) and args[field] < today:
                return f"{field} {args[field]} is in the past"
        one_off = name == "create_event" and not args.get("recurrence")
        if one_off and args["date"] == today and args["start"] < f"{now:%H:%M}":
            return f"start {args['start']} has already passed today"
    return None


_WEEKDAY_WORDS = {"mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
                  "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
                  "sat": 5, "saturday": 5, "sun": 6, "sunday": 6}
_MONTH_WORDS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def _next_occurrence(today: date, month: int, day: int) -> Optional[date]:
    """The next time this month and day comes round, today included."""
    try:
        this_year = date(today.year, month, day)
        return this_year if this_year >= today else date(today.year + 1, month, day)
    except ValueError:
        return None


def dates_the_wording_allows(say: str, now: datetime) -> Optional[set[date]]:
    """The dates a message's own date word allows, or None if it has no single clear one.

    This does not parse the request. It reads one English date word ("tomorrow", "friday",
    "the 3rd", "nov 24", "12/1") so a proposed date can be checked against it. A message it
    cannot read is simply not checked.
    """
    text, today = say.lower(), now.date()
    if found := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        try:
            return {date(int(found[1]), int(found[2]), int(found[3]))}
        except ValueError:
            return None
    if "day after tomorrow" in text:
        return {today + timedelta(days=2)}
    if re.search(r"\b(tomorrow|tmrw|tmr)\b", text):
        return {today + timedelta(days=1)}
    if re.search(r"\b(today|tonight|this (morning|afternoon|evening))\b", text):
        return {today}
    if found := re.search(r"\b(\d{1,2})/(\d{1,2})\b", text):
        named = _next_occurrence(today, int(found[1]), int(found[2])) if int(found[1]) <= 12 else None
        return {named} if named else None
    if found := re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? (\d{1,2})(?:st|nd|rd|th)?\b", text):
        named = _next_occurrence(today, _MONTH_WORDS[found[1]], int(found[2]))
        return {named} if named else None
    if found := re.search(r"\bthe (\d{1,2})(st|nd|rd|th)\b", text):
        this_month = _next_occurrence(today, today.month, int(found[1]))
        if this_month and this_month.year == today.year:
            return {this_month}
        next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        try:
            return {next_month.replace(day=int(found[1]))}
        except ValueError:
            return None
    weekdays = {_WEEKDAY_WORDS[word] for word in re.findall(r"\b(" + "|".join(_WEEKDAY_WORDS) + r")\b", text)}
    if len(weekdays) == 1 and not re.search(r"\b(every|each)\b", text):
        ahead = (weekdays.pop() - today.weekday()) % 7
        nearest = today + timedelta(days=ahead)
        # "next friday" is read both ways, and so is the weekday that is today.
        return {nearest, nearest + timedelta(days=7)} if ahead == 0 or re.search(r"\bnext\b", text) else {nearest}
    return None


def date_contradiction(say: str, name: str, args: dict[str, Any], now: datetime) -> Optional[str]:
    """Why a proposed date disagrees with the message's own date word, or None if it does not.

    A gate check: a proposal that fails it is not stored; it is retried or turned into a question.
    Questions, repeating events and messages with no readable date word are left alone.
    """
    if name == "ask_user" or args.get("recurrence"):
        return None
    allowed = dates_the_wording_allows(say, now)
    if allowed is None:
        return None
    wanted = " or ".join(sorted(day.isoformat() for day in allowed))
    proposed = args.get("date") if name == "create_event" else args.get("due_date")
    if not proposed:
        return f"no date was set, but the message points to {wanted}"
    if date.fromisoformat(proposed) not in allowed:
        return f"date {proposed} does not match the message, which points to {wanted}"
    return None


def calendar_hint(now: datetime, days: int = 14) -> str:
    """The next `days` dates with their weekdays, so 'next Tuesday' is a lookup, not arithmetic."""
    lines = []
    for offset in range(days):
        day = now.date() + timedelta(days=offset)
        mark = {0: " (today)", 1: " (tomorrow)"}.get(offset, "")
        lines.append(f"{day:%a} {day.isoformat()}{mark}")
    return "\n".join(lines)


_RULES = """\
Turn the user's message into exactly one kind of proposal by calling a tool.
- Use create_event for anything at a set time, anything that repeats, and any block of time held for
  something. Use add_todo only for a one-off thing to get done, with or without a deadline.
- Dates are YYYY-MM-DD and times are 24-hour HH:MM. Work them out from the current date above.
- A time that has already passed today means the next time that day comes around.
- Read the time of day from context ("dinner at 7" is 19:00, "tonight at 8" is 20:00). For a bare hour
  with nothing to settle morning or evening ("meeting at 7"), pick the likelier reading; the user is
  shown the time and can correct it.
- If an event has no date at all, or no time at all, call ask_user. Never invent one.
- Only set an end time, a due date or a repeat when the user asked for it.
- The title is the user's own words for the thing, without date, time or repeat words."""


def system_prompt(arm: str, now: datetime) -> str:
    """What the model is told. The arms share the rules and differ in how the date is presented."""
    zone = getattr(now.tzinfo, "key", None) or now.strftime("UTC%z")
    if arm == "date_only":
        clock = f"Current date and time: {now:%Y-%m-%d %H:%M} ({zone})."
    elif arm in ("calendar", "calendar_single"):
        clock = (f"Current date and time: {now:%A} {now:%Y-%m-%d %H:%M} ({zone}).\n"
                 f"The next two weeks:\n{calendar_hint(now)}")
    else:
        raise ValueError(f"unknown arm {arm!r}; use one of {ARMS}")
    return f"{clock}\n\n{_RULES}"
