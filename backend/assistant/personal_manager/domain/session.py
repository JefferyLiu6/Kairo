"""Session helpers for personal-manager workflows."""
from __future__ import annotations


def normalize_pm_session_id(session_id: str) -> str:
    return session_id if session_id.startswith("pm-") else f"pm-{session_id}"
