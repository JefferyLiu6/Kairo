"""Generic text normalization and identifier parsing."""
from __future__ import annotations

import re

_ID_RE = re.compile(r"\b([a-f0-9]{6,12})\b", re.IGNORECASE)


def _norm(text: str) -> str:
    return " ".join(text.lower().strip().split())


def _extract_id(text: str) -> str:
    match = _ID_RE.search(text)
    return match.group(1) if match else ""

