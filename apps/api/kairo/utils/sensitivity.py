"""Whether text touches private topics (health, money, identity, routines)."""
from __future__ import annotations

from kairo.utils.parsing.datetime import _parse_time
from kairo.utils.parsing.text import _norm

_SENSITIVE_TERMS = {
    "medical", "diagnosis", "diagnosed", "therapy", "therapist", "doctor",
    "health", "medication", "bank", "debt", "salary", "tax", "legal",
    "lawyer", "password", "secret", "ssn", "social security", "credit",
    "relationship", "divorce", "diabetes",
    # Personal identity / location / immigration — risky to surface via web search
    "home address", "address", "location history", "immigration", "visa",
    # Unsafe queries — illegal/abusive intent; gated behind approval so the user
    # has to explicitly confirm before we search.
    "hack into", "hacking into", "exploit", "crack into",
    # Government-issued identifiers — surfacing these via web search is risky.
    "license", "driver's license", "drivers license", "passport number",
    # Daily routines → private notes, not shared profile
    "routine", "daily routine", "every morning", "every evening", "every night",
    "every day", "each day", "wake up", "go to bed", "go to sleep",
    "my schedule", "bedtime", "sleep at",
}

_PHYSICAL_ACTIVITY_TERMS = {
    "gym", "run", "workout", "exercise", "jog", "yoga", "training",
}

_FREQUENCY_TERMS = {"always", "usually", "every", "each", "regularly"}


def _contains_sensitive_terms(text: str) -> bool:
    lower = _norm(text)
    if any(term in lower for term in _SENSITIVE_TERMS):
        return True
    # Recurring physical activity with a specific time → treat as private habit
    has_frequency = any(w in lower for w in _FREQUENCY_TERMS)
    has_physical = any(w in lower for w in _PHYSICAL_ACTIVITY_TERMS)
    has_time = bool(_parse_time(text))
    return has_frequency and has_physical and has_time
