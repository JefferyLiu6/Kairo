"""Action dispatcher for typed personal-manager commands."""
from __future__ import annotations

from typing import Any

from kairo.core.domain.commands import validate_action_payload
from kairo.core.domain.session import normalize_pm_session_id
from kairo.storage.store import todo_list
from kairo.core.domain.types import PMAction
from kairo.tools.executors.common import _result
from kairo.tools.executors.habit import execute_habit_action
from kairo.tools.executors.journal import execute_journal_action
from kairo.tools.executors.memory import execute_memory_action
from kairo.tools.executors.schedule import execute_schedule_action
from kairo.tools.executors.todo import execute_todo_action
from kairo.tools.receipts import observed


def execute_pm_action(action: PMAction, config: Any) -> dict[str, Any]:
    """Run one typed action. Inside a receipt collector, the stores are read back and checked afterwards."""
    sid = getattr(config, "user_id", "") or normalize_pm_session_id(config.session_id)
    return observed(action, config, sid, lambda: _execute(action, config, sid, config.data_dir))


def _execute(action: PMAction, config: Any, sid: str, data_dir: str) -> dict[str, Any]:
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
