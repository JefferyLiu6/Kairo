"""Habit lookup helpers for the personal-manager workflow."""
from __future__ import annotations

import re
from typing import Any

from ..persistence.habits import habit_list
from ..parsing.text import _norm


def resolve_habit_id(db_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("id"):
        return {"ok": True, "id": payload["id"]}
    query = _norm(payload.get("query") or payload.get("name") or "")
    if not query:
        return {"ok": False, "message": "Error: habit id or name is required"}
    listed = habit_list(db_path)
    matches: list[tuple[str, str]] = []
    for line in listed.splitlines():
        m = re.match(r"- \[([a-f0-9]{8})\] (.+?)(?:\s+[-\u2014]|$)", line)
        if m and query in _norm(m.group(2)):
            matches.append((m.group(1), m.group(2)))
    if not matches:
        return {"ok": False, "message": f"Error: no habit matched '{query}'"}
    if len(matches) > 1:
        return {"ok": False, "message": "Error: multiple habits matched; use the habit id"}
    return {"ok": True, "id": matches[0][0]}


_resolve_habit_id = resolve_habit_id
