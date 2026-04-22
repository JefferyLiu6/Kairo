"""Approval preview helpers for Kairo control actions."""
from __future__ import annotations

from typing import Any

from ..persistence.control_store import ApprovalRecord
from ..persistence.store import load_schedule, load_todos


def approval_response(record: ApprovalRecord, data_dir: str, *, include_preview: bool = False) -> dict[str, Any]:
    response = record.model_dump()
    if _payload_should_be_redacted(record.action_type):
        response["payload"] = {"redacted": True}
    if include_preview:
        response["preview"] = build_approval_preview(record, data_dir)
    return response


def build_approval_preview(record: ApprovalRecord, data_dir: str) -> dict[str, Any]:
    """Return a user-facing preview without executing the approval."""
    action_type = record.action_type
    if action_type == "schedule_update":
        return _preview_schedule_update(record, data_dir)
    if action_type == "schedule_remove":
        return _preview_schedule_remove(record, data_dir)
    if action_type == "todo_remove":
        return _preview_todo_remove(record, data_dir)
    if action_type == "private_export":
        return _redacted_preview(
            record,
            "Will export private manager memory after approval.",
            "private_memory",
        )
    if action_type.startswith("private_"):
        return _redacted_preview(
            record,
            "Will update private manager memory after approval.",
            "private_memory",
        )
    if action_type == "web_search_blocked":
        return _redacted_preview(
            record,
            "Sensitive search remains blocked unless explicitly approved.",
            "web_search_query",
        )
    return {
        "kind": action_type,
        "summary": record.summary,
        "before": None,
        "after": None,
        "changes": [],
        "redacted": False,
    }


def _preview_schedule_update(record: ApprovalRecord, data_dir: str) -> dict[str, Any]:
    schedule = load_schedule(record.session_id, data_dir)
    by_id = {entry.id: entry for entry in schedule.entries}
    updates = record.payload.get("updates", [])
    before: list[dict[str, Any]] = []
    after: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []

    for update in updates if isinstance(updates, list) else []:
        if not isinstance(update, dict):
            continue
        entry_id = update.get("id")
        entry = by_id.get(entry_id)
        if entry is None:
            changes.append(
                {
                    "entryId": entry_id,
                    "field": "entry",
                    "before": "missing",
                    "after": "cannot preview",
                }
            )
            continue
        entry_before = entry.model_dump()
        entry_after = {**entry_before, **{k: v for k, v in update.items() if k != "id"}}
        before.append(entry_before)
        after.append(entry_after)
        for key, value in update.items():
            if key == "id":
                continue
            old_value = entry_before.get(key)
            if old_value != value:
                changes.append(
                    {
                        "entryId": entry_id,
                        "title": entry.title,
                        "field": key,
                        "before": old_value,
                        "after": value,
                    }
                )

    return {
        "kind": "schedule_update",
        "summary": record.summary,
        "before": {"entries": before},
        "after": {"entries": after},
        "changes": changes,
        "redacted": False,
    }


def _preview_schedule_remove(record: ApprovalRecord, data_dir: str) -> dict[str, Any]:
    schedule = load_schedule(record.session_id, data_dir)
    ids = set(record.payload.get("ids", []))
    entries = [entry.model_dump() for entry in schedule.entries if entry.id in ids]
    changes = [
        {
            "entryId": entry["id"],
            "title": entry["title"],
            "field": "entry",
            "before": entry["title"],
            "after": None,
        }
        for entry in entries
    ]
    return {
        "kind": "schedule_remove",
        "summary": record.summary,
        "before": {"entries": entries},
        "after": {"entries": []},
        "changes": changes,
        "redacted": False,
    }


def _preview_todo_remove(record: ApprovalRecord, data_dir: str) -> dict[str, Any]:
    todos = load_todos(record.session_id, data_dir)
    target_id = record.payload.get("id")
    query = str(record.payload.get("query", "")).lower()
    matches = [
        item.model_dump()
        for item in todos.items
        if (target_id and item.id == target_id) or (query and query in item.title.lower())
    ]
    return {
        "kind": "todo_remove",
        "summary": record.summary,
        "before": {"items": matches},
        "after": {"items": []},
        "changes": [
            {
                "itemId": item["id"],
                "title": item["title"],
                "field": "item",
                "before": item["title"],
                "after": None,
            }
            for item in matches
        ],
        "redacted": False,
    }


def _redacted_preview(record: ApprovalRecord, summary: str, field: str) -> dict[str, Any]:
    return {
        "kind": record.action_type,
        "summary": summary,
        "before": {"redacted": True},
        "after": {"redacted": True},
        "changes": [
            {
                "field": field,
                "before": "[redacted]",
                "after": "[redacted]",
            }
        ],
        "redacted": True,
    }


def _payload_should_be_redacted(action_type: str) -> bool:
    return action_type.startswith("private_") or action_type == "web_search_blocked"
