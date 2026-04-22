"""
SQLite-backed habit tracker.

Stored at: data/personal-manager/<encoded-session>/pm.db
Tables:
  habits          — one row per habit
  habit_checkins  — one row per (habit, date) check-in
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date, timedelta
from typing import Optional


# ── Connection helper ──────────────────────────────────────────────────────────

def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init(db_path: str) -> None:
    with _conn(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS habits (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS habit_checkins (
                habit_id TEXT NOT NULL,
                date     TEXT NOT NULL,
                PRIMARY KEY (habit_id, date)
            );
            CREATE INDEX IF NOT EXISTS idx_checkins_habit
                ON habit_checkins(habit_id, date DESC);
        """)


# ── Streak calculation ─────────────────────────────────────────────────────────

def _streak(habit_id: str, db_path: str) -> int:
    """
    Count consecutive days ending on today (or yesterday if today not checked).
    This means the streak doesn't break until you miss TWO days.
    """
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT date FROM habit_checkins WHERE habit_id = ? ORDER BY date DESC",
            (habit_id,),
        ).fetchall()

    if not rows:
        return 0

    dates = {date.fromisoformat(r["date"]) for r in rows}
    today = date.today()
    anchor = today if today in dates else today - timedelta(days=1)
    if anchor not in dates:
        return 0

    count = 0
    cur = anchor
    while cur in dates:
        count += 1
        cur -= timedelta(days=1)
    return count


# ── Public operations ──────────────────────────────────────────────────────────

def habit_add(name: str, db_path: str) -> str:
    _init(db_path)
    hid = uuid.uuid4().hex[:8]
    with _conn(db_path) as conn:
        conn.execute(
            "INSERT INTO habits (id, name, created_at) VALUES (?, ?, ?)",
            (hid, name.strip(), date.today().isoformat()),
        )
    return f"OK: habit [{hid}] '{name}' added"


def habit_remove(habit_id: str, db_path: str) -> str:
    _init(db_path)
    with _conn(db_path) as conn:
        row = conn.execute("SELECT id FROM habits WHERE id = ?", (habit_id,)).fetchone()
        if not row:
            return f"Error: habit '{habit_id}' not found"
        conn.execute("DELETE FROM habit_checkins WHERE habit_id = ?", (habit_id,))
        conn.execute("DELETE FROM habits WHERE id = ?", (habit_id,))
    return f"OK: habit [{habit_id}] removed"


def habit_checkin(habit_id: str, db_path: str, checkin_date: Optional[str] = None) -> str:
    _init(db_path)
    d = checkin_date or date.today().isoformat()
    with _conn(db_path) as conn:
        row = conn.execute("SELECT name FROM habits WHERE id = ?", (habit_id,)).fetchone()
        if not row:
            return f"Error: habit '{habit_id}' not found"
        conn.execute(
            "INSERT OR IGNORE INTO habit_checkins (habit_id, date) VALUES (?, ?)",
            (habit_id, d),
        )
        name = row["name"]
    s = _streak(habit_id, db_path)
    return f"OK: checked in '{name}' for {d} — streak: {s} day(s)"


def habit_streak(habit_id: str, db_path: str) -> str:
    _init(db_path)
    with _conn(db_path) as conn:
        row = conn.execute("SELECT name FROM habits WHERE id = ?", (habit_id,)).fetchone()
        if not row:
            return f"Error: habit '{habit_id}' not found"
        name = row["name"]
    s = _streak(habit_id, db_path)
    return f"'{name}' — streak: {s} day(s)"


def habit_list(db_path: str) -> str:
    _init(db_path)
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name FROM habits ORDER BY created_at",
        ).fetchall()
    if not rows:
        return "Habits: (none)"
    lines = ["## Habits"]
    for r in rows:
        s = _streak(r["id"], db_path)
        lines.append(f"- [{r['id']}] {r['name']} — streak: {s}d")
    return "\n".join(lines)


def format_habits_for_context(db_path: str) -> str:
    return habit_list(db_path)
