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
    if re.search(r"\byesterday\b", text):
        return {today - timedelta(days=1)}
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
        # The weekday that is today is read both ways. So is "next friday" while that Friday is
        # still in this week; once the nearest one is already in next week, "next" means that one.
        this_week = nearest.isocalendar()[:2] == today.isocalendar()[:2]
        both = ahead == 0 or (this_week and bool(re.search(r"\bnext\b", text)))
        return {nearest, nearest + timedelta(days=7)} if both else {nearest}
    return None


_CLOCK = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?!\w)", re.I)


_HALF = r"(a\.?m\.?|p\.?m\.?)"
_RANGE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*" + _HALF + r"?\s*(?:-|–|—|to|until|till)\s*(\d{1,2})(?::(\d{2}))?\s*"
                    + _HALF + r"(?!\w)", re.I)


def _minutes(hour: str, minute: str, afternoon: bool) -> int:
    return (int(hour) % 12 + (12 if afternoon else 0)) * 60 + int(minute or 0)


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def range_the_wording_gives(say: str) -> Optional[tuple[str, str]]:
    """The (start, end) of a time range the message states, as HH:MM: "2-3pm", "7pm to 7:30am".

    A start with no am or pm takes the half of the day that puts it before the end ("11-1pm"
    starts at 11:00). None if the message states no range.
    """
    for hour, minute, half, end_hour, end_minute, end_half in _RANGE.findall(say):
        if not 1 <= int(hour) <= 12 or not 1 <= int(end_hour) <= 12:
            continue
        afternoon = end_half.lower().startswith("p")
        end = _minutes(end_hour, end_minute, afternoon)
        if half:
            start = _minutes(hour, minute, half.lower().startswith("p"))
        else:
            start = _minutes(hour, minute, afternoon)
            if start > end:
                start = _minutes(hour, minute, not afternoon)
        return _clock(start), _clock(end)
    return None


def times_the_wording_gives(say: str) -> set[str]:
    """Clock times the message states outright, as HH:MM: "4pm", "11:30 am", "2-3pm", "noon", "midnight".

    A bare hour ("at 7") is not included: it does not say which half of the day.
    """
    times = set()
    for hour, minute, half in _CLOCK.findall(say):
        hour = int(hour) % 12 + (12 if half.lower().startswith("p") else 0)
        times.add(f"{hour:02d}:{minute or '00'}")
    stated = range_the_wording_gives(say)
    if stated:
        times.add(stated[0])
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
    r"mon\w*|tue\w*|wed\w*|thu\w*|fri\w*|sat\w*|sun\w*)\b|\b(daily|nightly|weekly|monthly)\b"
    r"|\b(mon|tues|wednes|thurs|fri|satur|sun|week)days\b|\bweekends\b", re.I)


def asks_for_a_repeat(say: str) -> bool:
    """Whether the message's own wording asks for something that repeats ("every morning", "weekly", "on fridays")."""
    return bool(_REPEATS.search(say))


_REMEMBER = re.compile(r"\b(remember|keep in mind|note that|for future reference|don['’]?t forget)\b", re.I)


def asks_to_remember(say: str) -> bool:
    """Whether the message's own wording asks for something to be remembered ("remember that …", "note that …")."""
    return bool(_REMEMBER.search(say))


_DAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_NAMED_DAYS = re.compile(r"\b(" + "|".join(_WEEKDAY_WORDS) + r")s?\b")
_EVERY_DAY = re.compile(r"\b(every|each)\s+(day|morning|afternoon|evening|night)\b|\b(daily|nightly)\b")


def repeat_problem(say: str, recurrence: Optional[dict]) -> Optional[str]:
    """Why a proposed repeat ({"freq", "interval", "by_day"}, or None) disagrees with the message, if it does.

    Only what the wording clearly says is checked: that it repeats at all, which weekdays it names,
    "every day" and "every other". A message with no repeat wording is not checked.
    """
    if not asks_for_a_repeat(say):
        # Days are named but no repeat is asked for ("on call sat sun"): a repeat would be invented.
        # A message that names no day this can read is not checked.
        if recurrence and _NAMED_DAYS.search(say.lower()):
            return ("the message names days but does not ask for a repeat; make one event for each day named, "
                    "with recurrence null")
        return None
    if not recurrence:
        return "the message asks for something that repeats, but no repeat was set"
    text = say.lower()
    freq, days = recurrence.get("freq"), set(recurrence.get("by_day") or [])
    named = {_DAY_CODES[_WEEKDAY_WORDS[word]] for word in _NAMED_DAYS.findall(text)}
    if re.search(r"\bweekdays?\b", text):
        named |= set(_DAY_CODES[:5])
    if named and (freq != "weekly" or days != named):
        return (f"the repeat is {freq} on {sorted(days) or 'no days'}, but the message names "
                f"{sorted(named)}; use weekly with exactly those days")
    if not named and _EVERY_DAY.search(text) and not (freq == "daily" or days == set(_DAY_CODES)):
        return f"the message asks for every day, but the repeat is {freq} on {sorted(days)}"
    if re.search(r"\bevery other\b", text) and (recurrence.get("interval") or 1) != 2:
        return "the message says every other, so the interval must be 2"
    return None


def own_words_problem(text: str, said: tuple[str, ...], what: str) -> Optional[str]:
    """Why `text` is not the user's own words, if most of it is not in what they said.

    Something kept on the user's behalf (a journal entry, a fact to remember) is copied, not
    reworded. A very short message is not checked: it may point at something said earlier.
    """
    spoken = set(re.findall(r"\w+", " ".join(said).casefold()))
    words = re.findall(r"\w+", text.casefold())
    if len(spoken) < 6 or not words:
        return None
    kept = sum(word in spoken for word in words) / len(words)
    return None if kept >= 0.8 else f"{what} is kept in the user's own words; copy them from the message without rewording"


_FILLER = frozenset({"the", "and", "with", "for", "from", "about", "this", "that", "your", "todo", "task", "event"})


def named_in(title: str, said: tuple[str, ...]) -> bool:
    """Whether any word of `title` appears in what the user said.

    Words are looked for as substrings, so it also works for languages written without spaces.
    """
    spoken = " ".join(said).casefold()
    words = [word for word in re.findall(r"\w+", title.casefold())
             if word not in _FILLER and (len(word) >= 3 or not word.isascii())]
    return any(word in spoken for word in words)


# Words that point at a stored item without describing one. A message made only of these ("done
# with it", "the second one", "delete them all", "cancel everything on friday") leaves the choice
# to the conversation.
_POINTING = frozenset("""
    done did finish finished finishing complete completed completing mark marked check checked tick ticked
    delete deleted remove removed cancel cancelled canceled clear drop scrap skip move push todo todos task tasks
    item items list event events meeting meetings calendar schedule that this these those them they first second
    third fourth last next other another both everything every each please just already also with from have has
    been was were will would could can you the one ones all and off out thing today tonight tomorrow tmrw week
    weekend morning afternoon evening night
""".split()) | frozenset(_WEEKDAY_WORDS) | frozenset(_MONTH_WORDS)


def not_what_was_said(chosen: list[str], items: dict[str, str], said: tuple[str, ...]) -> Optional[str]:
    """Why the items a model chose by id are not the ones the user's words point to, if so.

    `items` is every item it could have chosen, id -> title. If the message names some of them, the
    choice has to be among those. If it names none but uses a word that is on no item ("the funding
    one" when nothing about funding is there, "delete the standup" with no standup), nothing is
    picked for it at all: a made-up match is worse than saying it is not there.
    """
    named = {item_id for item_id, title in items.items() if named_in(title, said)}
    off = next((item_id for item_id in chosen if named and item_id not in named), None)
    if off:
        return (f"the message does not name '{items[off]}'; what it names is: "
                + ", ".join(f"[{item_id}] {items[item_id]}" for item_id in sorted(named)))
    if not named:
        words = [word for text in said for word in re.findall(r"[^\W\d_]+", text.casefold())
                 if len(word) >= 4 and word.isascii() and word not in _POINTING]
        if words:
            return (f"nothing here mentions “{words[0]}”, so nothing is picked for it: it may be gone already, "
                    "or be something else. Say so, or hand it off")
    return None


def say_dates(days: set[date]) -> str:
    """Dates with their weekdays, for telling a model which one was meant: '2026-11-13 (Fri)'."""
    return " or ".join(f"{day.isoformat()} ({day:%a})" for day in sorted(days))


def date_word_problem(say: str, proposed: Optional[str], now: datetime, *, must_have_date: bool = True) -> Optional[str]:
    """Why a proposed date (YYYY-MM-DD, or None) disagrees with the message's own date word, if it does.

    With `must_have_date` off, a missing date is accepted; use that when several items share one
    message and the date word may belong to only one of them.
    """
    allowed = dates_the_wording_allows(say, now)
    if allowed is None:
        return None
    wanted = say_dates(allowed)
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
