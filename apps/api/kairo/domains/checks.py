"""
Checks a proposed date must pass before anything is stored, and the calendar the model is shown.

None of this parses a request. `dates_the_wording_allows` reads one English date
word in the user's message so a date the model proposed can be compared with it.
A message it cannot read is simply not checked. Measured in docs/MODEL_DATE_TEST.md:
on gpt-4o-mini these checks stopped every wrong date and no correct one.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

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


_CLOCK = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?!\w)", re.I)


def times_the_wording_gives(say: str) -> set[str]:
    """Clock times the message states outright, as HH:MM: "4pm", "11:30 am", "noon", "midnight".

    A bare hour ("at 7") is not included: it does not say which half of the day.
    """
    times = set()
    for hour, minute, half in _CLOCK.findall(say):
        hour = int(hour) % 12 + (12 if half.lower().startswith("p") else 0)
        times.add(f"{hour:02d}:{minute or '00'}")
    text = say.lower()
    if re.search(r"\bnoon\b", text):
        times.add("12:00")
    if re.search(r"\bmidnight\b", text):
        times.add("00:00")
    return times


def time_word_problem(say: str, proposed: Optional[str]) -> Optional[str]:
    """Why a proposed time (HH:MM, or None) disagrees with a clock time the message states, if it does."""
    stated = times_the_wording_gives(say)
    if not stated or proposed in stated:
        return None
    wanted = " or ".join(sorted(stated))
    return (f"time {proposed} does not match the message, which says {wanted}" if proposed
            else f"no time was set, but the message says {wanted}")


_REPEATS = re.compile(
    r"\b(every|each)\s+(other\s+)?(day|morning|afternoon|evening|night|week|weekday|weekend|month|year|"
    r"mon\w*|tue\w*|wed\w*|thu\w*|fri\w*|sat\w*|sun\w*)\b|\b(daily|nightly|weekly|monthly)\b", re.I)


def asks_for_a_repeat(say: str) -> bool:
    """Whether the message's own wording asks for something that repeats ("every morning", "weekly")."""
    return bool(_REPEATS.search(say))


def date_word_problem(say: str, proposed: Optional[str], now: datetime, *, must_have_date: bool = True) -> Optional[str]:
    """Why a proposed date (YYYY-MM-DD, or None) disagrees with the message's own date word, if it does.

    With `must_have_date` off, a missing date is accepted; use that when several items share one
    message and the date word may belong to only one of them.
    """
    allowed = dates_the_wording_allows(say, now)
    if allowed is None:
        return None
    wanted = " or ".join(sorted(day.isoformat() for day in allowed))
    if not proposed:
        return f"no date was set, but the message points to {wanted}" if must_have_date else None
    if date.fromisoformat(proposed[:10]) not in allowed:
        return f"date {proposed[:10]} does not match the message, which points to {wanted}"
    return None


def past_problem(proposed: Optional[str], now: datetime, *, start: Optional[str] = None) -> Optional[str]:
    """Why a date (and, for a one-off event today, a start time) is already in the past, if it is."""
    if not proposed:
        return None
    today = now.date().isoformat()
    if proposed[:10] < today:
        return f"date {proposed[:10]} is in the past"
    if start and proposed[:10] == today and start < f"{now:%H:%M}":
        return f"start {start} has already passed today"
    return None


def calendar_hint(now: datetime, days: int = 14) -> str:
    """The next `days` dates with their weekdays, so 'next Tuesday' is a lookup, not arithmetic."""
    lines = []
    for offset in range(days):
        day = now.date() + timedelta(days=offset)
        mark = {0: " (today)", 1: " (tomorrow)"}.get(offset, "")
        lines.append(f"{day:%a} {day.isoformat()}{mark}")
    return "\n".join(lines)
