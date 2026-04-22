"""Tool factory for the Kairo LangGraph agent."""
from __future__ import annotations

import json
import os
from typing import Any

from langchain_core.tools import tool

from assistant.shared.search import web_search as _web_search
from .persistence.habits import (
    habit_add,
    habit_checkin,
    habit_list,
    habit_remove,
    habit_streak,
)
from .persistence.journal import journal_append, journal_read, journal_search
from .persistence.store import (
    _pm_dir,
    private_export,
    private_patch,
    private_read,
    schedule_add,
    schedule_add_exception,
    schedule_add_override,
    schedule_cancel_series_from,
    schedule_read,
    schedule_remove,
    schedule_replace,
    schedule_update,
    todo_add,
    todo_complete,
    todo_list,
    todo_remove,
)


def _schedule_data_arg_to_str(data: Any) -> str:
    if data is None or data == "":
        return ""
    if isinstance(data, str):
        return data
    try:
        return json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        return str(data)


def _private_patch_value_arg_to_str(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _build_tools(config: Any) -> list[Any]:
    sid = config.session_id
    data_dir = config.data_dir
    vault = config.vault_dir
    pm_db = os.path.join(_pm_dir(sid, data_dir), "pm.db")

    @tool
    def manager_private(operation: str, key: str = "", value: Any = "") -> str:
        """
        Manage sensitive private metadata.
        Operations: read, export, patch.
        For patch, pass `key` (profile | active_plans | notes_private) and `value` as JSON
        or as a dict/list — both accepted.
        """
        op = operation.strip().lower()
        if op in ("read", "read_user"):
            return private_read(sid, data_dir)
        if op == "export":
            return private_export(sid, data_dir)
        if op == "patch":
            if not key:
                return "Error: key is required for patch"
            return private_patch(key, _private_patch_value_arg_to_str(value), sid, data_dir)
        return f"Error: unknown operation '{operation}'. Use: read, export, patch"

    @tool
    def manager_schedule(operation: str, data: Any = "") -> str:
        """
        Manage the user's calendar.
        Operations:
          read                — show schedule
          replace             — overwrite entire schedule (data: list of entries)
          add_events          — add new events (data: list of entries); for recurring events include
                                a "recurrence" object with {freq, by_day, interval, until}
          update_events       — patch existing entries by id (data: list of patches)
          remove_events       — delete entries by id (data: list of ids)
          add_exception       — skip one occurrence of a recurring series
                                (data: {series_id, skip_date: YYYY-MM-DD})
          update_occurrence   — override a single occurrence of a series
                                (data: {series_id, override: {original_date, start?, end?, title?, cancelled?}})
          cancel_series_from  — end a recurring series from a given date forward
                                (data: {series_id, from_date: YYYY-MM-DD})
        Pass `data` as a JSON string or dict/list — both accepted.
        """
        data_str = _schedule_data_arg_to_str(data)
        op = operation.strip().lower()
        if op == "read":
            return schedule_read(sid, data_dir)
        if op == "replace":
            return schedule_replace(data_str, sid, data_dir)
        if op == "add_events":
            return schedule_add(data_str, sid, data_dir)
        if op == "update_events":
            return schedule_update(data_str, sid, data_dir)
        if op == "remove_events":
            return schedule_remove(data_str, sid, data_dir)
        if op == "add_exception":
            payload = json.loads(data_str) if isinstance(data_str, str) and data_str.strip().startswith("{") else (data if isinstance(data, dict) else {})
            return schedule_add_exception(payload.get("series_id", ""), payload.get("skip_date", ""), sid, data_dir)
        if op == "update_occurrence":
            payload = json.loads(data_str) if isinstance(data_str, str) and data_str.strip().startswith("{") else (data if isinstance(data, dict) else {})
            return schedule_add_override(payload.get("series_id", ""), payload.get("override", {}), sid, data_dir)
        if op == "cancel_series_from":
            payload = json.loads(data_str) if isinstance(data_str, str) and data_str.strip().startswith("{") else (data if isinstance(data, dict) else {})
            return schedule_cancel_series_from(payload.get("series_id", ""), payload.get("from_date", ""), sid, data_dir)
        return f"Error: unknown operation '{operation}'"

    @tool
    def manager_note(note: str) -> str:
        """Append a non-sensitive working note to MANAGER.md."""
        if not data_dir:
            return "Error: data_dir not configured"
        notes_path = os.path.join(data_dir, "MANAGER.md")
        os.makedirs(os.path.dirname(notes_path) if os.path.dirname(notes_path) else ".", exist_ok=True)
        with open(notes_path, "a", encoding="utf-8") as f:
            f.write(f"\n- {note}")
        return "Note saved."

    @tool
    def manager_todo(operation: str, title: str = "", due: str = "", id: str = "") -> str:
        """
        Manage the user's to-do list.
        Operations:
          list                      — show all todos
          add   title [due]         — add a new todo (due: YYYY-MM-DD, optional)
          complete id               — mark a todo as done
          remove id                 — delete a todo
        """
        op = operation.strip().lower()
        if op == "list":
            return todo_list(sid, data_dir)
        if op == "add":
            if not title:
                return "Error: title is required for add"
            return todo_add(title, due or None, sid, data_dir)
        if op == "complete":
            if not id:
                return "Error: id is required for complete"
            return todo_complete(id, sid, data_dir)
        if op == "remove":
            if not id:
                return "Error: id is required for remove"
            return todo_remove(id, sid, data_dir)
        return f"Error: unknown operation '{operation}'. Use: list, add, complete, remove"

    @tool
    def manager_habit(
        operation: str, name: str = "", id: str = "", checkin_date: str = ""
    ) -> str:
        """
        Track daily habits and streaks.
        Operations:
          list                         — show all habits with current streak
          add   name                   — create a new habit to track
          checkin id [checkin_date]    — mark habit done (date: YYYY-MM-DD, defaults to today)
          streak  id                   — show streak for one habit
          remove  id                   — delete a habit and its history
        """
        op = operation.strip().lower()
        if op == "list":
            return habit_list(pm_db)
        if op == "add":
            if not name:
                return "Error: name is required for add"
            return habit_add(name, pm_db)
        if op == "checkin":
            if not id:
                return "Error: id is required for checkin"
            return habit_checkin(id, pm_db, checkin_date or None)
        if op == "streak":
            if not id:
                return "Error: id is required for streak"
            return habit_streak(id, pm_db)
        if op == "remove":
            if not id:
                return "Error: id is required for remove"
            return habit_remove(id, pm_db)
        return f"Error: unknown operation '{operation}'. Use: list, add, checkin, streak, remove"

    @tool
    def manager_journal(operation: str, body: str = "", limit: int = 5, query: str = "") -> str:
        """
        Private dated journal — personal reflections, mood, end-of-day reviews.
        Operations:
          append  body              — save a new timestamped journal entry
          read   [limit]            — read the last N entries (default 5)
          search  query [limit]     — full-text search across all entries
        Entries are stored privately per-session, never shared externally.
        """
        op = operation.strip().lower()
        if op == "append":
            if not body:
                return "Error: body is required for append"
            return journal_append(body, pm_db)
        if op == "read":
            return journal_read(pm_db, limit=max(1, min(limit, 50)))
        if op == "search":
            if not query:
                return "Error: query is required for search"
            return journal_search(query, pm_db, limit=max(1, min(limit, 50)))
        return f"Error: unknown operation '{operation}'. Use: append, read, search"

    @tool
    def remember(fact: str) -> str:
        """Save a non-sensitive fact about the user to long-term memory (shared vault)."""
        if not vault:
            return "Memory not configured (VAULT_DIR not set)."
        profile_path = os.path.join(vault, "PROFILE.md")
        os.makedirs(vault, exist_ok=True)
        with open(profile_path, "a", encoding="utf-8") as f:
            f.write(f"\n- {fact}")
        return f"Remembered: {fact}"

    @tool
    def web_search(query: str) -> str:
        """Search the web. Keep queries minimal — no personal identifiers."""
        return _web_search(query)

    return [
        manager_private,
        manager_schedule,
        manager_note,
        manager_todo,
        manager_habit,
        manager_journal,
        remember,
        web_search,
    ]


