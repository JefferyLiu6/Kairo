"""User-facing reply formatting helpers for the personal-manager workflow."""
from __future__ import annotations

import re
from datetime import date, timedelta


_WEEKDAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
_BY_DAY_TO_SHORT = {"MO": "Mon", "TU": "Tue", "WE": "Wed", "TH": "Thu", "FR": "Fri", "SA": "Sat", "SU": "Sun"}


def _norm(text: str) -> str:
    return " ".join(text.lower().strip().split())


def _format_checkin_reply(raw: str) -> str:
    """Turn the raw habit_checkin store string into a friendly message."""
    streak_m = re.search(r"streak:\s*(\d+)\s*day", raw, re.IGNORECASE)
    streak = int(streak_m.group(1)) if streak_m else None
    name_m = re.search(r"checked in '(.+?)'", raw, re.IGNORECASE)
    name = name_m.group(1) if name_m else ""
    label = f"**{name}**" if name else "that"
    if streak is None:
        return f"Logged {label}."
    if streak == 1:
        return f"Logged {label}. Day one — good start."
    if streak < 4:
        return f"Logged {label}. {streak} days in a row."
    if streak < 14:
        return f"Logged {label}. {streak}-day streak — keep going."
    if streak < 30:
        return f"Logged {label}. {streak} days straight — solid."
    return f"Logged {label}. {streak}-day streak — that's serious consistency."


def _habit_list_is_empty(raw: str) -> bool:
    return raw.strip().lower() in {"", "habits: (none)", "habits: (empty)"}


def _empty_habit_suggestions_reply() -> str:
    return (
        "No habits tracked yet. Good starter options: Drink a glass of water, "
        "walk, read, stretch, or plan tomorrow. Say `add habit to drink water` "
        "to start one."
    )


def _format_date_natural(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        d = date.fromisoformat(date_str)
        today = date.today()
        if d == today:
            return "today"
        if d == today + timedelta(days=1):
            return "tomorrow"
        delta = (d - today).days
        if 1 < delta <= 6:
            return d.strftime("%A")
        return d.strftime("%A, %b %d")
    except (ValueError, TypeError):
        return date_str


def _format_time_natural(time_str: str) -> str:
    if not time_str:
        return ""
    try:
        h, m = map(int, time_str.split(":"))
        period = "AM" if h < 12 else "PM"
        display_h = h % 12 or 12
        return f"{display_h}:{m:02d} {period}" if m else f"{display_h} {period}"
    except (ValueError, TypeError):
        return time_str


def _format_recurrence_natural(recurrence: dict, start_date: str = "") -> str:
    by_day: list[str] = recurrence.get("by_day") or []
    freq = (recurrence.get("freq") or "weekly").lower()
    all_7 = set(by_day) == {"SU", "MO", "TU", "WE", "TH", "FR", "SA"}
    weekdays_only = set(by_day) == {"MO", "TU", "WE", "TH", "FR"}
    if freq == "daily" or all_7:
        label = "every day"
    elif weekdays_only:
        label = "every weekday"
    elif by_day:
        label = "every " + "/".join(_BY_DAY_TO_SHORT.get(d, d) for d in by_day)
    else:
        label = f"every {freq}"
    if start_date:
        label += f" from {start_date}"
    until = recurrence.get("until")
    if until:
        label += f" until {until}"
    return label


def _format_schedule_add_reply(entries: list[dict]) -> str:
    if not entries:
        return "Nothing to schedule."
    if len(entries) == 1:
        e = entries[0]
        title = e.get("title") or "Event"
        recurrence = e.get("recurrence")
        when = _format_recurrence_natural(recurrence, e.get("date") or "") if recurrence else _format_date_natural(e.get("date") or "")
        if not when and e.get("weekday") is not None:
            when = f"every {_WEEKDAY_NAMES[e['weekday']]}"
        at = _format_time_natural(e.get("start") or "")
        if when and at:
            return f"Done! Added '{title}' {when} at {at}."
        if when:
            return f"Done! Added '{title}' {when}."
        if at:
            return f"Done! Added '{title}' at {at}."
        return f"Done! Added '{title}'."
    lines = [f"Done! Added {len(entries)} events:"]
    for e in entries:
        title = e.get("title") or "Event"
        recurrence = e.get("recurrence")
        when = _format_recurrence_natural(recurrence, e.get("date") or "") if recurrence else _format_date_natural(e.get("date") or "")
        if not when and e.get("weekday") is not None:
            when = f"every {_WEEKDAY_NAMES[e['weekday']]}"
        at = _format_time_natural(e.get("start") or "")
        part = f"  • '{title}'"
        if when:
            part += f" {when}"
        if at:
            part += f" at {at}"
        lines.append(part)
    return "\n".join(lines)


def _format_schedule_remove_summary(titles: list[str]) -> str:
    if not titles:
        return "Remove schedule event"
    unique_titles = []
    seen = set()
    for title in titles:
        clean = str(title or "Untitled").strip() or "Untitled"
        key = _norm(clean)
        if key in seen:
            continue
        seen.add(key)
        unique_titles.append(clean)
    if len(titles) == 1:
        return f"Remove schedule event: {unique_titles[0]}"
    if len(unique_titles) == 1:
        return f"Remove {len(titles)} schedule events: {unique_titles[0]}"
    return f"Remove {len(titles)} schedule events"


def _format_event_duration(start: str, end: str) -> str:
    try:
        h1, m1 = map(int, start.split(":"))
        h2, m2 = map(int, end.split(":"))
        mins = (h2 * 60 + m2) - (h1 * 60 + m1)
        if 0 < mins < 1440:
            return f" ({mins // 60}h{mins % 60:02d}m)" if mins % 60 else f" ({mins // 60}h)"
    except ValueError:
        pass
    return ""
