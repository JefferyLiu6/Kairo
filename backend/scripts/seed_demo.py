#!/usr/bin/env python3
"""Seed a demo session with realistic demo data relative to today."""
from __future__ import annotations
import base64
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def seed_session(session_id: str, data_dir: str, vault_dir: str) -> None:
    """Seed demo data for `session_id` into `data_dir`/`vault_dir`."""
    from datetime import date, timedelta
    from assistant.personal_manager.persistence.store import (
        ScheduleData, ScheduleEntry, RecurrenceRule, TodoData, TodoItem,
        save_schedule, save_todos,
    )
    from assistant.personal_manager.persistence.habits import (
        habit_add, habit_checkin, _init as _init_habits,
    )
    from assistant.personal_manager.persistence.journal import (
        journal_append, _init as _init_journal,
    )

    today = date.today()
    tomorrow = today + timedelta(days=1)
    this_friday = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
    next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)

    def fmt(d: date) -> str:
        return d.isoformat()

    # Derive DB path (session_id is the raw frontend ID, e.g. "demo-abc")
    normalized = f"pm-{session_id}"
    encoded = base64.urlsafe_b64encode(normalized.encode()).decode().rstrip("=")
    pm_dir = os.path.join(data_dir, "personal-manager", encoded)
    os.makedirs(pm_dir, exist_ok=True)
    db_path = os.path.join(pm_dir, "pm.db")

    # ── Schedule ─────────────────────────────────────────────────────────────
    schedule = ScheduleData(entries=[
        ScheduleEntry(
            id="aaa00001", title="Morning run",
            start="07:00", end="07:45",
            recurrence=RecurrenceRule(freq="weekly", by_day=["MO", "WE", "FR"]),
            series_id="aaa00001", date=fmt(today),
        ),
        ScheduleEntry(
            id="aaa00002", title="Team standup",
            start="09:30", end="09:50",
            recurrence=RecurrenceRule(freq="weekly", by_day=["MO", "TU", "WE", "TH", "FR"]),
            series_id="aaa00002", date=fmt(today),
        ),
        ScheduleEntry(
            id="aaa00003", title="Lunch with Maya",
            start="12:30", end="13:30", date=fmt(today),
        ),
        ScheduleEntry(
            id="aaa00004", title="Deep work block",
            start="14:00", end="16:00",
            recurrence=RecurrenceRule(freq="weekly", by_day=["TU", "TH"]),
            series_id="aaa00004", date=fmt(today),
        ),
        ScheduleEntry(
            id="aaa00005", title="1:1 with Alex",
            start="10:00", end="10:30", date=fmt(tomorrow),
        ),
        ScheduleEntry(
            id="aaa00006", title="Product demo with investors",
            start="15:00", end="16:00", date=fmt(tomorrow),
        ),
        ScheduleEntry(
            id="aaa00007", title="Weekly review",
            start="17:00", end="17:30",
            recurrence=RecurrenceRule(freq="weekly", by_day=["FR"]),
            series_id="aaa00007", date=fmt(this_friday),
        ),
        ScheduleEntry(
            id="aaa00008", title="Sprint planning",
            start="10:00", end="11:00", date=fmt(next_monday),
        ),
    ])
    save_schedule(schedule, normalized, data_dir)

    # ── Todos ─────────────────────────────────────────────────────────────────
    todos = TodoData(items=[
        TodoItem(id="t0000001", title="Prep slides for investor demo", due=fmt(today)),
        TodoItem(id="t0000002", title="Follow up with Alex on API proposal", due=fmt(tomorrow)),
        TodoItem(id="t0000003", title="Review Q2 roadmap doc", due=fmt(this_friday)),
        TodoItem(id="t0000004", title="Book dentist appointment"),
        TodoItem(id="t0000005", title="Read Atomic Habits chapter 6"),
    ])
    save_todos(todos, normalized, data_dir)

    # ── Habits ────────────────────────────────────────────────────────────────
    _init_habits(db_path)
    habit_ids: dict[str, str] = {}
    for name in ["Morning run 🏃", "Read 30 min 📚", "No phone after 10pm 📵"]:
        result = habit_add(name, db_path)
        m = re.search(r"\[([a-f0-9-]+)\]", result)
        if m:
            habit_ids[name] = m.group(1)

    run_id = habit_ids.get("Morning run 🏃")
    read_id = habit_ids.get("Read 30 min 📚")
    nophone_id = habit_ids.get("No phone after 10pm 📵")
    if run_id:
        for d in [today - timedelta(days=i) for i in [1, 3, 4, 6, 7]]:
            habit_checkin(run_id, db_path, d.isoformat())
    if read_id:
        for d in [today - timedelta(days=i) for i in [0, 1, 2, 3, 5]]:
            habit_checkin(read_id, db_path, d.isoformat())
    if nophone_id:
        for d in [today - timedelta(days=i) for i in [1, 2, 4]]:
            habit_checkin(nophone_id, db_path, d.isoformat())

    # ── Journal ───────────────────────────────────────────────────────────────
    _init_journal(db_path)
    journal_append(
        "Good standup today — the team is aligned on the API timeline. "
        "Need to make sure Alex has what he needs before the 1:1 tomorrow.",
        db_path,
    )
    journal_append(
        "Investor demo is tomorrow. Slides look solid but I want to tighten the opening "
        "2 minutes. The product speaks for itself once they see the live demo.",
        db_path,
    )

    # ── Profile / Memory ──────────────────────────────────────────────────────
    os.makedirs(vault_dir, exist_ok=True)
    profile_path = os.path.join(vault_dir, "PROFILE.md")
    if not os.path.exists(profile_path):
        with open(profile_path, "w", encoding="utf-8") as f:
            f.write("""\
# User Profile

## About
- Name: Alex (demo user)
- Role: Product manager at a mid-stage startup
- Works remotely, prefers async communication

## Preferences
- Prefers morning deep work before meetings (before 10am)
- Likes back-to-back meetings on Tuesdays and Thursdays to keep other days clear
- Takes a lunch break away from the desk — important for energy
- Prefers short, actionable summaries over long explanations
- Ends the day by 6:30pm

## Working style
- Uses time-blocking to protect focus time
- Reviews todos every morning during standup prep
- Weekly review every Friday at 5pm to close out the week

## Current focus
- Preparing for investor demo (high priority this week)
- Improving team communication cadence
- Building consistent exercise and reading habits
""")


if __name__ == "__main__":
    import argparse
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=False))

    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default="demo")
    args, _ = parser.parse_known_args()

    seed_session(
        session_id=args.session,
        data_dir=os.environ.get("DATA_DIR", "./data"),
        vault_dir=os.environ.get("VAULT_DIR", "./vault"),
    )
    print(f"Seeded demo session: {args.session}")
