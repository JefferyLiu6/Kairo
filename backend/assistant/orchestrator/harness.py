"""Quality harness — judge PM agent output and manage retry/fallback."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .translator import StructuredAction


@dataclass
class HarnessVerdict:
    verdict: str          # "pass" | "retry" | "fallback"
    confidence: float
    reason: str
    suggested_fix: str
    failure_type: str     # "empty" | "irrelevant" | "read_failed" | "write_failed" | "null"


_EMPTY_PATTERNS = (
    r"todo list:\s*\(empty\)",
    r"nothing on the schedule",
    r"no schedule data",
    r"schedule:\s*\(empty\)",
    r"habits:\s*\(none\)",
    r"^$",
)

_ERROR_PREFIX = "error:"


def _is_empty_output(text: str) -> bool:
    lower = text.strip().lower()
    return any(re.search(p, lower) for p in _EMPTY_PATTERNS)


def _is_error_output(text: str) -> bool:
    return text.strip().lower().startswith(_ERROR_PREFIX)


def _is_irrelevant(intent: str, output: str) -> bool:
    """Detect obvious intent/output mismatches without an LLM call."""
    lower = output.lower()
    if intent == "show_schedule" and "todo list" in lower:
        return True
    if intent in ("show_todos", "add_todo") and "schedule" in lower and "todo" not in lower:
        return True
    return False


def fast_precheck(action: StructuredAction, pm_output: str) -> HarnessVerdict | None:
    """
    Cheap deterministic pre-check before invoking the LLM judge.
    Returns a verdict if the issue is obvious, None if LLM should decide.
    """
    text = pm_output.strip()

    if _is_error_output(text):
        failure = "write_failed" if action.is_write else "read_failed"
        return HarnessVerdict(
            verdict="retry",
            confidence=0.95,
            reason=f"PM agent returned an error: {text[:120]}",
            suggested_fix=action.pm_prompt,
            failure_type=failure,
        )

    if _is_empty_output(text):
        return HarnessVerdict(
            verdict="retry",
            confidence=0.90,
            reason="PM agent returned empty result",
            suggested_fix=action.pm_prompt,
            failure_type="empty",
        )

    if _is_irrelevant(action.intent, text):
        return HarnessVerdict(
            verdict="retry",
            confidence=0.88,
            reason=f"Output type does not match intent '{action.intent}'",
            suggested_fix=action.pm_prompt,
            failure_type="irrelevant",
        )

    return None


def parse_harness_verdict(raw: str) -> HarnessVerdict:
    try:
        match = re.search(r"\{.*\}", raw.strip(), re.DOTALL)
        if match:
            data = json.loads(match.group())
            return HarnessVerdict(
                verdict=str(data.get("verdict", "fallback")),
                confidence=float(data.get("confidence", 0.5)),
                reason=str(data.get("reason", "")),
                suggested_fix=str(data.get("suggested_fix", "")),
                failure_type=str(data.get("failure_type", "null")),
            )
    except (json.JSONDecodeError, AttributeError, ValueError):
        pass
    return HarnessVerdict(
        verdict="fallback",
        confidence=0.0,
        reason="Could not parse harness verdict",
        suggested_fix="",
        failure_type="null",
    )


# ── Fallback reply generation ─────────────────────────────────────────────────

def build_fallback_reply(
    action: StructuredAction,
    verdict: HarnessVerdict,
    cached_snapshot: str | None,
) -> str:
    """
    Rules from the plan:
    1. Never fabricate state-changing success.
    2. Distinguish read vs write failure.
    3. For writes: never guess outcome.
    4. For reads: use cache if available.
    6. Never surface internal error text.
    """
    if action.is_write:
        target = _intent_to_noun(action.intent)
        return (
            f"I couldn't confirm that was saved. "
            f"Please try again or check your {target} directly."
        )

    if cached_snapshot:
        from assistant.orchestrator.agent import _strip_json_blocks
        clean = _strip_json_blocks(cached_snapshot)
        if clean:
            return (
                f"I wasn't able to pull that up fresh right now. "
                f"Here's what I had a moment ago — it may not reflect the latest changes:\n\n"
                f"{clean}"
            )

    return "I wasn't able to retrieve that right now. Please try again in a moment."


def _intent_to_noun(intent: str) -> str:
    mapping = {
        "add_event": "calendar", "update_event": "calendar", "delete_event": "calendar",
        "add_todo": "task list", "update_todo": "task list", "delete_todo": "task list",
        "add_habit": "habits", "habit_checkin": "habits",
        "journal_append": "journal",
        "save_memory": "profile",
    }
    return mapping.get(intent, "data")


# ── Fallback logging ──────────────────────────────────────────────────────────

def log_fallback(
    data_dir: str,
    session_id: str,
    *,
    user_message: str,
    action: StructuredAction,
    pm_output: str,
    verdict: HarnessVerdict,
    retry_count: int,
    fallback_reply: str,
) -> None:
    """Rule 5 — log every fallback to fallback_log.jsonl. Never raises."""
    try:
        from assistant.personal_manager.persistence.store import _pm_dir  # type: ignore
        log_dir = _pm_dir(session_id, data_dir)
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "fallback_log.jsonl")
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "original_user_message": user_message,
            "translated_prompt": action.pm_prompt,
            "structured_action": action.to_log_dict(),
            "pm_output": pm_output,
            "harness_verdict": {
                "verdict": verdict.verdict,
                "confidence": verdict.confidence,
                "reason": verdict.reason,
                "suggested_fix": verdict.suggested_fix,
                "failure_type": verdict.failure_type,
            },
            "retry_count": retry_count,
            "fallback_reply": fallback_reply,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
