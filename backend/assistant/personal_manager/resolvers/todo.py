"""Todo lookup helpers for the personal-manager workflow."""
from __future__ import annotations

from typing import Any

from ..persistence.store import load_todos
from ..parsing.text import _norm


def resolve_todo_id(session_id: str, data_dir: str, payload: dict[str, Any]) -> dict[str, Any]:
    items = load_todos(session_id, data_dir).items
    if payload.get("id"):
        if any(item.id == payload["id"] for item in items):
            return {"ok": True, "id": payload["id"]}
        return {"ok": False, "message": "That task ID no longer exists. Please list your tasks again."}
    query = _norm(payload.get("query", ""))
    if not query:
        return {"ok": False, "message": "I need a task title or ID to find it."}
    matches = [item for item in items if query in _norm(item.title)]
    if not matches:
        return {"ok": False, "message": f"Couldn't find a task matching '{query}'."}
    if len(matches) > 1:
        ids = ", ".join(f"{m.id}: {m.title}" for m in matches)
        return {"ok": False, "message": f"Found several tasks matching '{query}' — which one? {ids}",
                "candidates": [m.model_dump(mode="json") for m in matches]}
    return {"ok": True, "id": matches[0].id}


_resolve_todo_id = resolve_todo_id
