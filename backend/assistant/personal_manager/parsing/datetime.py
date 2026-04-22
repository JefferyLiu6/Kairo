"""Date, time, and duration parsing helpers."""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

from .text import _norm

_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_TIME_RE = re.compile(r"(?<![\d-])(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b(?!-)", re.IGNORECASE)

_SHORT_EVENT_KEYWORDS = frozenset({
    "standup", "stand-up", "stand up", "scrum", "sync", "check-in", "checkin",
    "coffee", "coffee chat", "quick call", "quick sync", "debrief", "touchbase",
    "touch base", "1:1", "one-on-one",
})
_LONG_EVENT_KEYWORDS = frozenset({
    "deep work", "focus", "focus time", "focus block",
    "gym", "workout", "run", "jog", "yoga", "crossfit", "training",
    "basketball", "tennis", "soccer", "football",
    "seminar", "workshop", "conference", "class", "lecture",
})


def _parse_date(text: str) -> Optional[str]:
    lower = _norm(text)
    match = _DATE_RE.search(text)
    if match:
        return match.group(1)
    today = date.today()
    if "today" in lower or re.search(r"\btdy\b", lower):
        return today.isoformat()
    if "tomorrow" in lower or re.search(r"\b(tmr|tmrw)\b", lower):
        return (today + timedelta(days=1)).isoformat()
    weekdays = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    for name, idx in weekdays.items():
        if name in lower:
            days = (idx - today.weekday()) % 7
            if days == 0:
                days = 7
            return (today + timedelta(days=days)).isoformat()
    return None


def _parse_destination_date(text: str) -> Optional[str]:
    date_words = (
        r"20\d{2}-\d{2}-\d{2}|today|tdy|tomorrow|tmr|tmrw|"
        r"(?:next\s+|this\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
        r"next\s+week"
    )
    match = re.search(rf"\b(?:to|for|on)\s+({date_words})\b", text, flags=re.IGNORECASE)
    if not match:
        return None
    captured = match.group(1).strip().lower()
    if re.fullmatch(r"next\s+week", captured):
        today = date.today()
        days_to_next_monday = (0 - today.weekday()) % 7 or 7
        return (today + timedelta(days=days_to_next_monday)).isoformat()
    return _parse_date(captured)


def _parse_time(text: str) -> Optional[str]:
    lower = _norm(text)
    explicit_times = _parse_times(text)
    if explicit_times:
        return explicit_times[0]
    if re.search(r"\bfirst\s+thing\b", lower):
        return "08:00"
    if re.search(r"\b(end[\s-]of[\s-]day|eod)\b", lower):
        return "17:00"
    if "after work" in lower:
        return "17:00"
    if re.search(r"\bmid[\s-]?morning\b", lower):
        return "10:00"
    if "before lunch" in lower:
        return "11:30"
    if "after lunch" in lower:
        return "13:00"
    if re.search(r"\bmid[\s-]?afternoon\b", lower):
        return "15:00"
    if re.search(r"\b(late\s+afternoon|late\s+pm)\b", lower):
        return "16:30"
    if re.search(r"\bearly\s+morning\b", lower):
        return "07:00"
    if re.search(r"\blate\s+morning\b", lower):
        return "11:00"
    if "lunch" in lower:
        return "12:00"
    if "morning" in lower:
        return "09:00"
    if "noon" in lower:
        return "12:00"
    if "afternoon" in lower:
        return "14:00"
    if "evening" in lower:
        return "18:00"
    if re.search(r"\b(tonight|night)\b", lower):
        return "20:00"
    return None


def _parse_times(text: str) -> list[str]:
    times: list[str] = []
    for match in _TIME_RE.finditer(text):
        raw = match.group(0).lower()
        if raw.isdigit() and len(raw) == 4:
            continue
        hour = int(match.group(1))
        minute = int(match.group(2) or "0")
        meridiem = (match.group(3) or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            times.append(f"{hour:02d}:{minute:02d}")
    return times


def _parse_ordinal(text: str) -> Optional[int]:
    lower = _norm(text)
    words = {
        "first": 1,
        "1st": 1,
        "second": 2,
        "2nd": 2,
        "third": 3,
        "3rd": 3,
        "fourth": 4,
        "4th": 4,
        "fifth": 5,
        "5th": 5,
    }
    for word, value in words.items():
        if re.search(rf"\b{re.escape(word)}\b", lower):
            return value
    return None


def _smart_end_time(start: Optional[str], title: str = "") -> str:
    if not start:
        return ""
    lower = (title or "").lower()
    if any(kw in lower for kw in _SHORT_EVENT_KEYWORDS):
        minutes = 30
    elif any(kw in lower for kw in _LONG_EVENT_KEYWORDS):
        minutes = 90
    else:
        minutes = 60
    hour, minute = [int(x) for x in start.split(":", 1)]
    total = hour * 60 + minute + minutes
    return f"{(total // 60) % 24:02d}:{total % 60:02d}"


def _default_end_time(start: Optional[str]) -> str:
    return _smart_end_time(start, "")


def _has_explicit_time_signal(text: str) -> bool:
    lower = _norm(text)
    if any(
        phrase in lower
        for phrase in (
            "after work", "after lunch", "before lunch", "at lunch",
            "morning", "noon", "afternoon", "evening", "tonight", "night",
            "first thing", "end of day", "eod",
            "mid-morning", "mid morning", "mid-afternoon", "mid afternoon",
            "early morning", "late morning", "late afternoon",
        )
    ):
        return True
    return bool(
        re.search(r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b", text, flags=re.IGNORECASE)
        or re.search(r"\b(at|around|by|from)\s+\d{1,2}(:\d{2})?\b", text, flags=re.IGNORECASE)
    )
