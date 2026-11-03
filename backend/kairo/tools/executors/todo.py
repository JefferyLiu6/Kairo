"""Todo action executor."""
from __future__ import annotations

from typing import Any

from kairo.utils.formatters import _format_date_natural
from kairo.tools.resolvers.todo import resolve_todo_id
from kairo.storage.store import todo_add, todo_complete, todo_remove
from kairo.core.domain.types import PMAction
from kairo.tools.executors.common import _result


def execute_todo_action(action: PMAction, session_id: str, data_dir: str) -> dict[str, Any] | None:
    if action.action_type == "todo_add":
        title = action.payload["title"]
        due = action.payload.get("due")
        raw = todo_add(title, due, session_id, data_dir)
        if raw.startswith("Error"):
            return _result(raw)
        if due:
            msg = f"Added '{title}' — due {_format_date_natural(due) or due}."
        else:
            msg = f"Added '{title}'."
        return _result(msg)
    if action.action_type == "todo_complete":
        todo_id = resolve_todo_id(session_id, data_dir, action.payload)
        if not todo_id["ok"]:
            return todo_id
        raw = todo_complete(todo_id["id"], session_id, data_dir)
        if raw.startswith("Error"):
            return _result(raw)
        label = action.payload.get("query", "")
        return _result(f"Done! Checked off '{label}'." if label else "Done! Checked it off.")
    if action.action_type == "todo_remove":
        todo_id = resolve_todo_id(session_id, data_dir, action.payload)
        if not todo_id["ok"]:
            return todo_id
        label = action.payload.get("query", "")
        raw = todo_remove(todo_id["id"], session_id, data_dir)
        if raw.startswith("Error"):
            return _result(raw)
        return _result(f"Removed '{label}' from your list." if label else "Removed.")
    return None
