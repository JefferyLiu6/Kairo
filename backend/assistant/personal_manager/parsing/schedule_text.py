"""Schedule-specific text classification helpers."""
from __future__ import annotations

import re

from .datetime import _has_explicit_time_signal, _parse_date
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
