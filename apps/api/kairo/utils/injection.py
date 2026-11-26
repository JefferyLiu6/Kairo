"""Spotting a message that tries to pass itself off as instructions."""
from __future__ import annotations

import re

_INJECTION_PATTERNS = (
    r"\bignore\s+(?:previous|all|prior|any|the)\s+(?:instructions?|rules?|prompts?|directives?|context)\b",
    r"\bdisregard\s+(?:previous|all|prior|the)\s+(?:instructions?|rules?|context)\b",
    r"^\s*system\s*[:：]",
    r"^\s*assistant\s*[:：]",
    r"\bpretend\s+(?:you|i|we)\s+(?:said|are|did|were|had)\b",
    r"\bnew\s+instructions?\s*[:：\-]",
    r"</?(?:system|user|assistant)\s*>",
)


def looks_like_injection(text: str) -> bool:
    lower = text.strip().lower()
    return any(re.search(p, lower) for p in _INJECTION_PATTERNS)
