"""Translate user messages into structured PM agent action objects."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass
class StructuredAction:
    intent: str
    timeframe: str | None
    entities: list[str]
    confidence: float
    pm_prompt: str
    is_write: bool

    def needs_clarification(self) -> bool:
        return self.confidence < 0.70 or self.intent == "direct"

    def cache_key(self) -> str:
        """Which PM cache bucket this action reads from."""
        if "schedule" in self.intent or "event" in self.intent:
            return "schedule"
        if "todo" in self.intent or "task" in self.intent:
            return "todos"
        if "habit" in self.intent:
            return "habits"
        if "journal" in self.intent:
            return "journal"
        return ""

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "timeframe": self.timeframe,
            "entities": self.entities,
            "confidence": self.confidence,
            "pm_prompt": self.pm_prompt,
            "is_write": self.is_write,
        }


def parse_translator_response(raw: str) -> StructuredAction:
    """Parse the LLM translator's JSON output into a StructuredAction."""
    try:
        text = raw.strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return StructuredAction(
                intent=str(data.get("intent", "direct")),
                timeframe=data.get("timeframe") or None,
                entities=list(data.get("entities") or []),
                confidence=float(data.get("confidence", 0.5)),
                pm_prompt=str(data.get("pm_prompt", "")),
                is_write=bool(data.get("is_write", False)),
            )
    except (json.JSONDecodeError, AttributeError, ValueError):
        pass
    return StructuredAction(
        intent="direct",
        timeframe=None,
        entities=[],
        confidence=0.0,
        pm_prompt="",
        is_write=False,
    )


def build_retry_prompt(action: StructuredAction, harness_fix: str) -> StructuredAction:
    """Build a corrected action for retry using the harness suggested_fix."""
    return StructuredAction(
        intent=action.intent,
        timeframe=action.timeframe,
        entities=action.entities,
        confidence=action.confidence,
        pm_prompt=harness_fix or action.pm_prompt,
        is_write=action.is_write,
    )
