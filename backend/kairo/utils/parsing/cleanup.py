"""Title and query cleanup: strip command words, dates and times from user text."""
from __future__ import annotations

import re
from typing import Any

from kairo.utils.parsing.datetime import _DATE_RE, _parse_date
from kairo.utils.parsing.text import _extract_id, _norm


def _strip_leading_phrases(text: str, words: list[str]) -> str:
    cleaned = text.strip()
    for word in words:
        cleaned = re.sub(rf"^\s*{re.escape(word)}( that| entry| this)?\s*", "", cleaned, flags=re.IGNORECASE)
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
        r"\b(today|tdy|tonight|tomorrow|tmr|tmrw|morning|afternoon|evening|noon|after lunch|lunch|after work|"
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
        r"20\d{2}-\d{2}-\d{2}|today|tdy|tomorrow|tmr|tmrw|"
        r"(?:next\s+|this\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    )
    cleaned = re.sub(rf"^\s*(?:on\s+|for\s+)?(?:{date_prefix})\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*(?:at\s+)?\d{1,2}(:\d{2})?\s*(am|pm)?(?:\s+|$)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(?:on|for)?\s*(?:next\s+|this\s+)?"
        r"(?:tomorrow|tmr|tmrw|today|tdy|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\bat\s+\d{1,2}(:\d{2})?\s*(am|pm)?\b.*$", "", cleaned, flags=re.IGNORECASE)
    # Require leading whitespace so time keywords are only stripped as suffixes,
    # not when the keyword IS the title (e.g. "lunch", "morning standup").
    # "every morning" must go before the bare "morning" rule, or "every" is left behind.
    cleaned = re.sub(r"\s+(?:every|each)\s+(?:morning|evening|night|day)\b.*$", "", cleaned, flags=re.IGNORECASE)
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
