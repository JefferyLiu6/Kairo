"""Deterministic parsing and text cleanup helpers for the PM workflow."""
from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from typing import Any, Optional

from ..persistence.store import _OLD_WD_TO_BY_DAY


_ID_RE = re.compile(r"\b([a-f0-9]{6,12})\b", re.IGNORECASE)
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

_RECURRING_WEEKDAY_INDEX: dict[str, int] = {
    "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5,
    "saturday": 6, "sunday": 0,
    "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 0,
}


def _norm(text: str) -> str:
    return " ".join(text.lower().strip().split())


def _parse_date(text: str) -> Optional[str]:
    lower = _norm(text)
    match = _DATE_RE.search(text)
    if match:
        return match.group(1)
    today = date.today()
    if "today" in lower:
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
        r"20\d{2}-\d{2}-\d{2}|today|tomorrow|tmr|tmrw|"
        r"(?:next\s+|this\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    )
    match = re.search(rf"\b(?:to|for|on)\s+({date_words})\b", text, flags=re.IGNORECASE)
    return _parse_date(match.group(1)) if match else None


def _parse_time(text: str) -> Optional[str]:
    lower = _norm(text)
    # Explicit digit-based times always win over keyword shortcuts.
    explicit_times = _parse_times(text)
    if explicit_times:
        return explicit_times[0]
    # Fall back to semantic keywords when no explicit time is present.
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


def _extract_schedule_category(text: str) -> str:
    lower = _norm(text)
    for category in ("meeting", "appointment", "event"):
        if category in lower:
            return category
    return ""


def _smart_end_time(start: Optional[str], title: str = "") -> str:
    """Return end time with event-type-aware duration."""
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


def _extract_id(text: str) -> str:
    match = _ID_RE.search(text)
    return match.group(1) if match else ""


def _strip_leading_phrases(text: str, words: list[str]) -> str:
    cleaned = text.strip()
    for word in words:
        cleaned = re.sub(rf"^\s*{re.escape(word)}( that| entry| this)?\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(" .")


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
    # "what/show/list/find" queries should not be treated as creation
    if re.search(r"\b(what|show|list|find|check|when|where|who)\b", lower):
        return False
    return bool(
        re.search(r"\b(i\s+)?(need|have|want|wanna|got)\s+to\b", lower)
        or re.search(r"\b(i\s+)?wanna\b", lower)
        or re.search(r"\b(i\s+)?must\b", lower)
        or re.search(r"\bi'?m\s+(?:going\s+to\s+)?(having|eating|going|meeting|working|doing|running|heading)\b", lower)
        or re.search(r"\bi'?m\s+gonna\b", lower)
    )


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


def _extract_recurring_weekdays(text: str) -> list[int]:
    """Return weekday indices (0=Sun … 6=Sat) for recurring patterns, or [] if none."""
    lower = text.lower()
    if re.search(r"\bevery\s+weekday\b|\bweekdays?\b", lower):
        return [1, 2, 3, 4, 5]
    if re.search(r"\bevery\s+day\b|\beveryday\b|\bdaily\b", lower):
        return [0, 1, 2, 3, 4, 5, 6]
    if re.search(r"\bweekend\b", lower):
        return [0, 6]
    # Multi-day: "every Monday and Wednesday", "every Mon, Wed, Fri"
    found: list[int] = []
    seen: set[int] = set()
    # Sort by name length descending to match "tuesday" before "tue"
    for name, idx in sorted(_RECURRING_WEEKDAY_INDEX.items(), key=lambda x: -len(x[0])):
        if idx not in seen and re.search(rf"\bevery\s+{name}\b", lower):
            found.append(idx)
            seen.add(idx)
    if found:
        # Also pick up additional days joined by "and" or ","
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
    # "until June 30" or "until June 30, 2026"
    m = re.search(r"\buntil\s+(\w+)\s+(\d{1,2})(?:,?\s*(\d{4}))?\b", lower)
    if m:
        month_name, day_str = m.group(1), m.group(2)
        year = int(m.group(3)) if m.group(3) else today.year
        month = months.get(month_name)
        if month:
            try:
                return date(year, month, int(day_str)).isoformat()
            except ValueError:
                pass
    # "until end of the month" or "this month"
    if re.search(r"\buntil\s+end\s+of\s+(?:the\s+)?month\b|\bthis\s+month\b", lower):
        m2 = (date(today.year, today.month + 1, 1) - timedelta(days=1)) if today.month < 12 else date(today.year, 12, 31)
        return m2.isoformat()
    # "next month" -> until end of next month
    if re.search(r"\bnext\s+month\b", lower):
        nm = today.month + 1 if today.month < 12 else 1
        ny = today.year if today.month < 12 else today.year + 1
        last_day = (date(ny, nm + 1, 1) - timedelta(days=1)) if nm < 12 else date(ny, 12, 31)
        return last_day.isoformat()
    # "until June" or "until end of June"
    m3 = re.search(r"\buntil\s+(?:end\s+of\s+)?(\w+)\b", lower)
    if m3:
        month = months.get(m3.group(1))
        if month:
            year = today.year if month >= today.month else today.year + 1
            last_day = (date(year, month + 1, 1) - timedelta(days=1)) if month < 12 else date(year, 12, 31)
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


def _parse_next_week_date(text: str) -> Optional[str]:
    """Return the date of the same weekday next week, or Monday next week for 'next week'."""
    lower = text.lower()
    if re.search(r"\bnext\s+week\b", lower):
        today = date.today()
        # Find the start of next week (Monday)
        days_to_monday = (7 - today.weekday()) % 7
        if days_to_monday == 0:
            days_to_monday = 7
        return (today + timedelta(days=days_to_monday)).isoformat()
    return _parse_date(text)


def _parse_next_weekday_ref(text: str) -> Optional[str]:
    """Parse 'this Friday', 'next Monday', "this week's Thursday", etc. -> YYYY-MM-DD."""
    lower = text.lower()
    today = date.today()
    wd_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
        "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    }
    for name, py_wd in sorted(wd_map.items(), key=lambda x: -len(x[0])):
        m = re.search(rf"\b(?:this|next)\s+{name}\b", lower)
        if m:
            days = (py_wd - today.weekday()) % 7
            if days == 0:
                days = 7
            return (today + timedelta(days=days)).isoformat()
    return _parse_date(text)


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


def _date_mention_count(text: str) -> int:
    lower = _norm(text)
    count = len(_DATE_RE.findall(text))
    count += len(re.findall(r"\b(today|tomorrow|tmr|tmrw)\b", lower))
    count += len(
        re.findall(
            r"\b(?:next\s+|this\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            lower,
        )
    )
    return count


def _clean_todo_title(text: str) -> str:
    cleaned = re.sub(
        r"^\s*(add|create|new)\s+(a\s+)?(task|todo)\s+(to\s+)?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^\s*add\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+to\s+(my\s+|the\s+)?(todo|task)(\s+list)?\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*remind me\s+(to|about)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = _remove_date_time_words(cleaned)
    return cleaned.strip(" .")


def _clean_schedule_title(text: str) -> str:
    cleaned = re.sub(
        r"^\s*(add|create|book|put|schedule)\s+(a\s+)?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^\s*(i\s+)?((need|have|want|got)\s+to|must)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^\s*(i\s+)?(wanna|gonna|gotta)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^\s*i'?m\s+(going\s+to\s+|gonna\s+)?",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    date_prefix = (
        r"20\d{2}-\d{2}-\d{2}|today|tomorrow|tmr|tmrw|"
        r"(?:next\s+|this\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    )
    cleaned = re.sub(rf"^\s*(?:on\s+|for\s+)?(?:{date_prefix})\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*(?:at\s+)?\d{1,2}(:\d{2})?\s*(am|pm)?(?:\s+|$)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(?:on|for)?\s*(?:next\s+|this\s+)?"
        r"(?:tomorrow|tmr|tmrw|today|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\bat\s+\d{1,2}(:\d{2})?\s*(am|pm)?\b.*$", "", cleaned, flags=re.IGNORECASE)
    # Require leading whitespace so time keywords are only stripped as suffixes,
    # not when the keyword IS the title (e.g. "lunch", "morning standup").
    cleaned = re.sub(r"\s+(after\s+lunch|lunch|morning|afternoon|evening|noon)\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+(every\s+\w+|weekdays?|daily|everyday)\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(to|on|in)\s+(my\s+|the\s+)?(calendar|schedule)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(" .")
    if cleaned.lower() in {"it", "this", "that", "them"}:
        cleaned = ""
    if cleaned.isupper():
        cleaned = cleaned.capitalize()
    return cleaned or "Scheduled block"


def _clean_lookup_query(text: str) -> str:
    cleaned = re.sub(
        r"^\s*(delete|remove|cancel|complete|finish|mark|move|reschedule|update|change|push|shift|skip)\s+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^\s*all\s+", "", cleaned, flags=re.IGNORECASE)
    # "to start at 10am" / "time to 2pm" / "to next week" style phrases attach
    # destination info as noise tokens to the query.
    cleaned = re.sub(r"\s+to\s+start\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\btime\s+to\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+to\s+next\s+(week|month|year)\b.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bfrom\s+(my\s+|the\s+)?(calendar|schedule)\b", " ", cleaned, flags=re.IGNORECASE)
    # Drop possessive markers so "tomorrow's dentist" → "dentist" after cleanup.
    cleaned = re.sub(r"'s\b", " ", cleaned)
    cleaned = re.sub(
        r"\b(my|the|a|an|task|todo|event|events|meeting|meetings|appointment|appointments|"
        r"session|sessions|calendar|schedule|thing|time)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(future|past|upcoming|weekly|daily|monthly|yearly|recurring)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = _remove_date_time_words(cleaned)
    cleaned = " ".join(cleaned.split())
    cleaned = re.sub(r"^(with)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(on|in|for|at|to)\s*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(" .\"'")


def _clean_web_query(text: str) -> str:
    cleaned = re.sub(r"^\s*(search the web for|web search|search for)\s+", "", text, flags=re.IGNORECASE)
    return cleaned.strip(" .")


def _clean_journal_query(text: str) -> str:
    cleaned = re.sub(r"^\s*search\s+(my\s+)?journal\s+(for\s+)?", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*journal\s+search\s+(for\s+)?", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(" .")


def _remove_date_time_words(text: str) -> str:
    cleaned = _DATE_RE.sub("", text)
    cleaned = re.sub(
        r"\b\d{1,2}(:\d{2})?\s*(am|pm)?\s*[-–]\s*\d{1,2}(:\d{2})?\s*(am|pm)?\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(today|tomorrow|tmr|tmrw|morning|afternoon|evening|noon|after lunch|lunch|after work|"
        r"this month|next month|every day|everyday|daily|weekdays?)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(on|by|at|to|for)?\s*(next\s+|this\s+)?"
        r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(at|by|to)\s+\d{1,2}(:\d{2})?\s*(am|pm)?\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b", "", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split())


def _extract_habit_entities(text: str) -> dict[str, Any]:
    lower = _norm(text)
    if any(w in lower for w in ("show", "list")):
        return {"operation": "list"}
    if "streak" in lower:
        return {"operation": "streak", "id": _extract_id(text), "query": _clean_lookup_query(text)}
    if any(w in lower for w in ("remove", "delete")):
        return {"operation": "remove", "id": _extract_id(text), "query": _clean_lookup_query(text)}
    if "check in" in lower or "checkin" in lower or "done" in lower:
        return {"operation": "checkin", "id": _extract_id(text), "query": _clean_lookup_query(text), "checkin_date": _parse_date(text)}
    name = re.sub(r"^\s*(add|create|start)\s+(a\s+)?habit\s+(to\s+)?", "", text, flags=re.IGNORECASE)
    return {"operation": "add", "name": _remove_date_time_words(name).strip(" .")}
