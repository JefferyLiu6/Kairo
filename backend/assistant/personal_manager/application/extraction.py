"""Extraction logic: user text → PMPlanExtraction / PMExtraction."""
from __future__ import annotations

import re
from typing import Any, Optional

from assistant.shared.llm_env import build_llm, with_retry

from ..extractors.entities import extract_pm_entities
from ..extractors.intent import _looks_like_pm_coaching_prompt, classify_pm_intent
from ..extractors.model import (
    _format_model_extraction_prompt,
    _format_model_plan_prompt,
    _json_object_from_text,
    _message_content_to_text,
    _should_try_model_extraction,
)
from ..domain.types import PMExtraction, PMIntent, PMPlanExtraction, PMTaskExtraction
from ..legacy.workflow_parsing import (
    _parse_schedule_entries,
)
from ..parsing.datetime import _default_end_time, _has_explicit_time_signal, _parse_date, _parse_time
from ..parsing.schedule_text import _has_non_schedule_plan_marker, _looks_like_ambiguous_schedulable_routine
from .self_disclosure import analyze_activity_disclosure
from .validators import _LOW_CONFIDENCE_THRESHOLD, _estimate_extraction_confidence, validate_entities

_MODEL_EXTRACTION_SOURCE = "model_structured"
_DETERMINISTIC_EXTRACTION_SOURCE = "deterministic_regex"


def extract_pm_plan(message: str, config: Optional[Any] = None) -> PMPlanExtraction:
    """Extract all actionable PM tasks from one user message."""
    model_plan = _extract_pm_plan_with_model(message, config)
    allow_single_model_fallback = not _should_try_model_extraction(config)
    deterministic = _extract_pm_plan_deterministic(
        message,
        config,
        allow_single_model=allow_single_model_fallback,
    )
    if _deterministic_activity_disclosure_wins(message, deterministic, model_plan):
        return deterministic
    if _looks_like_pm_coaching_prompt(message) and len(deterministic.tasks) == 1:
        return deterministic
    if model_plan is None or model_plan.confidence < _LOW_CONFIDENCE_THRESHOLD:
        return deterministic
    if not model_plan.tasks and deterministic.tasks:
        return deterministic
    if len(deterministic.tasks) > 1 and len(model_plan.tasks) <= 1:
        return deterministic
    # Mirror the single-request guard: if the model produced one task with
    # missing fields that the deterministic plan resolves with the same intent,
    # prefer deterministic — the regex is more reliable for dates/times.
    if (
        len(model_plan.tasks) == 1
        and model_plan.tasks[0].missing_fields
        and len(deterministic.tasks) == 1
        and not deterministic.tasks[0].missing_fields
        and deterministic.tasks[0].intent == model_plan.tasks[0].intent
    ):
        return deterministic
    # Ambiguous-routine without time: model often creates a todo or fills in a
    # made-up time. Force the deterministic result so the missing-fields question
    # fires and the user is asked for date/time before anything is saved.
    if (
        _looks_like_ambiguous_schedulable_routine(message)
        and not _has_explicit_time_signal(message)
        and len(deterministic.tasks) == 1
        and deterministic.tasks[0].intent == PMIntent.CREATE_SCHEDULE_EVENT
        and deterministic.tasks[0].missing_fields
    ):
        return deterministic
    # Deterministic recurrence wins: if regex found a recurring entry but the
    # model returned a single-date event, trust the regex — models frequently
    # collapse "everyday" into one date.
    if (
        len(deterministic.tasks) == 1
        and deterministic.tasks[0].intent == PMIntent.CREATE_SCHEDULE_EVENT
        and _task_has_recurrence(deterministic.tasks[0])
        and len(model_plan.tasks) == 1
        and model_plan.tasks[0].intent == PMIntent.CREATE_SCHEDULE_EVENT
        and not _task_has_recurrence(model_plan.tasks[0])
    ):
        return deterministic
    return _merge_model_plan_with_deterministic_scope(model_plan, deterministic)


def _task_has_recurrence(task: PMTaskExtraction) -> bool:
    """Return True only when a well-formed recurrence dict is present (not a bare string)."""
    entries = task.entities.get("entries")
    if isinstance(entries, list):
        return any(isinstance(e, dict) and isinstance(e.get("recurrence"), dict) for e in entries)
    return isinstance(task.entities.get("recurrence"), dict)


def _deterministic_activity_disclosure_wins(
    message: str,
    deterministic: PMPlanExtraction,
    model_plan: Optional[PMPlanExtraction],
) -> bool:
    if analyze_activity_disclosure(message) is None:
        return False
    if len(deterministic.tasks) != 1 or deterministic.tasks[0].intent != PMIntent.SAVE_MEMORY:
        return False
    if model_plan is None or not model_plan.tasks:
        return True
    if len(model_plan.tasks) != 1:
        return False
    return model_plan.tasks[0].intent in {PMIntent.UNKNOWN, PMIntent.GENERAL_COACHING}


def _merge_model_plan_with_deterministic_scope(
    model_plan: PMPlanExtraction,
    deterministic: PMPlanExtraction,
) -> PMPlanExtraction:
    if len(model_plan.tasks) != 1 or len(deterministic.tasks) != 1:
        return model_plan
    model_task = model_plan.tasks[0]
    det_task = deterministic.tasks[0]
    if model_task.intent == PMIntent.LIST_STATE:
        merged_entities = dict(model_task.entities)
        det_target = det_task.entities.get("target") if det_task.intent == PMIntent.LIST_STATE else None
        merged_target = _prefer_list_target(
            model_task.entities.get("target"),
            det_target,
        )
        if merged_target:
            merged_entities["target"] = merged_target
        elif "target" not in merged_entities:
            merged_entities["target"] = "todos"
        if merged_entities == model_task.entities:
            return model_plan
        merged_task = model_task.model_copy(
            update={
                "entities": merged_entities,
                "missing_fields": validate_entities(model_task.intent, merged_entities),
            }
        )
        return _normalize_plan_extraction(
            model_plan.model_copy(
                update={
                    "tasks": [merged_task],
                }
            )
        )
    if model_task.intent != PMIntent.REMOVE_SCHEDULE_EVENT or det_task.intent != model_task.intent:
        return model_plan

    merged_entities = _merge_schedule_delete_scope(model_task.entities, det_task.entities)
    if merged_entities == model_task.entities:
        return model_plan
    merged_task = model_task.model_copy(
        update={
            "entities": merged_entities,
            "missing_fields": validate_entities(model_task.intent, merged_entities),
        }
    )
    global_missing = list(model_plan.global_missing_fields)
    if merged_entities.get("bulk"):
        global_missing = [
            field for field in global_missing
            if str(field).strip() not in {"range_start", "range_end"}
        ]
    return _normalize_plan_extraction(
        model_plan.model_copy(
            update={
                "tasks": [merged_task],
                "global_missing_fields": global_missing,
            }
        )
    )


def _merge_schedule_delete_scope(
    entities: dict[str, Any],
    deterministic_entities: dict[str, Any],
) -> dict[str, Any]:
    patched = dict(entities)
    if deterministic_entities.get("bulk"):
        patched["bulk"] = True
    for key in ("range_start", "range_end", "start", "end", "reference_date", "query"):
        if deterministic_entities.get(key) and not patched.get(key):
            patched[key] = deterministic_entities[key]
    return patched


def _extract_pm_plan_deterministic(
    message: str,
    config: Optional[Any] = None,
    *,
    allow_single_model: bool = True,
) -> PMPlanExtraction:
    if _looks_like_pm_coaching_prompt(message):
        extraction = _extract_pm_request_deterministic(message)
        return _normalize_plan_extraction(
            PMPlanExtraction(
                tasks=[_task_from_extraction(extraction, "task-1")],
                confidence=extraction.confidence,
                source=_DETERMINISTIC_EXTRACTION_SOURCE,
            )
        )

    tasks: list[PMTaskExtraction] = []
    for idx, clause in enumerate(_split_plan_clauses(message), start=1):
        extraction = (
            extract_structured_pm_request(clause, config)
            if allow_single_model
            else _extract_pm_request_deterministic(clause)
        )
        if extraction.intent == PMIntent.UNKNOWN and not extraction.entities:
            continue
        tasks.append(_task_from_extraction(extraction, f"task-{idx}"))
    if not tasks:
        return PMPlanExtraction(tasks=[], confidence=0.2, source=_DETERMINISTIC_EXTRACTION_SOURCE)
    return _normalize_plan_extraction(
        PMPlanExtraction(
            tasks=tasks,
            confidence=min((task.confidence for task in tasks), default=0.2),
            source=_DETERMINISTIC_EXTRACTION_SOURCE,
        )
    )


def _extract_pm_plan_with_model(message: str, config: Optional[Any]) -> Optional[PMPlanExtraction]:
    if not _should_try_model_extraction(config):
        return None

    try:
        llm = build_llm(
            getattr(config, "provider", "openai"),
            getattr(config, "model", ""),
            getattr(config, "api_key", None),
            getattr(config, "base_url", None),
        )
        raw = with_retry(lambda: llm.invoke(_format_model_plan_prompt(message)), max_attempts=1)
        payload = _json_object_from_text(_message_content_to_text(raw))
        if payload is None:
            return None
        return _normalize_plan_extraction(PMPlanExtraction.model_validate(payload))
    except Exception:
        return None


def _task_from_extraction(extraction: PMExtraction, task_id: str) -> PMTaskExtraction:
    return PMTaskExtraction(
        task_id=task_id,
        intent=extraction.intent,
        entities=dict(extraction.entities),
        confidence=extraction.confidence,
        missing_fields=list(extraction.missing_fields),
        depends_on=[],
        source=extraction.source,
        reasoning_summary=extraction.reasoning_summary,
    )


def _normalize_plan_extraction(plan: PMPlanExtraction) -> PMPlanExtraction:
    tasks = [_normalize_task_extraction(task, idx) for idx, task in enumerate(plan.tasks, start=1)]
    confidence = min([plan.confidence, *[task.confidence for task in tasks]]) if tasks else plan.confidence
    source = plan.source or (tasks[0].source if tasks else _DETERMINISTIC_EXTRACTION_SOURCE)
    return PMPlanExtraction(
        tasks=tasks,
        global_missing_fields=[str(item).strip() for item in plan.global_missing_fields if str(item).strip()],
        confidence=max(0.0, min(confidence, 1.0)),
        source=source,
    )


def _normalize_task_extraction(task: PMTaskExtraction, idx: int) -> PMTaskExtraction:
    intent = task.intent
    entities = _normalize_model_entities(intent, dict(task.entities))
    missing = validate_entities(intent, entities)
    confidence = task.confidence
    if missing:
        confidence = min(confidence, _estimate_extraction_confidence(intent, entities, missing))
    return PMTaskExtraction(
        task_id=task.task_id or f"task-{idx}",
        intent=intent,
        entities=entities,
        confidence=max(0.0, min(confidence, 1.0)),
        missing_fields=missing,
        depends_on=[str(item) for item in task.depends_on],
        source=task.source or _DETERMINISTIC_EXTRACTION_SOURCE,
        reasoning_summary=_clean_reasoning_summary(task.reasoning_summary),
    )


def extract_structured_pm_request(message: str, config: Optional[Any] = None) -> PMExtraction:
    """
    Return the best extraction for the message.

    Strategy:
    1. Try model extraction first (richer for complex/ambiguous phrasing).
    2. If model succeeds but still has missing_fields, check deterministic.
       If deterministic resolves the same intent with no missing fields, use it —
       the deterministic regex is more reliable for structured patterns (dates,
       times, named events) than the model's varying key names.
    3. Fall back to deterministic when model call fails or confidence is too low.
    """
    det = _extract_pm_request_deterministic(message)
    model_extraction = _extract_pm_request_with_model(message, config)

    if model_extraction is None or model_extraction.confidence < _LOW_CONFIDENCE_THRESHOLD:
        return det

    if (
        analyze_activity_disclosure(message) is not None
        and det.intent == PMIntent.SAVE_MEMORY
        and model_extraction.intent in {PMIntent.UNKNOWN, PMIntent.GENERAL_COACHING}
    ):
        return det
    # Self-disclosure phrases should not be blocked by a malformed model SAVE_MEMORY
    # extraction (e.g., missing fact/operation). Let the deterministic path route
    # to UNKNOWN and rely on the dedicated self-disclosure handler in workflow.
    if (
        analyze_activity_disclosure(message) is not None
        and model_extraction.intent == PMIntent.SAVE_MEMORY
        and model_extraction.missing_fields
    ):
        return det

    # Model succeeded — but if it still has missing fields that deterministic resolves,
    # prefer deterministic (model uses inconsistent key names; regex is predictable).
    if model_extraction.missing_fields and not det.missing_fields and det.intent == model_extraction.intent:
        return det

    # For LIST_STATE: the model often omits the "target" field.
    # Always copy it from the deterministic extractor, which reliably reads
    # keywords like "calendar"/"schedule" → "schedule", "habit" → "habits", etc.
    if model_extraction.intent == PMIntent.LIST_STATE:
        preferred_target = _prefer_list_target(
            model_extraction.entities.get("target"),
            det.entities.get("target"),
        )
        if preferred_target is None:
            preferred_target = "todos"
        patched = dict(model_extraction.entities)
        patched["target"] = preferred_target
        return PMExtraction(
            intent=model_extraction.intent,
            entities=patched,
            confidence=model_extraction.confidence,
            missing_fields=model_extraction.missing_fields,
            reasoning_summary=model_extraction.reasoning_summary,
            source=model_extraction.source,
        )

    if model_extraction.intent == PMIntent.REMOVE_SCHEDULE_EVENT and det.intent == model_extraction.intent:
        patched = _merge_schedule_delete_scope(model_extraction.entities, det.entities)
        if patched != model_extraction.entities:
            return PMExtraction(
                intent=model_extraction.intent,
                entities=patched,
                confidence=model_extraction.confidence,
                missing_fields=validate_entities(model_extraction.intent, patched),
                reasoning_summary=model_extraction.reasoning_summary,
                source=model_extraction.source,
            )

    return model_extraction


def _extract_pm_request_deterministic(message: str) -> PMExtraction:
    intent = classify_pm_intent(message)
    entities = extract_pm_entities(message, intent)
    missing = validate_entities(intent, entities)
    confidence = _estimate_extraction_confidence(intent, entities, missing)
    return PMExtraction(
        intent=intent,
        entities=entities,
        confidence=confidence,
        missing_fields=missing,
        source=_DETERMINISTIC_EXTRACTION_SOURCE,
    )


def _extract_pm_request_with_model(message: str, config: Optional[Any]) -> Optional[PMExtraction]:
    if not _should_try_model_extraction(config):
        return None

    try:
        llm = build_llm(
            getattr(config, "provider", "openai"),
            getattr(config, "model", ""),
            getattr(config, "api_key", None),
            getattr(config, "base_url", None),
        )
        raw = with_retry(lambda: llm.invoke(_format_model_extraction_prompt(message)), max_attempts=1)
        payload = _json_object_from_text(_message_content_to_text(raw))
        if payload is None:
            return None
        parsed = PMExtraction.model_validate(payload)
        return _normalize_model_extraction(parsed)
    except Exception:
        return None


def _normalize_model_extraction(parsed: PMExtraction) -> PMExtraction:
    entities = _normalize_model_entities(parsed.intent, parsed.entities)
    # validate_entities is the authoritative source of what's actually missing.
    # Discard the model's own missing_fields — LLMs often declare a field missing
    # even when they successfully extracted it (e.g. returns start="08:00" but
    # also puts "start" in missing_fields), which causes spurious clarifying questions.
    missing = validate_entities(parsed.intent, entities)
    return PMExtraction(
        intent=parsed.intent,
        entities=entities,
        confidence=parsed.confidence,
        missing_fields=missing,
        reasoning_summary=_clean_reasoning_summary(parsed.reasoning_summary),
        source=_MODEL_EXTRACTION_SOURCE,
    )


def _normalize_model_entities(intent: PMIntent, entities: dict[str, Any]) -> dict[str, Any]:
    normalized = {str(k): v for k, v in entities.items() if v not in (None, "")}

    # Normalize common LLM key aliases before any other processing
    for alias, canonical in (
        ("start_time", "start"),
        ("end_time", "end"),
        ("time", "start"),          # "time": "15:00" → start
        ("event_date", "date"),
        ("event_title", "title"),
        ("task_title", "title"),
        ("task_name", "title"),
        ("event_name", "title"),
        ("name", "title"),          # model often uses "name" for event title
        ("event", "title"),
        ("habit_name", "name"),
        ("entry_body", "body"),
    ):
        if alias in normalized and canonical not in normalized:
            normalized[canonical] = normalized.pop(alias)
        elif alias in normalized:
            del normalized[alias]  # duplicate — canonical already present

    if intent in {PMIntent.CREATE_TODO, PMIntent.COMPLETE_TODO, PMIntent.REMOVE_TODO}:
        if isinstance(normalized.get("due"), str):
            normalized["due"] = _parse_date(normalized["due"]) or normalized["due"]

    if intent == PMIntent.LIST_STATE:
        normalized_target = _normalize_list_target(normalized.get("target"))
        if normalized_target is not None:
            normalized["target"] = normalized_target

    if intent in {
        PMIntent.CREATE_SCHEDULE_EVENT,
        PMIntent.UPDATE_SCHEDULE_EVENT,
        PMIntent.REMOVE_SCHEDULE_EVENT,
    }:
        # For remove/update the model often returns "title" meaning "find the event
        # with this title" — that's a query, not a new event title.
        if intent in {PMIntent.UPDATE_SCHEDULE_EVENT, PMIntent.REMOVE_SCHEDULE_EVENT}:
            if "title" in normalized and "query" not in normalized:
                normalized["query"] = normalized.pop("title")
            elif "title" in normalized:
                del normalized["title"]

        for key in ("date", "reference_date"):
            value = normalized.get(key)
            if isinstance(value, str):
                normalized[key] = _parse_date(value) or value
        for key in ("start", "end"):
            value = normalized.get(key)
            if isinstance(value, str) and not re.match(r"^\d{2}:\d{2}$", value):
                normalized[key] = _parse_time(value) or value
        if normalized.get("ordinal") is not None:
            try:
                normalized["ordinal"] = int(normalized["ordinal"])
            except (TypeError, ValueError):
                normalized.pop("ordinal", None)
        if intent == PMIntent.CREATE_SCHEDULE_EVENT:
            normalized = _normalize_schedule_create_entities(normalized)

    if "sensitive" in normalized:
        normalized["sensitive"] = bool(normalized["sensitive"])
    return normalized


def _normalize_schedule_create_entities(entities: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(entities)
    entries = normalized.get("entries")
    if isinstance(entries, list):
        fixed_entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            fixed = dict(entry)
            if not fixed.get("title") and fixed.get("date") and fixed.get("start"):
                fixed["title"] = "Scheduled block"
            if fixed.get("start") and not fixed.get("end"):
                fixed["end"] = _default_end_time(str(fixed["start"]))
            fixed_entries.append(fixed)
        if fixed_entries:
            normalized["entries"] = fixed_entries
            normalized.update({k: v for k, v in fixed_entries[0].items() if k in {"title", "date", "start", "end", "notes"}})
    elif not normalized.get("title") and normalized.get("date") and normalized.get("start"):
        normalized["title"] = "Scheduled block"
    if normalized.get("start") and not normalized.get("end"):
        normalized["end"] = _default_end_time(str(normalized["start"]))
    return normalized


def _merge_missing_fields(primary: list[str], secondary: list[str]) -> list[str]:
    merged: list[str] = []
    for field in [*primary, *secondary]:
        item = str(field).strip()
        if item and item not in merged:
            merged.append(item)
    return merged


def _normalize_list_target(raw_target: Any) -> str | None:
    if raw_target is None:
        return None
    text = str(raw_target).strip().lower()
    if not text:
        return None
    if text in {"schedule", "calendar", "event", "events", "appointment", "appointments"}:
        return "schedule"
    if text in {"habit", "habits", "streak", "streaks", "routine", "routines"}:
        return "habits"
    if text in {"journal", "journals", "log", "logs"}:
        return "journal"
    if text in {"todo", "todos", "task", "tasks", "reminder", "reminders"}:
        return "todos"
    return None


def _prefer_list_target(model_target: Any, deterministic_target: Any) -> str | None:
    model_norm = _normalize_list_target(model_target)
    det_norm = _normalize_list_target(deterministic_target)
    if model_norm is None:
        return det_norm
    # Deterministic extraction is reliable when explicit schedule/habit/journal
    # keywords are present. Prefer it over the model's generic todo default.
    if model_norm == "todos" and det_norm and det_norm != "todos":
        return det_norm
    return model_norm


def _clean_reasoning_summary(value: str) -> str:
    return " ".join(str(value).split())[:160]


def _split_plan_clauses(message: str) -> list[str]:
    cleaned = " ".join(message.strip().split())
    if not cleaned:
        return []
    if _should_keep_as_single_schedule_task(cleaned):
        return [cleaned]

    chunks = [part.strip() for part in re.split(r"[,;\n]+", cleaned) if part.strip()]
    action_start = (
        r"remind\b|journal\b|log\b|remember\b|add\s+(?:task|todo|habit)\b|"
        r"create\s+(?:task|todo|habit)\b|new\s+(?:task|todo)\b|"
        r"delete\b|remove\b|cancel\b|complete\b|mark\b|finish\b|done\b|"
        r"move\b|reschedule\b|update\b|change\b|search\b|show\b|list\b|schedule\b"
    )
    parts: list[str] = []
    for chunk in chunks:
        chunk = re.sub(r"^\s*(and|then)\s+", "", chunk, flags=re.IGNORECASE)
        parts.extend(
            part.strip(" .")
            for part in re.split(rf"\s+(?:and|then)\s+(?={action_start})", chunk, flags=re.IGNORECASE)
            if part.strip(" .")
        )
    return parts or [cleaned]


def _should_keep_as_single_schedule_task(message: str) -> bool:
    if _has_non_schedule_plan_marker(message):
        return False
    if classify_pm_intent(message) != PMIntent.CREATE_SCHEDULE_EVENT:
        return False
    return len(_parse_schedule_entries(message)) > 1
