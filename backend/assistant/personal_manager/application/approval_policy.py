"""Approval policy for personal-manager actions."""
from __future__ import annotations

from dataclasses import replace

from ..domain.types import PMAction


def apply_approval_policy(action: PMAction) -> PMAction:
    risky = {
        "todo_remove": "medium",
        "schedule_update": "medium",
        "schedule_remove": "medium",
        "schedule_replace": "high",
        "schedule_add_override": "medium",
        "schedule_cancel_series_from": "medium",
        "habit_remove": "medium",
        "private_export": "high",
        "private_patch_profile": "high",
        "web_search_blocked": "high",
    }
    if action.action_type.startswith("private_"):
        return replace(action, requires_approval=True, risk_level="high")
    if action.action_type in risky:
        return replace(action, requires_approval=True, risk_level=risky[action.action_type])
    return action
