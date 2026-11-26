"""Strip personal data from text before it is written to a log.

Two passes, line by line:

1. A line that touches a private topic (the same check that decides what is
   stored as a private note) is withheld whole; only its length is kept.
2. Otherwise, identifiers with a recognisable shape (links, email addresses,
   phone, card and ID numbers, postal codes) are replaced by a label.

Names and other free text have no shape to match, so they pass through. A log
that must hold no personal data at all should be switched off instead.
"""
from __future__ import annotations

import json
import re
from typing import Any

from kairo.utils.sensitivity import _contains_sensitive_terms

# Order matters: links and emails first, so the number rules never split them.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"https?://\S+", re.IGNORECASE), "[url]"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    # 13-19 digits in groups of four: payment cards.
    (re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{1,7}\b"), "[card-number]"),
    # 3-3-4 with an optional country code. Needs ten digits, so ISO dates stay readable.
    (re.compile(
        r"(?<![\w-])(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?![\w-])"
    ), "[phone]"),
    # 3-2-4 (SSN) and 3-3-3 (SIN).
    (re.compile(r"\b\d{3}[ -]\d{2}[ -]\d{4}\b|\b\d{3}[ -]\d{3}[ -]\d{3}\b"), "[id-number]"),
    # One or two capitals then digits: passport and licence numbers.
    (re.compile(r"\b[A-Z]{1,2}\d{6,9}\b"), "[id-number]"),
    (re.compile(r"\b[A-Z]\d[A-Z] ?\d[A-Z]\d\b"), "[postal-code]"),
    (re.compile(r"\b\d{9,}\b"), "[number]"),
)


def _redact_line(line: str) -> str:
    if not line.strip():
        return line
    if _contains_sensitive_terms(line):
        return f"[withheld: sensitive, {len(line)} chars]"
    for pattern, label in _PATTERNS:
        line = pattern.sub(label, line)
    return line


def redact_text(text: str) -> str:
    """Return `text` with personal data removed. JSON text keeps its keys."""
    if text.lstrip()[:1] in ("{", "["):
        try:
            return json.dumps(redact(json.loads(text)), ensure_ascii=False, default=str)
        except ValueError:
            pass
    return "\n".join(_redact_line(line) for line in text.split("\n"))


def redact(value: Any) -> Any:
    """Redact every string inside `value`; dict keys, numbers and booleans are kept."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value
