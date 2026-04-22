"""Deterministic entity extraction for personal-manager intents."""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from ..application.validators import _contains_sensitive_terms
from ..calendar.recurrence import (
    _parse_next_week_date,
    _parse_next_weekday_ref,
    _parse_schedule_reference_range,
)
from ..domain.types import PMIntent
from ..legacy.workflow_parsing import (
    _clean_journal_query,
    _clean_lookup_query,
    _clean_schedule_title,
    _clean_todo_title,
    _clean_web_query,
    _extract_habit_entities,
    _parse_schedule_entries,
    _strip_leading_phrases,
)
from ..parsing.datetime import (
    _default_end_time,
    _parse_date,
    _parse_destination_date,
    _parse_ordinal,
    _parse_time,
    _parse_times,
)
from ..parsing.schedule_text import (
    _extract_schedule_category,
    _looks_like_ambiguous_schedulable_routine,
    _looks_like_bulk_schedule_delete,
)
from ..parsing.text import _extract_id, _norm
from .intent import _looks_like_web_search_request


# Vague-target markers for removal/update commands. These are generic pronouns
# or catch-all nouns that can't be resolved to a specific event without
# clarification (unlike "the event tomorrow", which is still ambiguous but
# names an actual thing the user has in mind).
_VAGUE_TARGET_NOUN = re.compile(
    r"\b(?:(?:my|all)\s+(?:thing|things|stuff|plans?|schedule|calendar|agenda|day|week)"
    r"|everything|all\s+of\s+it)\b",
    re.IGNORECASE,
)
# If a specific reference is present elsewhere in the same message, the vague
# phrase is just filler (e.g. "...from my schedule"), not the real target.
_SPECIFIC_TIME_WINDOW = re.compile(
    r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?\s*-\s*"
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?\b",
    re.IGNORECASE,
)
_SPECIFIC_NAMED_EVENT = re.compile(
    r"\b(?:the|my)\s+\w+\s+(?:event|meeting|appointment|session|call|reservation|"
    r"block|workout|lunch|dinner|breakfast|class)\b",
    re.IGNORECASE,
)
_SPECIFIC_WITH_PERSON = re.compile(r"\bwith\s+[A-Za-z]{2,}\b", re.IGNORECASE)


def _is_vague_schedule_target(text: str) -> bool:
    if not _VAGUE_TARGET_NOUN.search(text):
        return False
    if _SPECIFIC_TIME_WINDOW.search(text):
        return False
    if _SPECIFIC_NAMED_EVENT.search(text):
        return False
    if _SPECIFIC_WITH_PERSON.search(text):
        return False
    return True


def extract_pm_entities(message: str, intent: PMIntent) -> dict[str, Any]:
    text = message.strip()
    lower = _norm(text)
    entities: dict[str, Any] = {}

    if intent in (PMIntent.APPROVE_ACTION, PMIntent.REJECT_ACTION):
        approval_id = _extract_id(text)
        if approval_id:
            entities["approval_id"] = approval_id
        return entities

    if intent == PMIntent.CREATE_TODO:
        entities["title"] = _clean_todo_title(text)
        entities["due"] = _parse_date(text)
        return entities

    if intent in (PMIntent.COMPLETE_TODO, PMIntent.REMOVE_TODO):
        entities["id"] = _extract_id(text)
        entities["query"] = _clean_lookup_query(text)
        return entities

    if intent == PMIntent.CREATE_SCHEDULE_EVENT:
        entries = _parse_schedule_entries(text)
        if entries:
            entities.update(entries[0])
            entities["entries"] = entries
            return entities
        start = _parse_time(text)
        event_date = _parse_date(text)
        entities["title"] = _clean_schedule_title(text)
        entities["date"] = event_date
        entities["start"] = start
        entities["end"] = _default_end_time(start)
        if _looks_like_ambiguous_schedulable_routine(text):
            entities["ambiguous_life_event"] = True
            entities["date_bias"] = "today"
        return entities

    if intent in (PMIntent.UPDATE_SCHEDULE_EVENT, PMIntent.REMOVE_SCHEDULE_EVENT):
        entities["id"] = _extract_id(text)
        entities["ordinal"] = _parse_ordinal(text)
        entities["category"] = _extract_schedule_category(text)
        entities["query"] = _clean_lookup_query(text)
        if intent == PMIntent.REMOVE_SCHEDULE_EVENT:
            times = _parse_times(text)
            entities["reference_date"] = _parse_date(text)
            entities["start"] = times[0] if times else None
            if len(times) > 1:
                entities["end"] = times[1]
            entities["bulk"] = _looks_like_bulk_schedule_delete(text)
            range_start, range_end = _parse_schedule_reference_range(text)
            if range_start:
                entities["range_start"] = range_start
            if range_end:
                entities["range_end"] = range_end
            if _is_vague_schedule_target(text):
                entities["vague_target"] = True
        else:
            destination_date = _parse_destination_date(text)
            parsed_date = _parse_date(text)
            entities["reference_date"] = parsed_date if entities.get("ordinal") and not destination_date else None
            entities["date"] = destination_date or (None if entities.get("reference_date") else parsed_date)
            entities["start"] = _parse_time(text)
        return entities

    if intent == PMIntent.SKIP_OCCURRENCE:
        entities["query"] = _clean_lookup_query(text)
        entities["skip_date"] = _parse_date(text) or _parse_next_week_date(text)
        return entities

    if intent == PMIntent.MODIFY_OCCURRENCE:
        entities["query"] = _clean_lookup_query(text)
        entities["original_date"] = _parse_date(text) or _parse_next_weekday_ref(text)
        entities["start"] = _parse_time(text)
        if entities["start"]:
            entities["end"] = _default_end_time(entities["start"])
        return entities

    if intent == PMIntent.CANCEL_SERIES_FROM:
        entities["query"] = _clean_lookup_query(text)
        entities["from_date"] = _parse_date(text) or date.today().isoformat()
        return entities

    if intent == PMIntent.LIST_STATE:
        if "habit" in lower:
            entities["target"] = "habits"
        elif "journal" in lower:
            entities["target"] = "journal"
        elif "schedule" in lower or "calendar" in lower or "meeting" in lower or "appointment" in lower or "event" in lower:
            entities["target"] = "schedule"
        elif any(w in lower for w in ("this week", "next week", "today", "tomorrow", "this month", "coming up", "need to do", "should i do")):
            entities["target"] = "schedule"
        else:
            entities["target"] = "todos"
        return entities

    if intent == PMIntent.HABIT_ACTION:
        entities.update(_extract_habit_entities(text))
        return entities

    if intent == PMIntent.JOURNAL_ACTION:
        if "search" in lower:
            op = "search"
            entities["query"] = _clean_journal_query(text)
        else:
            op = "read" if any(w in lower for w in ("read", "show", "list")) else "append"
            entities["body"] = _strip_leading_phrases(text, ["journal", "log", "write", "add"])
        entities["operation"] = op
        return entities

    if intent == PMIntent.SAVE_MEMORY:
        if "export" in lower:
            entities["operation"] = "private_export"
        else:
            is_explicit = bool(re.match(r"^\s*remember( that)?\s+", text, re.IGNORECASE))
            fact = re.sub(r"^\s*remember( that)?\s+", "", text, flags=re.IGNORECASE).strip()
            entities["operation"] = "save_fact"
            entities["fact"] = fact
            entities["sensitive"] = _contains_sensitive_terms(fact)
            entities["habit"] = _is_routine_statement(text)
            entities["explicit_request"] = is_explicit
        return entities

    if intent == PMIntent.GENERAL_COACHING and _looks_like_web_search_request(text):
        entities["operation"] = "web_search"
        entities["query"] = _clean_web_query(text)
        entities["sensitive"] = _contains_sensitive_terms(text)

    return entities


def _is_routine_statement(text: str) -> bool:
    lower = text.strip().lower()
    return bool(re.search(r"\b(always|usually|every|each|routine|habit|regularly|never)\b", lower))
