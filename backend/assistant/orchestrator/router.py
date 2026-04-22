"""Router: decide if PM agent is needed, and predict which PM state to lazy-load."""
from __future__ import annotations

import json
import re

_SCHEDULE_SIGNALS = frozenset([
    "schedule", "calendar", "meeting", "appointment", "event", "today", "tomorrow",
    "this week", "next week", "this month", "coming up", "what do i have",
    "when am i", "when is", "when do i", "what's on", "what is on",
])
_TODO_SIGNALS = frozenset(["todo", "task", "remind", "to-do", "checklist"])
_HABIT_SIGNALS = frozenset(["habit", "streak", "check in", "checkin", "routine"])
_JOURNAL_SIGNALS = frozenset(["journal", "log", "reflection", "daily log"])

_WRITE_SIGNALS = frozenset([
    "add", "create", "new", "book", "schedule", "remind", "remember",
    "delete", "remove", "cancel", "move", "reschedule", "update", "change",
    "push", "shift", "skip", "complete", "done", "finish", "mark",
    "check in", "checkin", "log ",
])


def predict_pm_relevance(message: str, recent_used_pm: bool) -> dict[str, bool]:
    """
    Return which PM data stores are likely needed for this message.
    Used to decide what to lazy-load before the orchestrator LLM call.
    """
    lower = message.lower()
    return {
        "schedule": recent_used_pm or any(s in lower for s in _SCHEDULE_SIGNALS),
        "todos": any(s in lower for s in _TODO_SIGNALS),
        "habits": any(s in lower for s in _HABIT_SIGNALS),
        "journal": any(s in lower for s in _JOURNAL_SIGNALS),
    }


def is_likely_pm_needed(message: str) -> bool:
    """Fast heuristic — true if the message probably needs PM agent."""
    lower = message.lower()
    all_signals = _SCHEDULE_SIGNALS | _TODO_SIGNALS | _HABIT_SIGNALS | _JOURNAL_SIGNALS | _WRITE_SIGNALS
    return any(s in lower for s in all_signals)


def parse_router_verdict(raw: str) -> dict:
    """Parse JSON verdict from the router LLM call."""
    try:
        text = raw.strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except (json.JSONDecodeError, AttributeError):
        pass
    return {"needs_pm": is_likely_pm_needed(raw), "reason": "heuristic fallback"}
