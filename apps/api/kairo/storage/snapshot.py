"""
Everything Kairo has stored for one user, in one plain shape.

Used to read state back after an action (receipts) and by the evals. Each store
maps an item id to the item as a plain dict:

    schedule  title, date, start, end, recurrence, exceptions, overrides, ...
    todos     title, due, done
    habits    name, checkins (dates)
    journal   body
    memory    kind ("fact" or "private_note"), text
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from typing import Any, Iterable, Optional

from kairo.storage.control_store import pm_db_path
from kairo.storage.store import load_private, load_schedule, load_todos

Item = dict[str, Any]
Stores = dict[str, dict[str, Item]]
STORES = ("schedule", "todos", "habits", "journal", "memory")


def read_stores(user_id: str, data_dir: str, *, vault_dir: Optional[str] = None) -> Stores:
    db_path = pm_db_path(user_id, data_dir)
    return {
        "schedule": _by_id(entry.model_dump(mode="json") for entry in load_schedule(user_id, data_dir).entries),
        "todos": _by_id(todo.model_dump(mode="json") for todo in load_todos(user_id, data_dir).items),
        "habits": _by_id(_habits(db_path)),
        "journal": _by_id(_journal(db_path)),
        "memory": _by_id(_memory(user_id, data_dir, vault_dir)),
    }


def _by_id(items: Iterable[Item]) -> dict[str, Item]:
    return {str(item["id"]): item for item in items}


def _rows(db_path: str, sql: str) -> list[sqlite3.Row]:
    """Rows from a table that may not exist yet (a store is created on first write)."""
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _habits(db_path: str) -> list[Item]:
    checkins: dict[str, list[str]] = {}
    for row in _rows(db_path, "SELECT habit_id, date FROM habit_checkins ORDER BY date"):
        checkins.setdefault(row["habit_id"], []).append(row["date"])
    return [{"id": row["id"], "name": row["name"], "checkins": checkins.get(row["id"], [])}
            for row in _rows(db_path, "SELECT id, name FROM habits ORDER BY created_at, id")]


def _journal(db_path: str) -> list[Item]:
    return [{"id": row["id"], "body": row["body"]}
            for row in _rows(db_path, "SELECT id, body FROM journal_entries ORDER BY ts")]


def _memory(user_id: str, data_dir: str, vault_dir: Optional[str]) -> list[Item]:
    """Profile facts (one per PROFILE.md bullet) and private notes. The id is derived from the text.

    PROFILE.md is looked for in the user's own directory and, if one is in use, a separate vault.
    """
    texts: list[tuple[str, str]] = []
    user_dir = os.path.dirname(pm_db_path(user_id, data_dir))
    for directory in dict.fromkeys(filter(None, (user_dir, vault_dir))):
        try:
            with open(os.path.join(directory, "PROFILE.md"), encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            continue
        texts += [("fact", line.strip().lstrip("-*").strip()) for line in lines if line.strip().lstrip("-*").strip()]
    texts += [("private_note", str(note)) for note in load_private(user_id, data_dir).notes_private]
    return [{"id": hashlib.sha256(f"{kind}:{text}".encode()).hexdigest()[:10], "kind": kind, "text": text}
            for kind, text in texts]
