"""Calendar recurrence parsing helpers."""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

from ..parsing.datetime import _parse_date

_RECURRING_WEEKDAY_INDEX: dict[str, int] = {
    "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5,
    "saturday": 6, "sunday": 0,
    "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 0,
}


def _extract_recurring_weekdays(text: str) -> list[int]:
    """Return weekday indices (0=Sun through 6=Sat) for recurring patterns, or [] if none."""
    lower = text.lower()
    if re.search(r"\bevery\s+weekday\b|\bweekdays?\b", lower):
        return [1, 2, 3, 4, 5]
    if re.search(
        r"\bevery\s+day\b|\beveryday\b|\bdaily\b"
        r"|\bevery\s+morning\b|\beach\s+morning\b"
        r"|\bevery\s+evening\b|\beach\s+evening\b"
        r"|\bevery\s+night\b|\beach\s+night\b",
        lower,
    ):
        return [0, 1, 2, 3, 4, 5, 6]
    if re.search(r"\bweekend\b", lower):
        return [0, 6]
    found: list[int] = []
    seen: set[int] = set()
    for name, idx in sorted(_RECURRING_WEEKDAY_INDEX.items(), key=lambda x: -len(x[0])):
        if idx not in seen and re.search(rf"\bevery\s+{name}\b", lower):
            found.append(idx)
            seen.add(idx)
    if found:
        for name, idx in sorted(_RECURRING_WEEKDAY_INDEX.items(), key=lambda x: -len(x[0])):
            if idx not in seen and re.search(rf"(?:,|and)\s*{name}\b", lower):
                found.append(idx)
                seen.add(idx)
        return sorted(found)
    return []


def _parse_until_date(text: str) -> Optional[str]:
    """Parse 'until June', 'until end of June', 'until June 30', etc. -> YYYY-MM-DD."""
    lower = text.lower()
    months: dict[str, int] = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
        "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    today = date.today()
    match = re.search(r"\buntil\s+(\w+)\s+(\d{1,2})(?:,?\s*(\d{4}))?\b", lower)
    if match:
        month_name, day_str = match.group(1), match.group(2)
        year = int(match.group(3)) if match.group(3) else today.year
        month = months.get(month_name)
        if month:
            try:
                return date(year, month, int(day_str)).isoformat()
            except ValueError:
                pass
    if re.search(r"\buntil\s+end\s+of\s+(?:the\s+)?month\b|\bthis\s+month\b", lower):
        month_end = (
            date(today.year, today.month + 1, 1) - timedelta(days=1)
            if today.month < 12
            else date(today.year, 12, 31)
        )
        return month_end.isoformat()
    if re.search(r"\bnext\s+month\b", lower):
        next_month = today.month + 1 if today.month < 12 else 1
        year = today.year if today.month < 12 else today.year + 1
        last_day = (
            date(year, next_month + 1, 1) - timedelta(days=1)
            if next_month < 12
            else date(year, 12, 31)
        )
        return last_day.isoformat()
    match = re.search(r"\buntil\s+(?:end\s+of\s+)?(\w+)\b", lower)
    if match:
        month = months.get(match.group(1))
        if month:
            year = today.year if month >= today.month else today.year + 1
            last_day = (
                date(year, month + 1, 1) - timedelta(days=1)
                if month < 12
                else date(year, 12, 31)
            )
            return last_day.isoformat()
    return None


def _parse_recurrence_start_date(text: str) -> Optional[str]:
    """Parse recurrence range starts such as 'next month' -> first day of next month."""
    lower = text.lower()
    today = date.today()
    if re.search(r"\bthis\s+month\b", lower):
        return date(today.year, today.month, 1).isoformat()
    if re.search(r"\bnext\s+month\b", lower):
        month = today.month + 1 if today.month < 12 else 1
        year = today.year if today.month < 12 else today.year + 1
        return date(year, month, 1).isoformat()
    return None


def _parse_schedule_reference_range(text: str) -> tuple[Optional[str], Optional[str]]:
    start = _parse_recurrence_start_date(text)
    end = _parse_until_date(text)
    return start, end


def _parse_next_week_date(text: str) -> Optional[str]:
    """Return the date of the same weekday next week, or Monday next week for 'next week'."""
    lower = text.lower()
    if re.search(r"\bnext\s+week\b", lower):
        today = date.today()
        days_to_monday = (7 - today.weekday()) % 7
        if days_to_monday == 0:
            days_to_monday = 7
        return (today + timedelta(days=days_to_monday)).isoformat()
    return _parse_date(text)


def _parse_next_weekday_ref(text: str) -> Optional[str]:
    """Parse 'this Friday', 'next Monday', "this week's Thursday", etc. -> YYYY-MM-DD."""
    lower = text.lower()
    today = date.today()
    weekday_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
        "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    }
    for name, py_wd in sorted(weekday_map.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b(?:this|next)\s+{name}\b", lower):
            days = (py_wd - today.weekday()) % 7
            if days == 0:
                days = 7
            return (today + timedelta(days=days)).isoformat()
    return _parse_date(text)


__all__ = [
    "_extract_recurring_weekdays",
    "_parse_next_week_date",
    "_parse_next_weekday_ref",
    "_parse_recurrence_start_date",
    "_parse_schedule_reference_range",
    "_parse_until_date",
]
