"""Action planning for personal-manager intents."""
from __future__ import annotations

from datetime import date
from typing import Any

from ..domain.commands import (
    HabitCommand,
    JournalCommand,
    ListStateCommand,
    ScheduleAddCommand,
    ScheduleCancelSeriesFromCommand,
    ScheduleModifyOccurrenceCommand,
    ScheduleRemoveCommand,
    ScheduleSkipOccurrenceCommand,
    ScheduleUpdateCommand,
    TodoAddCommand,
    TodoCompleteCommand,
    TodoRemoveCommand,
    action_from_command,
)
from ..presentation.formatters import _format_schedule_remove_summary
from ..resolvers.schedule import resolve_schedule_targets
from ..domain.session import normalize_pm_session_id
from ..domain.types import PMAction, PMIntent
from ..parsing.datetime import _default_end_time, _parse_time, _smart_end_time
from ..persistence.personalization import upsert_user_preference


def _normalize_recurrence(rec: Any) -> dict | None:
    if isinstance(rec, str) and rec:
        return {"freq": rec, "by_day": [], "interval": 1}
    if isinstance(rec, dict):
        return rec
    return None


def plan_pm_actions(intent: PMIntent, entities: dict[str, Any], config: Any) -> list[PMAction]:
    sid = getattr(config, "user_id", "") or normalize_pm_session_id(config.session_id)
    if intent == PMIntent.CREATE_TODO:
        title = entities["title"]
        return [action_from_command("todo_add", TodoAddCommand(title=title, due=entities.get("due")), summary=f"Add todo: {title}")]

    if intent == PMIntent.COMPLETE_TODO:
        return [action_from_command("todo_complete", TodoCompleteCommand.model_validate(entities), summary="Mark todo complete")]

    if intent == PMIntent.REMOVE_TODO:
        return [
            action_from_command(
                "todo_remove",
                TodoRemoveCommand.model_validate(entities),
                risk_level="medium",
                requires_approval=True,
                summary="Remove todo",
            )
        ]

    if intent == PMIntent.CREATE_SCHEDULE_EVENT:
        raw_entries = entities.get("entries") if isinstance(entities.get("entries"), list) else [entities]
        entries = [
            {
                "title": str(entry.get("title") or "Scheduled block").strip() or "Scheduled block",
                "date": entry.get("date") or "",
                "weekday": entry.get("weekday"),
                "series_id": entry.get("series_id") or "",
                "recurrence": _normalize_recurrence(entry.get("recurrence")),
                "start": entry.get("start") or "",
                "end": entry.get("end") or _smart_end_time(entry.get("start"), entry.get("title") or ""),
                "notes": str(entry.get("notes") or ""),
            }
            for entry in raw_entries
            if isinstance(entry, dict)
        ]
        summary = f"Add {len(entries)} schedule events" if len(entries) > 1 else f"Add schedule event: {entries[0]['title']}"
        return [
            action_from_command(
                "schedule_add",
                ScheduleAddCommand(entries=entries),
                risk_level="low",
                requires_approval=False,
                summary=summary,
            )
        ]

    if intent == PMIntent.REMOVE_SCHEDULE_EVENT:
        resolved = resolve_schedule_targets(sid, config.data_dir, entities)
        if not resolved.ok:
            return [PMAction("explain", {"message": resolved.message}, summary=resolved.message)]
        remove_payload = {"ids": resolved.ids}
        if resolved.google_events:
            remove_payload["googleEvents"] = resolved.google_events
        return [
            action_from_command(
                "schedule_remove",
                ScheduleRemoveCommand.model_validate(remove_payload),
                risk_level="medium",
                requires_approval=True,
                summary=_format_schedule_remove_summary(resolved.titles),
            )
        ]

    if intent == PMIntent.UPDATE_SCHEDULE_EVENT:
        resolved = resolve_schedule_targets(sid, config.data_dir, entities)
        if not resolved.ok:
            return [PMAction("explain", {"message": resolved.message}, summary=resolved.message)]
        patch: dict[str, Any] = {"id": resolved.ids[0]}
        if entities.get("date"):
            patch["date"] = entities["date"]
        if entities.get("start"):
            patch["start"] = entities["start"]
            patch["end"] = _default_end_time(entities["start"])
        update_payload = {"updates": [patch]}
        if resolved.google_events:
            update_payload["googleEvents"] = resolved.google_events
        return [
            action_from_command(
                "schedule_update",
                ScheduleUpdateCommand.model_validate(update_payload),
                risk_level="medium",
                requires_approval=True,
                summary=f"Update schedule event: {resolved.titles[0]}",
            )
        ]

    if intent == PMIntent.SKIP_OCCURRENCE:
        resolved = resolve_schedule_targets(sid, config.data_dir, entities)
        if not resolved.ok:
            return [PMAction("explain", {"message": resolved.message}, summary=resolved.message)]
        series_id = resolved.ids[0]
        skip_date = entities.get("skip_date", "")
        return [
            action_from_command(
                "schedule_add_exception",
                ScheduleSkipOccurrenceCommand(series_id=series_id, skip_date=skip_date),
                risk_level="medium",
                requires_approval=True,
                summary=f"Skip {skip_date} for '{resolved.titles[0]}'",
            )
        ]

    if intent == PMIntent.MODIFY_OCCURRENCE:
        resolved = resolve_schedule_targets(sid, config.data_dir, entities)
        if not resolved.ok:
            return [PMAction("explain", {"message": resolved.message}, summary=resolved.message)]
        series_id = resolved.ids[0]
        override: dict[str, Any] = {"original_date": entities.get("original_date", "")}
        if entities.get("start"):
            override["start"] = entities["start"]
            override["end"] = entities.get("end") or _default_end_time(entities["start"])
        if entities.get("title"):
            override["title"] = entities["title"]
        return [
            action_from_command(
                "schedule_add_override",
                ScheduleModifyOccurrenceCommand(series_id=series_id, override=override),
                risk_level="medium",
                requires_approval=True,
                summary=f"Move occurrence on {override['original_date']} for '{resolved.titles[0]}'",
            )
        ]

    if intent == PMIntent.CANCEL_SERIES_FROM:
        resolved = resolve_schedule_targets(sid, config.data_dir, entities)
        if not resolved.ok:
            return [PMAction("explain", {"message": resolved.message}, summary=resolved.message)]
        series_id = resolved.ids[0]
        from_date = entities.get("from_date") or date.today().isoformat()
        return [
            action_from_command(
                "schedule_cancel_series_from",
                ScheduleCancelSeriesFromCommand(series_id=series_id, from_date=from_date),
                risk_level="medium",
                requires_approval=True,
                summary=f"Cancel all future '{resolved.titles[0]}' from {from_date}",
            )
        ]

    if intent == PMIntent.LIST_STATE:
        return [action_from_command("list_state", ListStateCommand(target=entities.get("target", "todos")), summary="List state")]

    if intent == PMIntent.HABIT_ACTION:
        action_type = f"habit_{entities.get('operation', 'list')}"
        requires = action_type == "habit_remove"
        return [
            action_from_command(
                action_type,
                HabitCommand.model_validate(entities),
                risk_level="medium" if requires else "low",
                requires_approval=requires,
                summary=f"Habit action: {entities.get('operation', 'list')}",
            )
        ]

    if intent == PMIntent.JOURNAL_ACTION:
        return [action_from_command(f"journal_{entities['operation']}", JournalCommand.model_validate(entities), summary="Journal action")]

    if intent == PMIntent.SAVE_MEMORY:
        op = entities.get("operation")
        if op == "private_export":
            return [
                PMAction(
                    "private_export",
                    {},
                    risk_level="high",
                    requires_approval=True,
                    summary="Export private personal-manager memory",
                )
            ]
        # Confidence gate — explicit "remember that…" requests bypass it (confidence=1.0
        # by convention); model-extracted SAVE_MEMORY must clear the threshold.
        confidence = float(entities.get("_confidence", 1.0))
        explicit = bool(entities.get("explicit_request"))
        if not explicit and confidence < 0.72:
            return []  # not confident enough — let the turn fall through to fallback
        fact = entities.get("fact", "")
        if not fact:
            return []
        if entities.get("sensitive"):
            _maybe_write_habit_preference(entities, config)
            return [
                PMAction(
                    "private_note_append",
                    {"note": fact},
                    summary="Save sensitive fact to private manager notes",
                )
            ]
        return [PMAction("remember", {"fact": fact}, summary="Save shared memory")]

    if intent == PMIntent.GENERAL_COACHING and entities.get("operation") == "web_search":
        if entities.get("sensitive"):
            return [
                PMAction(
                    "web_search_blocked",
                    {"query": entities.get("query", "")},
                    risk_level="high",
                    requires_approval=True,
                    summary="Search may expose private context",
                )
            ]
        return []

    return []


_SCHEDULABLE_ACTIVITIES = {
    "gym", "workout", "run", "exercise", "jog", "yoga", "training",
    "meeting", "standup", "stand-up", "commute", "walk",
}

_CATEGORY_MAP = {
    "gym": "workout", "workout": "workout", "run": "workout",
    "exercise": "workout", "jog": "workout", "yoga": "workout",
    "training": "workout", "meeting": "meeting", "standup": "meeting",
    "stand-up": "meeting", "commute": "commute", "walk": "workout",
}


def _maybe_write_habit_preference(entities: dict[str, Any], config: Any) -> None:
    """If a sensitive habit fact contains a schedulable activity + concrete time,
    also write a machine-usable user_preference record for the scheduler."""
    if not entities.get("habit"):
        return
    fact = str(entities.get("fact") or "")
    time_val = _parse_time(fact)
    if not time_val:
        return
    fact_lower = fact.lower()
    category = next(
        (_CATEGORY_MAP[act] for act in _SCHEDULABLE_ACTIVITIES if act in fact_lower),
        None,
    )
    if not category:
        return
    try:
        sid = str(config.session_id)
        data_dir = str(config.data_dir)
        upsert_user_preference(
            sid,
            data_dir,
            scope_type="category",
            scope_key=category,
            rule_type="preferred_window",
            value={"start": time_val, "end": _default_end_time(time_val)},
            confidence=0.70,
            source="habit_statement",
        )
    except Exception:
        pass
