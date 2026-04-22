"""Journal action executor."""
from __future__ import annotations

from typing import Any

from ..persistence.control_store import pm_db_path
from ..persistence.journal import journal_append, journal_read, journal_search
from ..domain.types import PMAction
from .common import _result


def execute_journal_action(action: PMAction, session_id: str, data_dir: str) -> dict[str, Any] | None:
    db_path = pm_db_path(session_id, data_dir)
    if action.action_type == "list_state" and action.payload.get("target", "todos") == "journal":
        return _result(journal_read(db_path))
    if action.action_type == "journal_append":
        raw = journal_append(action.payload["body"], db_path)
        if raw.startswith("Error"):
            return _result(raw)
        return _result("Saved to your journal.")
    if action.action_type == "journal_read":
        return _result(journal_read(db_path))
    if action.action_type == "journal_search":
        return _result(journal_search(action.payload.get("query", ""), db_path))
    return None
