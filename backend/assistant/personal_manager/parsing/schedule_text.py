"""Schedule-specific text classification helpers."""
from __future__ import annotations

import re
import uuid
from typing import Any, Optional

from ..calendar.recurrence import _extract_recurring_weekdays, _parse_recurrence_start_date, _parse_until_date
from ..persistence.store import _OLD_WD_TO_BY_DAY
from .cleanup import _clean_schedule_title
from .datetime import _date_mention_count, _has_explicit_time_signal, _parse_date, _parse_time, _smart_end_time
from .text import _norm


def _extract_schedule_category(text: str) -> str:
    lower = _norm(text)
    for category in ("meeting", "appointment", "event"):
        if category in lower:
            return category
    return ""


def _looks_like_schedule_creation_command(text: str) -> bool:
    lower = _norm(text)
    if not (_parse_date(text) and _has_explicit_time_signal(text)):
        return False
    return bool(re.search(r"\b(add|create|book|put|schedule|calendar|block)\b", lower))


def _looks_like_bare_schedule_slot(text: str) -> bool:
    lower = _norm(text)
    if any(word in lower for word in ("?", "what ", "when ", "show ", "list ")):
        return False
    if _has_non_schedule_plan_marker(text):
        return False
    return bool(_parse_date(text) and _has_explicit_time_signal(text))


def _looks_like_scheduled_life_event(text: str) -> bool:
    lower = _norm(text)
    if not _has_explicit_time_signal(text):
        return False
    if re.search(r"\b(what|show|list|find|check|when|where|who)\b", lower):
        return False
    return bool(
        re.search(r"\b(i\s+)?(need|have|want|wanna|got)\s+to\b", lower)
        or re.search(r"\b(i\s+)?wanna\b", lower)
        or re.search(r"\b(i\s+)?must\b", lower)
        or re.search(r"\bi'?m\s+(?:going\s+to\s+)?(having|eating|going|meeting|working|doing|running|heading)\b", lower)
        or re.search(r"\bi'?m\s+gonna\b", lower)
    )


def _looks_like_ambiguous_schedulable_routine(text: str) -> bool:
    lower = _norm(text)
    if any(word in lower for word in ("?", "what ", "when ", "show ", "list ", "find ", "check ")):
        return False
    if _has_non_schedule_plan_marker(text):
        return False
    if not re.search(
        r"\b(i\s+)?(need|have|want|wanna|got)\s+to\b|\b(i\s+)?wanna\b|\bi'?m\s+gonna\b",
        lower,
    ):
        return False
    return bool(
        re.search(r"\b(eat|have|grab|get)\s+(breakfast|lunch|dinner|brunch)\b", lower)
        or re.search(r"\b(workout|exercise|go\s+to\s+(the\s+)?gym|gym|run|jog|walk|yoga)\b", lower)
    )


def _looks_like_bulk_schedule_delete(text: str) -> bool:
    lower = _norm(text)
    return bool(
        re.search(
            r"\ball\b|\bevery\s+day\b|\beveryday\b|\bdaily\b|"
            r"\bevery\s+weekday\b|\bweekdays?\b|"
            r"\bevery\s+(mon|monday|tue|tuesday|wed|wednesday|thu|thursday|"
            r"fri|friday|sat|saturday|sun|sunday)\b",
            lower,
        )
    )


def _has_non_schedule_plan_marker(message: str) -> bool:
    lower = _norm(message)
    markers = (
        "remind me",
        "journal",
        "log ",
        "remember ",
        "task",
        "todo",
        "habit",
        "search ",
        "delete ",
        "remove ",
        "cancel ",
        "complete ",
        "mark ",
        "finish ",
        "done ",
        "move ",
        "reschedule ",
        "update ",
        "change ",
    )
    return any(marker in lower for marker in markers)


def _parse_schedule_entries(text: str) -> list[dict[str, Any]]:
    # Recurring pattern: emit a single series entry with a RecurrenceRule.
    recurring = _extract_recurring_weekdays(text)
    if recurring:
        start = _parse_time(text)
        if not start:
            return []
        title = _clean_schedule_title(text)
        start_date = _parse_recurrence_start_date(text)
        until = _parse_until_date(text)
        is_daily = (
            set(recurring) == {0, 1, 2, 3, 4, 5, 6}
            and re.search(
                r"\bevery\s+day\b|\beveryday\b|\bdaily\b"
                r"|\bevery\s+morning\b|\beach\s+morning\b"
                r"|\bevery\s+evening\b|\beach\s+evening\b"
                r"|\bevery\s+night\b|\beach\s+night\b",
                text,
                flags=re.IGNORECASE,
            )
        )
        by_day = [] if is_daily else [_OLD_WD_TO_BY_DAY[wd] for wd in recurring if wd in _OLD_WD_TO_BY_DAY]
        return [{
            "title": title,
            "date": start_date or "",
            "series_id": uuid.uuid4().hex[:8],
            "recurrence": {"freq": "daily" if is_daily else "weekly", "by_day": by_day, "interval": 1, "until": until},
            "start": start,
            "end": _smart_end_time(start, title),
            "notes": "",
        }]

    entries: list[dict[str, Any]] = []
    default_date = _parse_date(text) if _date_mention_count(text) <= 1 else None
    last_date: Optional[str] = None

    for clause in _split_schedule_clauses(text):
        event_date = _parse_date(clause) or last_date or default_date
        start = _parse_time(clause)
        if not event_date or not start:
            continue
        title = _clean_schedule_title(clause)
        entries.append(
            {
                "title": title,
                "date": event_date,
                "start": start,
                "end": _smart_end_time(start, title),
                "notes": "",
            }
        )
        last_date = event_date
    return entries


def _split_schedule_clauses(text: str) -> list[str]:
    cleaned = " ".join(text.strip().split())
    chunks = [part.strip() for part in re.split(r"[,;\n]+", cleaned) if part.strip()]
    parts: list[str] = []
    split_on_and_before_date_or_time = (
        r"\s+and\s+(?="
        r"20\d{2}-\d{2}-\d{2}|today|tomorrow|tmr|tmrw|next\s+|this\s+|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b"
        r")"
    )
    for chunk in chunks:
        parts.extend(part.strip() for part in re.split(split_on_and_before_date_or_time, chunk, flags=re.IGNORECASE) if part.strip())
    return parts or [cleaned]
