"""Action dispatcher for typed personal-manager commands."""
from __future__ import annotations

from typing import Any

from ..domain.commands import validate_action_payload
from ..domain.session import normalize_pm_session_id
from ..persistence.store import todo_list
from ..domain.types import PMAction
from .common import _result
from .habit import execute_habit_action
from .journal import execute_journal_action
from .memory import execute_memory_action
from .schedule import execute_schedule_action
from .todo import execute_todo_action


def execute_pm_action(action: PMAction, config: Any) -> dict[str, Any]:
    sid = normalize_pm_session_id(config.session_id)
    data_dir = config.data_dir
    try:
        validate_action_payload(action)
        if action.action_type == "explain":
            return {"ok": False, "message": action.payload["message"]}

        for executor in (
            execute_todo_action,
            execute_schedule_action,
            execute_habit_action,
            execute_journal_action,
        ):
            result = executor(action, sid, data_dir)
            if result is not None:
                return result

        result = execute_memory_action(action, config, sid, data_dir)
        if result is not None:
            return result

        if action.action_type == "list_state":
            raw = todo_list(sid, data_dir)
            return _result(raw)

        return _result(f"Error: unsupported PM action '{action.action_type}'", ok=False)
    except Exception as exc:
        return _result(f"Error: {exc}", ok=False)
