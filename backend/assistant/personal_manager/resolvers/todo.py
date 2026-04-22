"""Todo lookup helpers for the personal-manager workflow."""
from __future__ import annotations

from typing import Any

from ..persistence.store import load_todos
from ..parsing.text import _norm


def resolve_todo_id(session_id: str, data_dir: str, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("id"):
        return {"ok": True, "id": payload["id"]}
    query = _norm(payload.get("query", ""))
    if not query:
        return {"ok": False, "message": "I need a task title or ID to find it."}
    matches = [item for item in load_todos(session_id, data_dir).items if query in _norm(item.title)]
    if not matches:
        return {"ok": False, "message": f"Couldn't find a task matching '{query}'."}
    if len(matches) > 1:
        ids = ", ".join(f"{m.id}: {m.title}" for m in matches)
        return {"ok": False, "message": f"Found several tasks matching '{query}' — which one? {ids}"}
    return {"ok": True, "id": matches[0].id}


_resolve_todo_id = resolve_todo_id
