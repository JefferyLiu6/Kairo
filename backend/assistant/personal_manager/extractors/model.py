"""Model-extraction support helpers for PM workflows."""
from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Any, Optional

from ..domain.types import PMIntent


def _should_try_model_extraction(config: Optional[Any]) -> bool:
    if config is None:
        return False
    model = str(getattr(config, "model", "") or "").strip()
    provider = str(getattr(config, "provider", "") or "").strip().lower()
    if not model or model == "unused":
        return False
    if provider == "openai":
        return bool(getattr(config, "api_key", None) or os.environ.get("OPENAI_API_KEY", "").strip())
    if provider == "anthropic":
        return bool(getattr(config, "api_key", None) or os.environ.get("ANTHROPIC_API_KEY", "").strip())
    return True


def _format_model_plan_prompt(message: str) -> str:
    intents = ", ".join(intent.value for intent in PMIntent)
    today = date.today().isoformat()
    return f"""\
You extract a personal-manager message into an ordered task plan. You do not
execute actions, call tools, mutate data, search the web, export data, or approve.

Current date: {today}
Allowed intents: {intents}

Return one JSON object with exactly these keys:
- tasks: array of task objects
- global_missing_fields: array
- confidence: number from 0 to 1
- source: "model_structured"

Each task object must contain exactly these keys:
- task_id: stable id like "task-1"
- intent: one allowed intent
- entities: extracted fields only
- confidence: number from 0 to 1
- missing_fields: array
- depends_on: array of task ids
- source: "model_structured"
- reasoning_summary: short non-sensitive parsing note, max 12 words

Use ISO dates (YYYY-MM-DD) and 24-hour times (HH:MM). Split compound requests
into separate tasks, but keep multiple concrete calendar slots in one schedule
creation task using entities.entries. Missing calendar title is allowed; the
workflow will default it. If a required field is ambiguous, include it in the
task's missing_fields and lower confidence.

    IMPORTANT: If the user explicitly states a time (e.g. "12:30pm", "9am", "3 PM"),
    always extract it as the start field — never put "start" or "date" in missing_fields
    when they are explicitly present in the message.

    SAVE_MEMORY — use when the message contains a genuine personal fact worth remembering
    long-term: preferences, dislikes, hobbies, biographical details, regular habits.
    Reason carefully: "I like basketball" → SAVE_MEMORY (confidence 0.90).
    "I might try yoga someday" → UNKNOWN (speculative, confidence too low).
    "I'm going to the gym tomorrow" → CREATE_SCHEDULE_EVENT (action, not a preference).
    "Do I have gym today?" → LIST_STATE (question, not a disclosure).
    Set confidence to reflect how certain you are this is a saveable personal fact.
    operation must be "save_fact". Use sensitive=true for health/finance/relationships.

    For bulk calendar deletes such as "delete breakfast at 8:30 every day next month",
use REMOVE_SCHEDULE_EVENT with entities: query, start, bulk=true, and range_start/range_end
only when the user names a date range. "Remove all Scheduled block 8:30am-9:30am"
should use bulk=true with no range_start/range_end.

User message:
{message}
"""


def _format_model_extraction_prompt(message: str) -> str:
    intents = ", ".join(intent.value for intent in PMIntent)
    today = date.today().isoformat()
    return f"""\
You extract a personal-manager request into JSON. You do not execute actions,
call tools, mutate data, search the web, or bypass approvals.

Current date: {today}
Allowed intents: {intents}

Return one JSON object with exactly these keys:
- intent: one allowed intent
- entities: object with extracted fields only
- confidence: number from 0 to 1
- missing_fields: array of required fields that are absent or ambiguous
- reasoning_summary: short non-sensitive parsing note, max 12 words
- source: "model_structured"

Use ISO dates (YYYY-MM-DD) and 24-hour times (HH:MM). For schedule moves,
use query/id/ordinal/category/reference_date to identify the existing event,
and date/start/end for the destination. If the request is ambiguous, keep the
best intent but lower confidence or add missing_fields.

    IMPORTANT distinctions:
    - SAVE_MEMORY: genuine personal facts — preferences, dislikes, hobbies, biographical
      details, habits. Reason carefully: "I like X" (conf 0.90), "I hate Y" (conf 0.90),
      "I might try X" (conf 0.30 → too low, use UNKNOWN), "schedule me a gym session"
      (action → CREATE_SCHEDULE_EVENT, not SAVE_MEMORY). Set confidence to reflect how
      certain you are this is a saveable long-term personal fact. operation="save_fact".
    - "eat breakfast at 8:30 am everyday", "gym at 7am every weekday" → CREATE_SCHEDULE_EVENT (recurring, has a specific time)
- HABIT_ACTION is only for tracking abstract habits with no fixed time, e.g. "add a habit to drink water", "check in my reading habit"

Recurring events — use these intents when the user refers to a series:
  SKIP_OCCURRENCE     — "skip next week's X", "don't do X this Friday"
                        entities: query (event name), skip_date (YYYY-MM-DD)
  MODIFY_OCCURRENCE   — "move just this Friday's X to 3pm", "only this time"
                        entities: query, original_date (YYYY-MM-DD), start, end
  CANCEL_SERIES_FROM  — "cancel all future X", "stop X from now on"
                        entities: query, from_date (YYYY-MM-DD, default today)
  CREATE_SCHEDULE_EVENT with recurrence — "every weekday", "every Mon and Wed until June"
                        entities include recurrence: {{freq, by_day, interval, until}}
  REMOVE_SCHEDULE_EVENT bulk — "delete breakfast at 8:30 every day next month"
                        entities: query, start, bulk=true, optional range_start/range_end

For LIST_STATE, always include a "target" field in entities:
  "schedule" — when user asks about calendar, schedule, appointments, or events
  "habits"   — when user asks about habits or streaks
  "journal"  — when user asks about journal or logs
  "todos"    — when user asks about tasks, todos, or reminders (default)

User message:
{message}
"""


def _message_content_to_text(raw: Any) -> str:
    content = getattr(raw, "content", raw)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    if isinstance(content, dict):
        return json.dumps(content)
    return str(content)


def _json_object_from_text(text: str) -> Optional[dict[str, Any]]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
