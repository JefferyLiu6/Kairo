"""Pending clarification state and plan-blocker logic."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

from ..domain.types import PMExtraction, PMIntent, PMPlanExtraction, PMTaskExtraction
from ..legacy.workflow_parsing import (
    _clean_lookup_query,
    _clean_schedule_title,
    _clean_todo_title,
    _strip_leading_phrases,
)
from ..parsing.datetime import _default_end_time, _parse_date, _parse_time
from ..parsing.text import _extract_id
from ..persistence.control_store import pm_db_path
from ..persistence.personalization import (
    promote_patterns_from_choice_memory,
    promote_preference_from_repeat_selects,
    record_choice_selected,
)
from ..persistence.working_memory import (
    WorkingMemoryRecord,
    clear_active_working_memory,
    load_active_working_memory,
    save_working_memory,
    working_memory_is_stale,
)
from .extraction import _DETERMINISTIC_EXTRACTION_SOURCE, _normalize_plan_extraction
from .contextual_scheduling import confirmation_plan_with_correction, confirmation_summary_for_plan
from .planner import plan_pm_actions
from .validators import (
    _clarifying_question,
    _contains_sensitive_terms,
    _low_confidence_question,
    _requires_confidence_clarification,
)


@dataclass(frozen=True)
class _PlanBlocker:
    task: PMTaskExtraction
    missing: list[str]
    question: str
    is_lookup_failure: bool = False  # True when the event/item wasn't found (not a missing-fields blocker)


@dataclass(frozen=True)
class ConfirmationResolution:
    status: str
    plan: PMPlanExtraction | None = None
    reply: str = ""


# ── Pending clarification state ───────────────────────────────────────────────

def _pending_path(session_id: str, data_dir: str) -> str:
    return os.path.join(os.path.dirname(pm_db_path(session_id, data_dir)), "pending.json")


def _save_pending(session_id: str, data_dir: str, intent: PMIntent, entities: dict[str, Any], missing: list[str], *, user_id: str = "") -> None:
    payload = {"intent": intent.value, "entities": entities, "missing": missing}
    save_working_memory(
        session_id,
        data_dir,
        user_id=user_id,
        mode="awaiting_freeform",
        source="legacy_pending",
        expected_reply="freeform_answer",
        payload=payload,
        summary=f"Pending {intent.value} freeform clarification",
    )


def _save_pending_plan(
    session_id: str,
    data_dir: str,
    plan: PMPlanExtraction,
    *,
    user_id: str = "",
    blocking_task_id: str,
    missing: list[str],
) -> None:
    payload = {
        "type": "plan",
        "plan": plan.model_dump(mode="json"),
        "blocking_task_id": blocking_task_id,
        "missing": missing,
    }
    save_working_memory(
        session_id,
        data_dir,
        user_id=user_id,
        mode="awaiting_clarification",
        source="plan_clarification",
        expected_reply="date_time_or_freeform",
        payload=payload,
        summary=f"Awaiting clarification for {', '.join(missing) or 'task'}",
    )


def _save_pending_field_choices(
    session_id: str,
    data_dir: str,
    plan: PMPlanExtraction,
    *,
    user_id: str = "",
    blocking_task_id: str,
    missing: list[str],
    choices: list[dict[str, Any]],
) -> None:
    payload = {
        "type": "field_choices",
        "plan": plan.model_dump(mode="json"),
        "blocking_task_id": blocking_task_id,
        "missing": missing,
        "choices": choices,
    }
    save_working_memory(
        session_id,
        data_dir,
        user_id=user_id,
        mode="awaiting_choice",
        source=_pending_choice_source(plan, choices),
        expected_reply="choice_index_or_freeform",
        payload=payload,
        summary=f"Awaiting choice for {', '.join(missing) or 'task'}",
    )


def _save_pending_disambiguation(
    session_id: str,
    data_dir: str,
    *,
    user_id: str = "",
    candidates: list[dict[str, Any]],
    parsed_date: str,
    parsed_start: str,
    reply: str,
    original_message: str,
) -> None:
    payload = {
        "type": "disambiguation",
        "candidates": candidates,
        "parsed_date": parsed_date,
        "parsed_start": parsed_start,
        "reply": reply,
        "original_message": original_message,
    }
    save_working_memory(
        session_id,
        data_dir,
        user_id=user_id,
        mode="awaiting_disambiguation",
        source="contextual_schedule_disambiguation",
        expected_reply="activity_label",
        payload=payload,
        summary=reply,
    )


def _save_pending_confirmation(
    session_id: str,
    data_dir: str,
    plan: PMPlanExtraction,
    *,
    user_id: str = "",
    blocking_task_id: str,
    confirmation_summary: str,
    original_message: str,
    provenance: dict[str, Any],
    relevance: dict[str, Any] | None = None,
) -> None:
    payload = {
        "type": "confirmation",
        "plan": plan.model_dump(mode="json"),
        "blocking_task_id": blocking_task_id,
        "confirmation_summary": confirmation_summary,
        "original_message": original_message,
        "provenance": provenance,
    }
    if relevance is not None:
        payload["relevance"] = relevance
    save_working_memory(
        session_id,
        data_dir,
        user_id=user_id,
        mode="awaiting_confirmation",
        source="contextual_schedule",
        expected_reply="yes_no_or_correction",
        payload=payload,
        summary=confirmation_summary,
    )


def _load_pending(session_id: str, data_dir: str, *, user_id: str = "") -> dict[str, Any] | None:
    record = load_active_working_memory(session_id, data_dir, user_id=user_id)
    if record is not None:
        return _pending_payload_from_working_memory(record)

    try:
        with open(_pending_path(session_id, data_dir), encoding="utf-8") as f:
            legacy = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not isinstance(legacy, dict):
        return None
    migrated = _save_legacy_pending_as_working_memory(session_id, data_dir, legacy, user_id=user_id)
    try:
        os.remove(_pending_path(session_id, data_dir))
    except FileNotFoundError:
        pass
    return _pending_payload_from_working_memory(migrated)


def _clear_pending(session_id: str, data_dir: str, *, user_id: str = "", status: str = "resolved") -> None:
    clear_active_working_memory(session_id, data_dir, user_id=user_id, status=status)
    try:
        os.remove(_pending_path(session_id, data_dir))
    except FileNotFoundError:
        pass


def _pending_payload_from_working_memory(record: WorkingMemoryRecord) -> dict[str, Any]:
    payload = dict(record.payload)
    payload["_working_memory"] = {
        "id": record.id,
        "mode": record.mode,
        "source": record.source,
        "expected_reply": record.expected_reply,
        "last_updated_at": record.last_updated_at,
        "expires_at": record.expires_at,
        "stale": working_memory_is_stale(record),
    }
    return payload


def _save_legacy_pending_as_working_memory(
    session_id: str,
    data_dir: str,
    payload: dict[str, Any],
    *,
    user_id: str = "",
) -> WorkingMemoryRecord:
    pending_type = payload.get("type")
    if pending_type == "field_choices":
        mode = "awaiting_choice"
        source = "field_completion"
        expected_reply = "choice_index_or_freeform"
    elif pending_type == "plan":
        mode = "awaiting_clarification"
        source = "plan_clarification"
        expected_reply = "date_time_or_freeform"
    else:
        mode = "awaiting_freeform"
        source = "legacy_pending"
        expected_reply = "freeform_answer"
    return save_working_memory(
        session_id,
        data_dir,
        user_id=user_id,
        mode=mode,
        source=source,
        expected_reply=expected_reply,
        payload=payload,
        summary="Migrated legacy pending state",
    )


def _pending_choice_source(plan: PMPlanExtraction, choices: list[dict[str, Any]]) -> str:
    if plan.source == "time_slot_recommendation":
        return "time_slot_recommendation"
    for choice in choices:
        source = str(choice.get("source") or "")
        if source == "time_slot_recommendation":
            return source
    return "field_completion"


_CLARIFICATION_BLOCKERS = (
    "add ", "create ", "schedule ", "delete ", "remove ", "cancel ",
    "move ", "reschedule ", "remind ", "journal", "log ", "remember ",
    "search ", "show ", "list ", "complete ", "mark ", "finish ",
    "update ", "change ", "approve ", "reject ", "deny ", "i need to ", "i want to ",
)


def _is_clarification_response(message: str) -> bool:
    """True when the message looks like a bare answer to a clarifying question
    (short, has a date/time signal, no action verb).
    """
    text = message.strip()
    if len(text) > 60:
        return False
    lower = text.lower()
    if any(lower.startswith(p) for p in _CLARIFICATION_BLOCKERS):
        return False
    return bool(_parse_date(text) or _parse_time(text))


def _is_pending_clarification_response(message: str) -> bool:
    text = message.strip()
    if not text or len(text) > 200:
        return False
    lower = text.lower()
    return not any(lower.startswith(p) for p in _CLARIFICATION_BLOCKERS)


def _is_pending_cancel_reply(message: str) -> bool:
    text = " ".join(message.lower().strip(" .!").split())
    return text in {"cancel", "never mind", "nevermind", "nvm", "stop", "no", "nope", "nah"}


def _is_more_options_reply(message: str) -> bool:
    text = " ".join(message.lower().strip(" .!").split())
    return text in {"more", "more options", "show more", "other options", "show other options"}


def _match_pending_disambiguation_candidate(
    pending: dict[str, Any], message: str
) -> dict[str, Any] | None:
    candidates = pending.get("candidates") if isinstance(pending, dict) else None
    if not isinstance(candidates, list):
        return None
    text = " ".join(message.lower().strip(" .!?").split())
    if not text:
        return None
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        activity = str(entry.get("activity") or "").lower().strip()
        label = str(entry.get("label") or "").lower().strip()
        if activity and activity in text:
            return entry
        if label and label in text:
            return entry
    return None


def _is_something_else_reply(message: str) -> bool:
    text = " ".join(message.lower().strip(" .!?").split())
    return text in {
        "something else",
        "give me another",
        "give me another one",
        "another one",
        "different one",
        "something different",
        "anything else",
    }


def _pending_message_is_dialogue_reply(message: str, pending: dict[str, Any]) -> bool:
    if _is_pending_cancel_reply(message) or _is_more_options_reply(message) or _is_something_else_reply(message):
        return True
    if _has_embedded_new_request_marker(message):
        return False
    if _is_clarification_response(message):
        return True
    if _selected_pending_choice(pending, message) is not None:
        return True
    text = message.strip()
    if len(text) > 80:
        return False
    lower = text.lower()
    return not any(marker in lower for marker in _CLARIFICATION_BLOCKERS)


def _has_embedded_new_request_marker(message: str) -> bool:
    lower = message.lower()
    markers = (
        "add ",
        "create ",
        "book ",
        "put ",
        "schedule ",
        "delete ",
        "remove ",
        "cancel my",
        "move ",
        "reschedule ",
        "remember ",
        "log ",
        "journal ",
    )
    return any(marker in lower for marker in markers)


def _pending_is_stale(pending: dict[str, Any]) -> bool:
    meta = pending.get("_working_memory") if isinstance(pending.get("_working_memory"), dict) else {}
    return bool(meta.get("stale"))


def _reconstruct_with_pending(pending: dict[str, Any], follow_up: str) -> str | None:
    """Rebuild a full command from a saved pending intent + the user's follow-up."""
    intent_str = pending.get("intent", "")
    entities = pending.get("entities", {})

    if intent_str == PMIntent.CREATE_SCHEDULE_EVENT.value:
        title = entities.get("title", "")
        return f"schedule {title} {follow_up}" if title else f"schedule event {follow_up}"

    if intent_str == PMIntent.UPDATE_SCHEDULE_EVENT.value:
        ref = entities.get("query") or entities.get("title") or ""
        return f"reschedule my {ref} to {follow_up}" if ref else f"move event to {follow_up}"

    return None


def _pending_payload_to_plan(pending: dict[str, Any]) -> Optional[PMPlanExtraction]:
    if pending.get("type") in {"plan", "field_choices", "confirmation"} and isinstance(pending.get("plan"), dict):
        try:
            return _normalize_plan_extraction(PMPlanExtraction.model_validate(pending["plan"]))
        except Exception:
            return None

    intent = pending.get("intent")
    if not intent:
        return None
    try:
        task = PMTaskExtraction(
            task_id="task-1",
            intent=PMIntent(intent),
            entities=pending.get("entities", {}),
            missing_fields=[str(item) for item in pending.get("missing", [])],
            confidence=0.6,
            source=_DETERMINISTIC_EXTRACTION_SOURCE,
        )
        return _normalize_plan_extraction(PMPlanExtraction(tasks=[task], confidence=task.confidence))
    except Exception:
        return None


def _apply_pending_confirmation_response(
    pending: dict[str, Any],
    message: str,
    config: Any,
) -> ConfirmationResolution:
    plan = _pending_payload_to_plan(pending)
    if plan is None:
        return ConfirmationResolution("unresolved")
    text = " ".join(message.lower().strip(" .!").split())
    yes = bool(re.match(r"^(yes|yep|yeah|confirm|do it|schedule it|ok|okay)\b", text))
    no_with_correction = bool(re.match(r"^(no|nope|nah)\b", text)) and bool(_parse_date(message) or _parse_time(message))
    correction = confirmation_plan_with_correction(plan, message)
    if yes:
        if correction is not None:
            return ConfirmationResolution("execute", plan=correction[0])
        return ConfirmationResolution("execute", plan=plan)
    if correction is not None or no_with_correction:
        updated = correction[0] if correction is not None else plan
        merged_provenance = dict(pending.get("provenance") or {})
        if correction is not None:
            merged_provenance.update(correction[1])
        summary = confirmation_summary_for_plan(updated)
        _save_pending_confirmation(
            str(config.session_id),
            str(config.data_dir),
            updated,
            blocking_task_id=str(pending.get("blocking_task_id") or "task-1"),
            confirmation_summary=summary,
            original_message=str(pending.get("original_message") or message),
            provenance=merged_provenance,
            relevance=pending.get("relevance") if isinstance(pending.get("relevance"), dict) else None,
        )
        return ConfirmationResolution("refresh", plan=updated, reply=summary)
    return ConfirmationResolution("unresolved")


def _apply_clarification_to_plan(
    plan: PMPlanExtraction,
    blocking_task_id: str,
    missing: list[str],
    follow_up: str,
) -> PMPlanExtraction:
    target = blocking_task_id or (plan.tasks[0].task_id if plan.tasks else "")
    updated = [
        _merge_clarification_into_task(task, missing or task.missing_fields, follow_up)
        if task.task_id == target
        else task
        for task in plan.tasks
    ]
    return _normalize_plan_extraction(
        PMPlanExtraction(
            tasks=updated,
            global_missing_fields=[],
            confidence=plan.confidence,
            source=plan.source,
        )
    )


def _apply_pending_field_choice_response(
    plan: PMPlanExtraction,
    pending: dict[str, Any],
    message: str,
    config: Any,
) -> PMPlanExtraction:
    selected = _selected_pending_choice(pending, message)
    blocking_task_id = str(pending.get("blocking_task_id", ""))
    missing = [str(item) for item in pending.get("missing", [])]
    if selected is None:
        if _should_switch_ambiguous_routine_to_fallback(plan, blocking_task_id, message):
            _clear_pending(str(config.session_id), str(config.data_dir), status="cancelled")
            return PMPlanExtraction(
                tasks=[
                    PMTaskExtraction(
                        task_id="task-1",
                        intent=PMIntent.GENERAL_COACHING,
                        entities={},
                        confidence=0.8,
                        source=_DETERMINISTIC_EXTRACTION_SOURCE,
                    )
                ],
                confidence=0.8,
                source=_DETERMINISTIC_EXTRACTION_SOURCE,
            )
        updated = _apply_clarification_to_plan(plan, blocking_task_id, missing, message)
        _record_other_choice(updated, blocking_task_id, missing, message, config)
        return updated

    values = selected.get("values") if isinstance(selected.get("values"), dict) else {}
    target = blocking_task_id or (plan.tasks[0].task_id if plan.tasks else "")
    updated_tasks = [
        _merge_choice_values_into_task(task, values)
        if task.task_id == target
        else task
        for task in plan.tasks
    ]
    _record_selected_choice(selected, plan, target, missing, config)
    return _normalize_plan_extraction(
        PMPlanExtraction(
            tasks=updated_tasks,
            global_missing_fields=[],
            confidence=plan.confidence,
            source=plan.source,
        )
    )


def _selected_pending_choice(pending: dict[str, Any], message: str) -> dict[str, Any] | None:
    raw = message.strip()
    choice_id = _choice_id_from_text(raw)
    if choice_id is not None:
        for choice in pending.get("choices", []):
            if isinstance(choice, dict) and str(choice.get("id")) == choice_id:
                return choice
        return None
    query = _normalized_choice_label(raw)
    if not query:
        return None
    for choice in pending.get("choices", []):
        if isinstance(choice, dict) and _choice_label_matches(query, choice):
            return choice
    return None


def _choice_id_from_text(raw: str) -> str | None:
    text = _choice_text(raw)
    exact = {
        "1": "1",
        "one": "1",
        "first": "1",
        "option 1": "1",
        "choice 1": "1",
        "number 1": "1",
        "the first one": "1",
        "pick 1": "1",
        "choose 1": "1",
        "select 1": "1",
        "do 1": "1",
        "2": "2",
        "two": "2",
        "second": "2",
        "option 2": "2",
        "choice 2": "2",
        "number 2": "2",
        "the second one": "2",
        "pick 2": "2",
        "choose 2": "2",
        "select 2": "2",
        "do 2": "2",
        "3": "3",
        "three": "3",
        "third": "3",
        "option 3": "3",
        "choice 3": "3",
        "number 3": "3",
        "the third one": "3",
        "pick 3": "3",
        "choose 3": "3",
        "select 3": "3",
        "do 3": "3",
    }
    if text in exact:
        return exact[text]
    match = re.match(r"^(?:pick|choose|select|do)\s+(?:the\s+)?(first|second|third|1|2|3)(?:\s+one)?$", text)
    if not match:
        return None
    return {"first": "1", "second": "2", "third": "3"}.get(match.group(1), match.group(1))


def _choice_label_matches(query: str, choice: dict[str, Any]) -> bool:
    values = choice.get("values") if isinstance(choice.get("values"), dict) else {}
    haystack = _choice_text(
        " ".join(
            str(item or "")
            for item in (
                choice.get("label"),
                choice.get("reason"),
                values.get("title"),
                values.get("kind"),
                choice.get("source"),
            )
        )
    )
    if not haystack:
        return False
    if query in haystack:
        return True
    query_tokens = [token for token in query.split() if len(token) >= 3]
    return bool(query_tokens and all(token in haystack for token in query_tokens))


def _normalized_choice_label(raw: str) -> str:
    text = _choice_text(raw)
    fillers = {
        "the", "one", "option", "choice", "do", "pick", "choose", "select",
        "please", "that", "this", "i", "want", "would", "like", "to",
    }
    tokens = [token for token in text.split() if token not in fillers]
    return " ".join(tokens)


def _choice_text(raw: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", raw.lower())
    text = " ".join(text.split())
    text = re.sub(r"\breading\b", "read", text)
    return text


def _should_switch_ambiguous_routine_to_fallback(
    plan: PMPlanExtraction,
    task_id: str,
    message: str,
) -> bool:
    if _parse_date(message) or _parse_time(message):
        return False
    target = task_id or (plan.tasks[0].task_id if plan.tasks else "")
    task = next((item for item in plan.tasks if item.task_id == target), None)
    return bool(task and (task.entities.get("ambiguous_life_event") or task.entities.get("time_slot_recommendation")))


def _merge_choice_values_into_task(task: PMTaskExtraction, values: dict[str, Any]) -> PMTaskExtraction:
    entities = dict(task.entities)
    for key, value in values.items():
        if value:
            entities[key] = value
    if task.intent == PMIntent.CREATE_SCHEDULE_EVENT:
        entries = entities.get("entries")
        if isinstance(entries, list) and entries:
            patched_entries: list[Any] = []
            for idx, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    patched_entries.append(entry)
                    continue
                patched = dict(entry)
                if idx == 0:
                    for key, value in values.items():
                        if value:
                            patched[key] = value
                patched_entries.append(patched)
            entities["entries"] = patched_entries
        if entities.get("start") and not entities.get("end"):
            entities["end"] = _default_end_time(entities["start"])
    if task.intent == PMIntent.UPDATE_SCHEDULE_EVENT and entities.get("start") and not entities.get("end"):
        entities["end"] = _default_end_time(entities["start"])
    return task.model_copy(update={"entities": entities, "confidence": max(task.confidence, 0.85)})


def _record_selected_choice(
    selected: dict[str, Any],
    plan: PMPlanExtraction,
    task_id: str,
    missing: list[str],
    config: Any,
) -> None:
    task = next((item for item in plan.tasks if item.task_id == task_id), None)
    if task is None:
        return
    values = selected.get("values") if isinstance(selected.get("values"), dict) else {}
    if not values:
        return
    scope = selected.get("scope") if isinstance(selected.get("scope"), dict) else {}
    scope_type = str(scope.get("scope_type") or "global")
    scope_key = str(scope.get("scope_key") or "*")
    field_name = _field_name_for_learning(missing)
    record_choice_selected(
        str(config.session_id),
        str(config.data_dir),
        intent=task.intent.value,
        field_name=field_name,
        value=values,
        label=str(selected.get("label") or ""),
        scope_type=scope_type,
        scope_key=scope_key,
        source=str(selected.get("source") or "selected_choice"),
    )
    promote_patterns_from_choice_memory(
        str(config.session_id),
        str(config.data_dir),
        intent=task.intent.value,
        field_name=field_name,
        scope_type=scope_type,
        scope_key=scope_key,
    )
    promote_preference_from_repeat_selects(
        str(config.session_id),
        str(config.data_dir),
        intent=task.intent.value,
        field_name=field_name,
        scope_type=scope_type,
        scope_key=scope_key,
    )


def _record_other_choice(
    plan: PMPlanExtraction,
    task_id: str,
    missing: list[str],
    message: str,
    config: Any,
) -> None:
    target = task_id or (plan.tasks[0].task_id if plan.tasks else "")
    task = next((item for item in plan.tasks if item.task_id == target), None)
    if task is None:
        return
    values = _custom_choice_values(task, missing)
    if not values:
        return
    record_choice_selected(
        str(config.session_id),
        str(config.data_dir),
        intent=task.intent.value,
        field_name=_field_name_for_learning(missing),
        value=values,
        label=message.strip(),
        scope_type="event_type",
        scope_key=_learning_scope_key(task),
        source="other",
    )
    promote_patterns_from_choice_memory(
        str(config.session_id),
        str(config.data_dir),
        intent=task.intent.value,
        field_name=_field_name_for_learning(missing),
        scope_type="event_type",
        scope_key=_learning_scope_key(task),
    )


def _custom_choice_values(task: PMTaskExtraction, missing: list[str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    missing_set = set(missing)
    if "date" in missing_set or "new date or time" in missing_set:
        if task.entities.get("date"):
            values["date"] = task.entities["date"]
    if "start" in missing_set or "new date or time" in missing_set:
        if task.entities.get("start"):
            values["start"] = task.entities["start"]
            values["end"] = task.entities.get("end") or _default_end_time(task.entities["start"])
    return values


def _learning_scope_key(task: PMTaskExtraction) -> str:
    title = str(task.entities.get("title") or task.entities.get("query") or "event")
    title = re.sub(r"\b(my|the|a|an|with|appointment|appt|meeting|event)\b", " ", title.lower())
    title = re.sub(r"[^a-z0-9]+", " ", title)
    return " ".join(title.split())[:80] or "event"


def _field_name_for_learning(missing: list[str]) -> str:
    if {"date", "start"}.issubset(set(missing)) or "new date or time" in missing:
        return "date_start"
    return "+".join(sorted(str(item).replace(" ", "_") for item in missing)) or "field"


def _merge_clarification_into_task(task: PMTaskExtraction, missing: list[str], follow_up: str) -> PMTaskExtraction:
    entities = dict(task.entities)
    text = follow_up.strip()
    parsed_date = _parse_date(text)
    parsed_time = _parse_time(text)
    missing_set = set(missing)

    if task.intent == PMIntent.CREATE_SCHEDULE_EVENT:
        if parsed_date and ("date" in missing_set or not entities.get("date")):
            entities["date"] = parsed_date
        # Always update the time when the user provides one — this handles both filling
        # in a missing start time and correcting an existing one ("sorry, should be 9pm").
        if parsed_time:
            entities["start"] = parsed_time
            entities["end"] = _default_end_time(parsed_time)
        # Update title if absent or defaulted to "Scheduled block"
        if not entities.get("title") or entities.get("title") == "Scheduled block":
            title = _clean_schedule_title(text)
            if title and title != "Scheduled block":
                entities["title"] = title
    elif task.intent == PMIntent.UPDATE_SCHEDULE_EVENT:
        if "new date or time" in missing_set:
            if parsed_date:
                entities["date"] = parsed_date
            if parsed_time:
                entities["start"] = parsed_time
                entities["end"] = _default_end_time(parsed_time)
        if "schedule event id or title" in missing_set:
            found_id = _extract_id(text)
            if found_id:
                entities["id"] = found_id
            else:
                entities["query"] = _clean_lookup_query(text) or text.strip(" .")
    elif task.intent == PMIntent.REMOVE_SCHEDULE_EVENT:
        if parsed_date:
            entities["reference_date"] = parsed_date
        if parsed_time:
            entities["start"] = parsed_time
        found_id = _extract_id(text)
        if found_id:
            entities["id"] = found_id
        elif "schedule event id or title" in missing_set:
            entities["query"] = _clean_lookup_query(text) or text.strip(" .")
    elif task.intent == PMIntent.CREATE_TODO:
        entities["title"] = _clean_todo_title(text) or text.strip(" .")
        if parsed_date:
            entities["due"] = parsed_date
    elif task.intent in {PMIntent.COMPLETE_TODO, PMIntent.REMOVE_TODO}:
        found_id = _extract_id(text)
        if found_id:
            entities["id"] = found_id
        else:
            entities["query"] = _clean_lookup_query(text) or text.strip(" .")
    elif task.intent == PMIntent.JOURNAL_ACTION:
        entities.setdefault("operation", "append")
        entities["body"] = _strip_leading_phrases(text, ["journal", "log", "write", "add"])
    elif task.intent == PMIntent.SAVE_MEMORY:
        entities.setdefault("operation", "save_fact")
        fact = re.sub(r"^\s*remember( that)?\s+", "", text, flags=re.IGNORECASE).strip()
        entities["fact"] = fact or text
        entities["sensitive"] = _contains_sensitive_terms(entities["fact"])
    elif task.intent == PMIntent.HABIT_ACTION:
        entities["query"] = _clean_lookup_query(text) or text.strip(" .")

    updates: dict[str, Any] = {"entities": entities}
    if missing:
        updates["confidence"] = max(task.confidence, 0.8)
    return task.model_copy(update=updates)


_SERIES_INTENTS = {
    PMIntent.SKIP_OCCURRENCE,
    PMIntent.MODIFY_OCCURRENCE,
    PMIntent.CANCEL_SERIES_FROM,
}


def _task_to_extraction(task: PMTaskExtraction) -> PMExtraction:
    return PMExtraction(
        intent=task.intent,
        entities=task.entities,
        confidence=task.confidence,
        missing_fields=task.missing_fields,
        reasoning_summary=task.reasoning_summary,
        source=task.source,
    )


def _find_plan_blocker(plan: PMPlanExtraction, config: Any) -> Optional[_PlanBlocker]:
    for task in plan.tasks:
        if task.missing_fields:
            return _PlanBlocker(
                task,
                list(task.missing_fields),
                _clarifying_question(task.intent, task.missing_fields, task.entities),
            )
        if _requires_confidence_clarification(_task_to_extraction(task)):
            return _PlanBlocker(task, [], _low_confidence_question(task.intent, task.entities))

        actions = plan_pm_actions(task.intent, task.entities, config)
        if not actions:
            return _PlanBlocker(task, ["task"], "Can you clarify the exact item and action?")
        for action in actions:
            if action.action_type == "explain":
                message = str(action.payload.get("message", ""))
                return _PlanBlocker(task, _missing_for_explain(task, message), _question_for_explain(task, message), is_lookup_failure=True)
    return None


def _missing_for_explain(task: PMTaskExtraction, message: str) -> list[str]:
    if task.intent in {PMIntent.UPDATE_SCHEDULE_EVENT, PMIntent.REMOVE_SCHEDULE_EVENT} | _SERIES_INTENTS:
        return ["schedule event id or title"]
    if task.intent in {PMIntent.COMPLETE_TODO, PMIntent.REMOVE_TODO}:
        return ["todo id or title"]
    return [message or "task"]


def _question_for_explain(task: PMTaskExtraction, message: str) -> str:
    if task.intent in {PMIntent.UPDATE_SCHEDULE_EVENT, PMIntent.REMOVE_SCHEDULE_EVENT} | _SERIES_INTENTS:
        if "multiple" in message.lower():
            return f"{message} Which one?"
        query = task.entities.get("query")
        if query:
            return f"I don't see anything called '{query}' — what's the full name?"
        return "Which event?" if task.intent not in _SERIES_INTENTS else "Which recurring event?"
    if task.intent in {PMIntent.COMPLETE_TODO, PMIntent.REMOVE_TODO}:
        return "Which task?"
    return message if message.endswith("?") else f"{message} What did you mean?"
