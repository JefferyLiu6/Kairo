"""Habit action executor."""
from __future__ import annotations

from typing import Any

from ..persistence.control_store import pm_db_path
from ..presentation.formatters import _empty_habit_suggestions_reply, _format_checkin_reply, _habit_list_is_empty
from ..persistence.habits import habit_add, habit_checkin, habit_list, habit_remove, habit_streak
from ..resolvers.habit import resolve_habit_id
from ..domain.types import PMAction
from .common import _result


def execute_habit_action(action: PMAction, session_id: str, data_dir: str) -> dict[str, Any] | None:
    db_path = pm_db_path(session_id, data_dir)
    if action.action_type == "list_state" and action.payload.get("target", "todos") == "habits":
        raw = habit_list(db_path)
        if _habit_list_is_empty(raw):
            return _result(_empty_habit_suggestions_reply())
        return _result(raw)
    if action.action_type == "habit_add":
        name = action.payload["name"]
        raw = habit_add(name, db_path)
        if raw.startswith("Error"):
            return _result(raw)
        return _result(f"Tracking **{name}** now.")
    if action.action_type == "habit_checkin":
        habit_id = resolve_habit_id(db_path, action.payload)
        if not habit_id["ok"]:
            return habit_id
        raw = habit_checkin(habit_id["id"], db_path, action.payload.get("checkin_date"))
        if raw.startswith("Error"):
            return _result(raw)
        return _result(_format_checkin_reply(raw))
    if action.action_type == "habit_streak":
        habit_id = resolve_habit_id(db_path, action.payload)
        if not habit_id["ok"]:
            return habit_id
        return _result(habit_streak(habit_id["id"], db_path))
    if action.action_type == "habit_list":
        raw = habit_list(db_path)
        if _habit_list_is_empty(raw):
            return _result(_empty_habit_suggestions_reply())
        return _result(raw)
    if action.action_type == "habit_remove":
        habit_id = resolve_habit_id(db_path, action.payload)
        if not habit_id["ok"]:
            return habit_id
        name = action.payload.get("name") or action.payload.get("query", "")
        raw = habit_remove(habit_id["id"], db_path)
        if raw.startswith("Error"):
            return _result(raw)
        return _result(f"Stopped tracking **{name}**." if name else "Habit removed.")
    return None
