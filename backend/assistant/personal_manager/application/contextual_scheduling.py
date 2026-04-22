"""Context-aware schedule draft creation from recent PM topic context."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..domain.types import PMIntent, PMPlanExtraction, PMTaskExtraction
from ..parsing.datetime import _parse_date, _parse_time, _parse_times
from ..parsing.text import _norm
from ..persistence.recent_context import RecentContextRecord, list_recent_context, recent_context_is_stale
from ..presentation.formatters import _format_date_natural, _format_time_natural
from .extraction import _normalize_plan_extraction


CONTEXTUAL_RELEVANCE_WEIGHTS = {
    "latest_topic_within_5m": 0.35,
    "location_like_phrase": 0.25,
    "activity_or_venue_keyword": 0.25,
    "assistant_invited_schedule_flag": 0.15,
    "topic_stale": -0.30,
    "multiple_fresh_topics_without_activity": -0.30,
    "question_or_hedge_or_noncommitment": -0.40,
}
CONTEXTUAL_RELEVANCE_THRESHOLD = 0.65
CONTEXTUAL_INVITED_ANSWER_THRESHOLD = 0.50

_AMBIGUOUS_CONTEXT_WINDOW_MINUTES = 10
_VENUE_KEYWORDS = {
    "court", "gym", "park", "field", "studio", "pool", "rink", "track",
    "community center", "community centre", "club",
}
_LOCATION_PREPOSITIONS = {"at", "in"}
_PRONOUN_TITLES = {"it", "this", "that", "them", "there"}
_SOFT_CONTEXT_TITLE_RE = re.compile(r"^(let'?s\s+do|should\s+be\s+fine|i'?ll\s+go|i\s+will\s+go)\b")
_ACTIVITY_DURATIONS = {
    "basketball": 90,
    "tennis": 90,
    "gym": 60,
    "workout": 60,
    "run": 60,
    "yoga": 60,
    "meeting": 60,
    "appointment": 60,
    "coffee": 30,
    "call": 30,
    "standup": 30,
}


@dataclass(frozen=True)
class ContextualScheduleProposal:
    status: str
    reply: str
    plan: PMPlanExtraction | None = None
    blocking_task_id: str = "task-1"
    provenance: dict[str, str] | None = None
    relevance: dict[str, Any] | None = None
    original_message: str = ""
    candidates: list[dict[str, Any]] | None = None
    parsed_date: str = ""
    parsed_start: str = ""


def build_contextual_schedule_proposal(
    message: str,
    plan: PMPlanExtraction,
    session_id: str,
    data_dir: str,
) -> ContextualScheduleProposal | None:
    contexts = _activity_contexts(session_id, data_dir)
    if not contexts:
        return None
    if _has_explicit_schedule_verb(message):
        return None
    if _has_competing_intent(plan):
        return None
    if not _has_date_and_time(message):
        # Bare-time fragment with multiple fresh topics — we can't pick a
        # schedule silently, but we can at least offer disambiguation so the
        # next turn has context to anchor on. Date defaults to today.
        disambiguation = _bare_time_disambiguation(message, contexts)
        if disambiguation is not None:
            return disambiguation
        return None

    commitment = classify_commitment(message)
    if commitment == "unclear":
        return ContextualScheduleProposal(
            status="repair",
            reply="Did you want me to schedule that?",
            original_message=message,
        )
    if commitment == "non_commit":
        return ContextualScheduleProposal(
            status="repair",
            reply="Did you want me to schedule that?",
            original_message=message,
        )

    context_choice = _choose_context(contexts, message)
    if context_choice.status != "ok":
        candidates_payload: list[dict[str, Any]] | None = None
        if context_choice.candidates:
            candidates_payload = [
                {
                    "activity": str(item.payload.get("activity") or "").lower(),
                    "label": str(
                        item.payload.get("activity_label")
                        or item.payload.get("activity")
                        or ""
                    ),
                }
                for item in context_choice.candidates
            ]
        parsed_date = _parse_date(message) or ""
        parsed_start = _parse_time(message) or ""
        return ContextualScheduleProposal(
            status=context_choice.status,
            reply=context_choice.reply,
            original_message=message,
            candidates=candidates_payload,
            parsed_date=parsed_date,
            parsed_start=parsed_start,
        )

    task = _first_schedule_task(plan)
    if task is not None and not _is_context_fill_candidate(task):
        return None

    relevance = _score_relevance(message, context_choice.context, contexts, commitment)
    required_score = _required_relevance_score(commitment, relevance)
    if float(relevance["score"]) < required_score and commitment != "soft_commit":
        return ContextualScheduleProposal(
            status="repair",
            reply="Did you want me to schedule that?",
            relevance=relevance,
            original_message=message,
        )

    draft = _build_contextual_plan(message, plan, context_choice.context)
    if draft is None:
        return None
    return ContextualScheduleProposal(
        status="confirm",
        reply=_confirmation_summary(draft.plan),
        plan=draft.plan,
        blocking_task_id=draft.blocking_task_id,
        provenance=draft.provenance,
        relevance=relevance,
        original_message=message,
    )


def build_contextual_plan_from_candidate(
    *,
    activity: str,
    label: str,
    parsed_date: str,
    parsed_start: str,
) -> tuple[PMPlanExtraction, str, dict[str, str]] | None:
    """Build a schedule plan for a specific activity candidate, used after the
    user resolves a disambiguation by naming one of the candidates.

    Returns (plan, confirmation_summary, provenance) or None.
    """
    if not parsed_date or not parsed_start:
        return None
    activity = activity.strip().lower()
    display_label = (label or activity.title() or "Activity").strip() or "Activity"
    title = f"Play {display_label.lower()}"
    end = _add_minutes(parsed_start, _activity_duration(activity))
    entry = {
        "title": title,
        "date": parsed_date,
        "start": parsed_start,
        "end": end,
        "notes": f"Activity chosen from disambiguation: {display_label}.",
    }
    entities = dict(entry)
    entities["entries"] = [entry]
    task = PMTaskExtraction(
        task_id="task-1",
        intent=PMIntent.CREATE_SCHEDULE_EVENT,
        entities=entities,
        confidence=0.9,
        missing_fields=[],
        source="contextual_schedule",
    )
    plan = _normalize_plan_extraction(
        PMPlanExtraction(tasks=[task], confidence=0.9, source="contextual_schedule")
    )
    provenance = {
        "title": "inferred_from_disambiguation",
        "activity": "inferred_from_disambiguation",
        "date": "user_provided",
        "start": "user_provided",
        "end": "inferred_duration",
    }
    return plan, _confirmation_summary(plan), provenance


def apply_context_to_explicit_schedule_plan(
    message: str,
    plan: PMPlanExtraction,
    session_id: str,
    data_dir: str,
) -> PMPlanExtraction:
    if not _has_explicit_schedule_verb(message):
        return plan
    contexts = _activity_contexts(session_id, data_dir)
    if not contexts or _has_competing_intent(plan):
        return plan
    task = _first_schedule_task(plan)
    if task is None or not _is_context_fill_candidate(task):
        return plan
    context_choice = _choose_context(contexts, message)
    if context_choice.status != "ok":
        return plan
    draft = _build_contextual_plan(message, plan, context_choice.context)
    return draft.plan if draft is not None else plan


def classify_commitment(message: str) -> str:
    text = _norm(message)
    if not _has_date_and_time(text):
        return "unclear"
    if "?" in message or re.search(r"\b(what\s+about|sounds?\s+good|should\s+we|could\s+we)\b", text):
        return "non_commit"
    if re.search(r"\b(maybe|probably|i\s+guess|works?\s+better|would\s+work)\b", text):
        return "non_commit"
    if re.search(r"\b(let'?s\s+do|should\s+be\s+fine|i'?ll\s+go|i\s+will\s+go)\b", text):
        return "soft_commit"
    return "commit"


def confirmation_plan_with_correction(
    plan: PMPlanExtraction,
    message: str,
) -> tuple[PMPlanExtraction, dict[str, str]] | None:
    if not (_parse_date(message) or _parse_time(message)):
        return None
    if not plan.tasks:
        return None
    task = plan.tasks[0]
    entities = dict(task.entities)
    entries = entities.get("entries")
    entry = dict(entries[0]) if isinstance(entries, list) and entries and isinstance(entries[0], dict) else dict(entities)
    provenance: dict[str, str] = {}
    parsed_date = _parse_date(message)
    parsed_start = _parse_time(message)
    if parsed_date:
        entry["date"] = parsed_date
        entities["date"] = parsed_date
        provenance["date"] = "user_correction"
    if parsed_start:
        duration = _duration_minutes(str(entry.get("start") or parsed_start), str(entry.get("end") or ""))
        entry["start"] = parsed_start
        entry["end"] = _add_minutes(parsed_start, duration)
        entities["start"] = entry["start"]
        entities["end"] = entry["end"]
        provenance["start"] = "user_correction"
        provenance["end"] = "inferred_duration"
    if isinstance(entries, list) and entries:
        patched_entries = [dict(item) if isinstance(item, dict) else item for item in entries]
        patched_entries[0] = entry
        entities["entries"] = patched_entries
    task = task.model_copy(update={"entities": entities, "confidence": max(task.confidence, 0.85)})
    return _normalize_plan_extraction(
        PMPlanExtraction(tasks=[task, *plan.tasks[1:]], confidence=max(plan.confidence, 0.85), source=plan.source)
    ), provenance


def confirmation_summary_for_plan(plan: PMPlanExtraction) -> str:
    return _confirmation_summary(plan)


@dataclass(frozen=True)
class _ContextChoice:
    status: str
    reply: str
    context: RecentContextRecord | None = None
    candidates: list[RecentContextRecord] | None = None


@dataclass(frozen=True)
class _Draft:
    plan: PMPlanExtraction
    blocking_task_id: str
    provenance: dict[str, str]


def _activity_contexts(session_id: str, data_dir: str) -> list[RecentContextRecord]:
    return list_recent_context(session_id, data_dir, context_type="activity_topic")


def _has_date_and_time(message: str) -> bool:
    return bool(_parse_date(message) and _parse_time(message))


def _has_explicit_schedule_verb(message: str) -> bool:
    text = _norm(message)
    return bool(re.search(r"\b(schedule|add|book|put|block|calendar)\b", text))


def _has_competing_intent(plan: PMPlanExtraction) -> bool:
    if not plan.tasks:
        return False
    if len(plan.tasks) > 1:
        return True
    first = plan.tasks[0]
    return first.intent != PMIntent.CREATE_SCHEDULE_EVENT


def _first_schedule_task(plan: PMPlanExtraction) -> PMTaskExtraction | None:
    if not plan.tasks or plan.tasks[0].intent != PMIntent.CREATE_SCHEDULE_EVENT:
        return None
    return plan.tasks[0]


def _is_context_fill_candidate(task: PMTaskExtraction) -> bool:
    title = str(task.entities.get("title") or "").strip().lower()
    if not title or title == "scheduled block" or title in _PRONOUN_TITLES:
        return True
    if _SOFT_CONTEXT_TITLE_RE.search(title):
        return True
    return bool(re.match(r"^(at|in)\s+", title))


def _choose_context(contexts: list[RecentContextRecord], message: str) -> _ContextChoice:
    latest = contexts[0]
    if len(contexts) < 2 or _message_mentions_activity(message, latest):
        return _ContextChoice("ok", "", latest)
    for context in contexts[1:]:
        if _message_mentions_activity(message, context):
            return _ContextChoice("ok", "", context)
    latest_time = _parse_context_time(latest)
    ambiguous: list[RecentContextRecord] = [latest]
    for context in contexts[1:]:
        context_time = _parse_context_time(context)
        if latest_time and context_time:
            delta = abs((latest_time - context_time).total_seconds()) / 60
            if delta <= _AMBIGUOUS_CONTEXT_WINDOW_MINUTES:
                ambiguous.append(context)
    if len(ambiguous) >= 2:
        labels = [
            str(item.payload.get("activity_label") or item.payload.get("activity") or "that").lower()
            for item in ambiguous
        ]
        return _ContextChoice(
            "disambiguate",
            _format_disambiguation_prompt(labels),
            None,
            candidates=ambiguous,
        )
    return _ContextChoice("ok", "", latest)


def _bare_time_disambiguation(
    message: str,
    contexts: list[RecentContextRecord],
) -> ContextualScheduleProposal | None:
    parsed_start = _parse_time(message)
    if not parsed_start:
        return None
    if classify_commitment(message.replace(parsed_start, "today " + parsed_start, 1)) == "non_commit":
        # If the phrasing is hedged/non-committal, don't push disambiguation.
        return None
    context_choice = _choose_context(contexts, message)
    if context_choice.status != "disambiguate" or not context_choice.candidates:
        return None
    from datetime import date as _date
    candidates_payload = [
        {
            "activity": str(item.payload.get("activity") or "").lower(),
            "label": str(
                item.payload.get("activity_label")
                or item.payload.get("activity")
                or ""
            ),
        }
        for item in context_choice.candidates
    ]
    return ContextualScheduleProposal(
        status="disambiguate",
        reply=context_choice.reply,
        original_message=message,
        candidates=candidates_payload,
        parsed_date=_date.today().isoformat(),
        parsed_start=parsed_start,
    )


def _format_disambiguation_prompt(labels: list[str]) -> str:
    if len(labels) == 2:
        return f"Did you mean {labels[0]} or {labels[1]}?"
    joined = ", ".join(labels[:-1])
    return f"Did you mean {joined}, or {labels[-1]}?"


def _parse_context_time(context: RecentContextRecord) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(context.updated_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _message_mentions_activity(message: str, context: RecentContextRecord) -> bool:
    text = _norm(message)
    activity = str(context.payload.get("activity") or "").lower()
    label = str(context.payload.get("activity_label") or "").lower()
    return bool(
        (activity and re.search(rf"\b{re.escape(activity)}\b", text))
        or (label and re.search(rf"\b{re.escape(label)}\b", text))
    )


def _score_relevance(
    message: str,
    context: RecentContextRecord | None,
    contexts: list[RecentContextRecord],
    commitment: str,
) -> dict[str, Any]:
    components: dict[str, float] = {}
    if context is None:
        return {"score": 0.0, "components": components, "commitment": commitment}
    updated = _parse_context_time(context)
    if updated and datetime.now(timezone.utc) - updated <= timedelta(minutes=5):
        components["latest_topic_within_5m"] = CONTEXTUAL_RELEVANCE_WEIGHTS["latest_topic_within_5m"]
    if _extract_location(message):
        components["location_like_phrase"] = CONTEXTUAL_RELEVANCE_WEIGHTS["location_like_phrase"]
    if _message_mentions_activity(message, context) or _has_venue_keyword(message):
        components["activity_or_venue_keyword"] = CONTEXTUAL_RELEVANCE_WEIGHTS["activity_or_venue_keyword"]
    if context.payload.get("assistant_invited_schedule"):
        components["assistant_invited_schedule_flag"] = CONTEXTUAL_RELEVANCE_WEIGHTS["assistant_invited_schedule_flag"]
    if recent_context_is_stale(context):
        components["topic_stale"] = CONTEXTUAL_RELEVANCE_WEIGHTS["topic_stale"]
    if len(contexts) > 1 and not any(_message_mentions_activity(message, item) for item in contexts[:2]):
        components["multiple_fresh_topics_without_activity"] = CONTEXTUAL_RELEVANCE_WEIGHTS["multiple_fresh_topics_without_activity"]
    if commitment == "non_commit":
        components["question_or_hedge_or_noncommitment"] = CONTEXTUAL_RELEVANCE_WEIGHTS["question_or_hedge_or_noncommitment"]
    return {
        "score": round(sum(components.values()), 4),
        "components": components,
        "commitment": commitment,
    }


def _has_venue_keyword(message: str) -> bool:
    text = _norm(message)
    return any(keyword in text for keyword in _VENUE_KEYWORDS)


def _build_contextual_plan(
    message: str,
    plan: PMPlanExtraction,
    context: RecentContextRecord | None,
) -> _Draft | None:
    if context is None:
        return None
    parsed_date = _parse_date(message)
    parsed_start = _parse_time(message)
    if not parsed_date or not parsed_start:
        return None
    task = _first_schedule_task(plan)
    if task is None:
        task = PMTaskExtraction(
            task_id="task-1",
            intent=PMIntent.CREATE_SCHEDULE_EVENT,
            entities={},
            confidence=0.85,
            source="contextual_schedule",
        )
        plan = PMPlanExtraction(tasks=[task], confidence=0.85, source="contextual_schedule")

    activity = str(context.payload.get("activity") or "")
    label = str(context.payload.get("activity_label") or context.payload.get("title_seed") or activity.title() or "Activity")
    location = _extract_location(message)
    style = str(context.payload.get("style") or "").strip().lower()
    title_label = f"{style} {label.lower()}" if style and not location else label
    title = f"{title_label} at {location}" if location else title_label
    times = _parse_times(message)
    end = times[1] if len(times) > 1 else _add_minutes(parsed_start, _activity_duration(activity))
    provenance = {
        "title": "inferred_from_context",
        "activity": "inferred_from_context",
        "date": "user_provided",
        "start": "user_provided",
        "end": "user_provided" if len(times) > 1 else "inferred_duration",
    }
    if style:
        provenance["style"] = "recent_context"
    if location:
        provenance["location"] = "user_provided"
    entry = {
        "title": title,
        "date": parsed_date,
        "start": parsed_start,
        "end": end,
        "notes": f"Activity inferred from recent context: {label}.",
    }
    entities = dict(task.entities)
    entities.update(entry)
    entities["entries"] = [entry]
    patched_task = task.model_copy(update={"entities": entities, "missing_fields": [], "confidence": max(task.confidence, 0.85)})
    patched = _normalize_plan_extraction(
        PMPlanExtraction(
            tasks=[patched_task, *plan.tasks[1:]],
            confidence=max(plan.confidence, 0.85),
            source=plan.source or "contextual_schedule",
        )
    )
    return _Draft(plan=patched, blocking_task_id=patched_task.task_id, provenance=provenance)


def _required_relevance_score(commitment: str, relevance: dict[str, Any]) -> float:
    components = relevance.get("components") if isinstance(relevance.get("components"), dict) else {}
    if (
        commitment == "commit"
        and components.get("latest_topic_within_5m")
        and components.get("assistant_invited_schedule_flag")
    ):
        return CONTEXTUAL_INVITED_ANSWER_THRESHOLD
    return CONTEXTUAL_RELEVANCE_THRESHOLD


def _extract_location(message: str) -> str:
    matches = list(re.finditer(r"\b(at|in)\s+(.+?)(?:[.!?]|$)", message, flags=re.IGNORECASE))
    for match in reversed(matches):
        prep = match.group(1).lower()
        if prep not in _LOCATION_PREPOSITIONS:
            continue
        value = re.sub(r"\b\d{1,2}(:\d{2})?\s*(am|pm)?\b", "", match.group(2), flags=re.IGNORECASE)
        value = re.sub(
            r"\b(today|tomorrow|tmr|tmrw|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            "",
            value,
            flags=re.IGNORECASE,
        )
        value = " ".join(value.strip(" .").split())
        if value and not value[0].isdigit():
            return value
    return ""


def _activity_duration(activity: str) -> int:
    return _ACTIVITY_DURATIONS.get(activity, 60)


def _duration_minutes(start: str, end: str) -> int:
    if not start or not end:
        return 60
    start_m = _minutes(start)
    end_m = _minutes(end)
    if end_m <= start_m:
        return 60
    return max(15, end_m - start_m)


def _add_minutes(start: str, minutes: int) -> str:
    total = (_minutes(start) + minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _minutes(value: str) -> int:
    hour, minute = [int(part) for part in value.split(":", 1)]
    return hour * 60 + minute


def _confirmation_summary(plan: PMPlanExtraction) -> str:
    task = _first_schedule_task(plan)
    entities = task.entities if task is not None else {}
    title = str(entities.get("title") or "that")
    date_value = str(entities.get("date") or "")
    start = str(entities.get("start") or "")
    end = str(entities.get("end") or "")
    time_range = _format_time_natural(start)
    if end:
        time_range = f"{time_range}-{_format_time_natural(end)}"
    when = _format_date_natural(date_value)
    if when and time_range:
        return f"Got it — schedule {title} for {when}, {time_range}?"
    return f"Got it — schedule {title}?"
