"""
The candidate tool contract for date-bearing proposals, and what the model is told.

These are the model-facing argument shapes Kairo 2.0 would use for
`create_event` and `add_todo`: every field typed, dates as YYYY-MM-DD, times as
24-hour HH:MM, weekdays as an enum, and an explicit `ask_user` so "I am not
sure" is something the model can say. They live here until the tool registry
exists (docs/ideas/kairo-2.0-agent-architecture.md, section 8).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from kairo.domains.checks import calendar_hint, date_word_problem, dates_the_wording_allows, past_problem

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
        one_off_event = name == "create_event" and not args.get("recurrence")
        return past_problem(args.get("date") or args.get("due_date"), now,
                            start=args.get("start") if one_off_event else None)
    return None


def date_contradiction(say: str, name: str, args: dict[str, Any], now: datetime) -> Optional[str]:
    """Why a proposed date disagrees with the message's own date word, or None if it does not.

    A gate check: a proposal that fails it is not stored; it is retried or turned into a question.
    Questions, repeating events and messages with no readable date word are left alone.
    """
    if name == "ask_user" or args.get("recurrence"):
        return None
    return date_word_problem(say, args.get("date") if name == "create_event" else args.get("due_date"), now)


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


__all__ = ["ARMS", "TOOLS", "calendar_hint", "date_contradiction", "dates_the_wording_allows", "openai_tools",
           "single_call", "system_prompt", "validate_call"]
