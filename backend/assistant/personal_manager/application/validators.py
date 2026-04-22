"""Validation, confidence, and clarification helpers for PM extractions."""
from __future__ import annotations

from typing import Any

from ..domain.types import PMExtraction, PMIntent
from ..parsing.datetime import _parse_time
from ..parsing.text import _norm


_LOW_CONFIDENCE_THRESHOLD = 0.65

_SENSITIVE_TERMS = {
    "medical", "diagnosis", "diagnosed", "therapy", "therapist", "doctor",
    "health", "medication", "bank", "debt", "salary", "tax", "legal",
    "lawyer", "password", "secret", "ssn", "social security", "credit",
    "relationship", "divorce", "diabetes",
    # Personal identity / location / immigration — risky to surface via web search
    "home address", "address", "location history", "immigration", "visa",
    # Unsafe queries — illegal/abusive intent; gated behind approval so the user
    # has to explicitly confirm before we search.
    "hack into", "hacking into", "exploit", "crack into",
    # Government-issued identifiers — surfacing these via web search is risky.
    "license", "driver's license", "drivers license", "passport number",
    # Daily routines → private notes, not shared profile
    "routine", "daily routine", "every morning", "every evening", "every night",
    "every day", "each day", "wake up", "go to bed", "go to sleep",
    "my schedule", "bedtime", "sleep at",
}

_PHYSICAL_ACTIVITY_TERMS = {
    "gym", "run", "workout", "exercise", "jog", "yoga", "training",
}

_FREQUENCY_TERMS = {"always", "usually", "every", "each", "regularly"}


def _contains_sensitive_terms(text: str) -> bool:
    lower = _norm(text)
    if any(term in lower for term in _SENSITIVE_TERMS):
        return True
    # Recurring physical activity with a specific time → treat as private habit
    has_frequency = any(w in lower for w in _FREQUENCY_TERMS)
    has_physical = any(w in lower for w in _PHYSICAL_ACTIVITY_TERMS)
    has_time = bool(_parse_time(text))
    return has_frequency and has_physical and has_time


def _estimate_extraction_confidence(intent: PMIntent, entities: dict[str, Any], missing: list[str]) -> float:
    if intent == PMIntent.UNKNOWN:
        return 0.2
    if intent == PMIntent.GENERAL_COACHING:
        return 0.8

    confidence = 0.9
    if intent in {PMIntent.UPDATE_SCHEDULE_EVENT, PMIntent.REMOVE_SCHEDULE_EVENT}:
        if entities.get("id"):
            confidence = 0.95
        elif entities.get("query"):
            confidence = 0.78
        elif entities.get("ordinal"):
            confidence = 0.72
        elif intent == PMIntent.REMOVE_SCHEDULE_EVENT and entities.get("start") and entities.get("reference_date"):
            confidence = 0.78
        elif intent == PMIntent.REMOVE_SCHEDULE_EVENT and entities.get("start"):
            confidence = 0.68
        else:
            confidence = 0.45
        if entities.get("ordinal") and entities.get("reference_date"):
            confidence += 0.08
        if (
            intent == PMIntent.UPDATE_SCHEDULE_EVENT
            and not entities.get("date")
            and not entities.get("start")
            and not (entities.get("ordinal") and entities.get("reference_date"))
        ):
            confidence = min(confidence, 0.55)
    elif intent == PMIntent.CREATE_SCHEDULE_EVENT:
        confidence = 0.9 if entities.get("title") else 0.6
    elif intent == PMIntent.HABIT_ACTION and entities.get("operation") == "list":
        confidence = 0.9
    elif intent in {PMIntent.COMPLETE_TODO, PMIntent.REMOVE_TODO, PMIntent.HABIT_ACTION}:
        confidence = 0.85 if (entities.get("id") or entities.get("query") or entities.get("name")) else 0.6

    confidence -= min(0.15 * len(missing), 0.45)
    return round(max(0.0, min(confidence, 1.0)), 2)


def _requires_confidence_clarification(extraction: PMExtraction) -> bool:
    if extraction.missing_fields:
        return False
    if extraction.intent in {
        PMIntent.UNKNOWN,
        PMIntent.GENERAL_COACHING,
        PMIntent.APPROVE_ACTION,
        PMIntent.REJECT_ACTION,
        PMIntent.LIST_STATE,
    }:
        return False
    return extraction.confidence < _LOW_CONFIDENCE_THRESHOLD


def _low_confidence_question(intent: PMIntent, entities: dict[str, Any]) -> str:
    if intent in {PMIntent.UPDATE_SCHEDULE_EVENT, PMIntent.REMOVE_SCHEDULE_EVENT}:
        query = entities.get("query", "")
        if query:
            return f"Which event called '{query}'? I want to make sure I get the right one."
        return "Which event were you thinking of?"
    if intent in {PMIntent.COMPLETE_TODO, PMIntent.REMOVE_TODO}:
        return "Which task did you mean?"
    return "Can you say a bit more about what you'd like?"


def _has_schedule_reference(entities: dict[str, Any], intent: "PMIntent | None" = None) -> bool:
    # Vague pronouns/nouns ("my thing", "everything") can pass through the
    # query extractor as a stray word but don't constitute a real reference.
    if entities.get("vague_target"):
        return False
    if entities.get("id") or entities.get("query"):
        return True
    # For UPDATE, `start` is the *destination* time, not an event identifier.
    # For REMOVE, `start` can identify which event to delete.
    if entities.get("start") and intent != PMIntent.UPDATE_SCHEDULE_EVENT:
        return True
    return bool(entities.get("ordinal") and (entities.get("reference_date") or entities.get("category")))


def _clarifying_question(
    intent: PMIntent,
    missing: list[str],
    entities: dict[str, Any] | None = None,
) -> str:
    if intent == PMIntent.CREATE_SCHEDULE_EVENT:
        if "date" in missing and "start" in missing:
            return "When's that happening? Give me a day and time."
        if "date" in missing:
            return "What day?"
        if "start" in missing:
            return "What time?"
    if intent == PMIntent.UPDATE_SCHEDULE_EVENT:
        if "schedule event id or title" in missing:
            return "Which event do you want to move?"
        if "new date or time" in missing:
            return "Move it to when?"
    if intent == PMIntent.REMOVE_SCHEDULE_EVENT:
        # Only force the which-event clarifying question when the request
        # itself was vague ("my thing", "my plans", "everything"). A plain
        # ambiguous reference ("the event tomorrow") stays on the default
        # "I'll need schedule event id or title" reply so the downstream
        # lookup flow can do its work.
        if "schedule event id or title" in missing and entities and entities.get("vague_target"):
            return "Which event do you want to remove?"
    if intent == PMIntent.CREATE_TODO:
        return "What's the task?"
    missing_str = " and ".join(missing) if len(missing) <= 2 else ", ".join(missing[:-1]) + f", and {missing[-1]}"
    return f"I'll need {missing_str} to do that."


def validate_entities(intent: PMIntent, entities: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if intent == PMIntent.CREATE_TODO and not entities.get("title"):
        missing.append("task title")
    if intent in (PMIntent.COMPLETE_TODO, PMIntent.REMOVE_TODO):
        if not entities.get("id") and not entities.get("query"):
            missing.append("todo id or title")
    if intent == PMIntent.CREATE_SCHEDULE_EVENT:
        entries = entities.get("entries")
        if isinstance(entries, list) and entries:
            for entry in entries:
                if not isinstance(entry, dict):
                    missing.append("schedule entry")
                    continue
                has_when = (
                    entry.get("date")
                    or entry.get("weekday") is not None
                    or entry.get("recurrence") is not None
                )
                if not has_when and "date" not in missing:
                    missing.append("date")
                if not entry.get("start") and "start" not in missing:
                    missing.append("start")
            return missing
        has_when = (
            entities.get("date")
            or entities.get("weekday") is not None
            or entities.get("recurrence") is not None
        )
        if not entities.get("start"):
            missing.append("start")
        if not has_when:
            missing.append("date")
    if intent in (PMIntent.UPDATE_SCHEDULE_EVENT, PMIntent.REMOVE_SCHEDULE_EVENT):
        if not _has_schedule_reference(entities, intent):
            missing.append("schedule event id or title")
        if intent == PMIntent.UPDATE_SCHEDULE_EVENT and not entities.get("date") and not entities.get("start"):
            missing.append("new date or time")
    if intent == PMIntent.SKIP_OCCURRENCE:
        if not entities.get("query") and not entities.get("id"):
            missing.append("recurring event name")
        if not entities.get("skip_date"):
            missing.append("date to skip")
    if intent == PMIntent.MODIFY_OCCURRENCE:
        if not entities.get("query") and not entities.get("id"):
            missing.append("recurring event name")
        if not entities.get("original_date"):
            missing.append("which occurrence date")
        if not entities.get("start") and not entities.get("date"):
            missing.append("new time or date")
    if intent == PMIntent.CANCEL_SERIES_FROM:
        if not entities.get("query") and not entities.get("id"):
            missing.append("recurring event name")
    if intent == PMIntent.HABIT_ACTION:
        op = entities.get("operation")
        if op in {"add"} and not entities.get("name"):
            missing.append("habit name")
        if op in {"checkin", "streak", "remove"} and not entities.get("id") and not entities.get("query"):
            missing.append("habit id or name")
    if intent == PMIntent.JOURNAL_ACTION:
        if entities.get("operation") == "append" and not entities.get("body"):
            missing.append("journal entry")
        if entities.get("operation") == "search" and not entities.get("query"):
            missing.append("journal search query")
    if intent == PMIntent.SAVE_MEMORY and entities.get("operation") == "save_fact":
        if not entities.get("fact"):
            missing.append("memory fact")
    return missing
