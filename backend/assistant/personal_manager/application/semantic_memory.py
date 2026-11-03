"""Typed semantic-memory interpretation and deterministic save policy."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..contracts import MemoryCandidateOut, MemoryCandidatesOut
from ..extractors.model import _should_try_model_extraction, call_pm_model
from ..parsing.text import _norm
from ..persistence.semantic_memory import SemanticMemoryRecord, upsert_semantic_memory
from .validators import _contains_sensitive_terms


@dataclass(frozen=True)
class SemanticMemoryCandidate:
    memory_type: str
    subject: str
    predicate: str
    object: str
    qualifiers: dict[str, Any]
    polarity: str
    confidence: float
    stability: str
    scheduling_relevance: str
    sensitivity: str
    source: str
    evidence: str


_POSITIVE_INTEREST_PATTERNS = (
    (r"\bi\s+(?:also\s+)?(?:like|love|enjoy|prefer|adore)\s+(.+)$", "likes"),
    (r"\b(?:i'?m|im|i\s+am)\s+(?:also\s+)?(?:interested\s+in|into|fascinated\s+by|curious\s+about|passionate\s+about|obsessed\s+with)\s+(.+)$", "interested_in"),
    (r"\buser\s+(?:likes|enjoys|prefers)\s+(.+)$", "likes"),
    (r"\buser\s+is\s+(?:interested\s+in|into|fascinated\s+by|curious\s+about|passionate\s+about|obsessed\s+with)\s+(.+)$", "interested_in"),
)
_NEGATIVE_PATTERNS = (
    (r"\bi\s+(?:do\s+not|don't|dont)\s+(?:like|enjoy|prefer|care\s+for)\s+(.+)$", "dislikes"),
    (r"\b(?:i'?m|im|i\s+am)\s+not\s+interested\s+in\s+(.+)$", "not_interested_in"),
    (r"\bi\s+(?:hate|dislike|can't\s+stand|cannot\s+stand)\s+(.+)$", "dislikes"),
)
_GOAL_PATTERNS = (
    r"\bi\s+want\s+to\s+(?:get\s+better\s+at|learn|practice|improve)\s+(.+)$",
    r"\b(?:i'?m|im|i\s+am)\s+(?:trying\s+to|working\s+on)\s+(?:get\s+better\s+at|learn|practice|improve)\s+(.+)$",
)
_REDUCE_PATTERNS = (
    r"\bi\s+(?:want|need|am\s+trying|i'?m\s+trying|im\s+trying)\s+to\s+(?:spend\s+)?less\s+(?:time\s+)?(?:on\s+|doing\s+|playing\s+)?(.+)$",
)


def interpret_semantic_memory_candidates(message: str, config: Any | None = None) -> list[SemanticMemoryCandidate]:
    """Interpret messy user language into normalized memory candidates."""
    model_candidates = _interpret_with_model(message, config)
    if model_candidates:
        return _dedupe_candidates(model_candidates)
    return _interpret_deterministic(message)


def save_semantic_memory_candidates(
    session_id: str,
    data_dir: str,
    candidates: list[SemanticMemoryCandidate],
    *,
    min_confidence: float = 0.65,
) -> list[SemanticMemoryRecord]:
    """Persist candidates that pass deterministic policy checks."""
    saved: list[SemanticMemoryRecord] = []
    for candidate in candidates:
        if candidate.confidence < min_confidence:
            continue
        if candidate.sensitivity != "low" or _contains_sensitive_terms(candidate.evidence):
            continue
        if not candidate.object.strip():
            continue
        saved.append(
            upsert_semantic_memory(
                session_id,
                data_dir,
                memory_type=candidate.memory_type,
                subject=candidate.subject,
                predicate=candidate.predicate,
                object_value=candidate.object,
                qualifiers=candidate.qualifiers,
                polarity=candidate.polarity,
                confidence=candidate.confidence,
                stability=candidate.stability,
                scheduling_relevance=candidate.scheduling_relevance,
                sensitivity=candidate.sensitivity,
                source=candidate.source,
                evidence=candidate.evidence,
            )
        )
    return saved


def semantic_profile_fact(candidates: list[SemanticMemoryCandidate], fallback: str) -> str:
    """Return a concise shared-profile fact for saved semantic memory."""
    positive = [candidate for candidate in candidates if candidate.polarity == "positive"]
    if not positive:
        return fallback
    if len(positive) == 1:
        candidate = positive[0]
        if candidate.predicate == "interested_in":
            return f"User is interested in {candidate.object}"
        if candidate.memory_type == "goal":
            return f"User wants to improve {candidate.object}"
        return f"User likes {candidate.object}"
    labels = _format_label_list([candidate.object for candidate in positive])
    return f"User likes {labels}"


def _interpret_deterministic(message: str) -> list[SemanticMemoryCandidate]:
    text = _norm(message)
    if not text:
        return []

    candidates: list[SemanticMemoryCandidate] = []
    for pattern, predicate in _NEGATIVE_PATTERNS:
        match = re.search(pattern, text)
        if match:
            value = _clean_memory_object(match.group(1))
            if value:
                candidates.append(_candidate("dislike", predicate, value, "negative", 0.78, message))
            return candidates

    for pattern in _REDUCE_PATTERNS:
        match = re.search(pattern, text)
        if match:
            value = _clean_memory_object(match.group(1))
            if value:
                candidates.append(_candidate("constraint", "reduce", value, "negative", 0.72, message, stability="tentative"))
            return candidates

    for pattern in _GOAL_PATTERNS:
        match = re.search(pattern, text)
        if match:
            value = _clean_memory_object(match.group(1))
            if value:
                candidates.append(_candidate("goal", "wants_to_improve", value, "positive", 0.74, message, stability="tentative"))
            return candidates

    for pattern, predicate in _POSITIVE_INTEREST_PATTERNS:
        match = re.search(pattern, text)
        if match:
            value = _clean_memory_object(match.group(1))
            if value and not _looks_like_bad_interest_value(value):
                candidates.append(_candidate("generic_interest", predicate, value, "positive", 0.78, message))
            return candidates
    return []


def _interpret_with_model(message: str, config: Any | None) -> list[SemanticMemoryCandidate]:
    if not _should_try_model_extraction(config):
        return []
    reply = call_pm_model("memory_interpret", _format_memory_interpreter_prompt(), message, MemoryCandidatesOut, config)
    if reply is None:
        return []
    candidates = [_candidate_from_model(item, message) for item in reply.candidates]
    return [candidate for candidate in candidates if candidate is not None]


def _candidate_from_model(item: MemoryCandidateOut, fallback_evidence: str) -> SemanticMemoryCandidate | None:
    object_value = _clean_memory_object(item.object)
    if not object_value:
        return None
    return SemanticMemoryCandidate(
        memory_type=item.memory_type,
        subject=item.subject.strip().lower() or "user",
        predicate=item.predicate,
        object=object_value,
        qualifiers=dict(item.qualifiers),
        polarity=item.polarity,
        confidence=item.confidence,
        stability=item.stability,
        scheduling_relevance=item.scheduling_relevance,
        sensitivity=item.sensitivity,
        source="model_memory_interpreter",
        evidence=item.evidence.strip() or fallback_evidence.strip(),
    )


def _candidate(
    memory_type: str,
    predicate: str,
    object_value: str,
    polarity: str,
    confidence: float,
    evidence: str,
    *,
    stability: str = "stable",
) -> SemanticMemoryCandidate:
    return SemanticMemoryCandidate(
        memory_type=memory_type,
        subject="user",
        predicate=predicate,
        object=object_value,
        qualifiers={},
        polarity=polarity,
        confidence=confidence,
        stability=stability,
        scheduling_relevance="none",
        sensitivity="high" if _contains_sensitive_terms(evidence) else "low",
        source="deterministic_memory_interpreter",
        evidence=evidence.strip(),
    )


def _dedupe_candidates(candidates: list[SemanticMemoryCandidate]) -> list[SemanticMemoryCandidate]:
    by_key: dict[tuple[str, str, str], SemanticMemoryCandidate] = {}
    for candidate in candidates:
        key = (candidate.subject, candidate.predicate, candidate.object.lower())
        existing = by_key.get(key)
        if existing is None or candidate.confidence > existing.confidence:
            by_key[key] = candidate
    return list(by_key.values())


def _clean_memory_object(value: str) -> str:
    cleaned = value.strip(" .!?")
    cleaned = re.sub(r"^(?:playing|doing|practicing|to\s+watch|watching)\s+", "", cleaned)
    cleaned = re.sub(r"\s+(?:too|as\s+well)$", "", cleaned)
    return " ".join(cleaned.split())


def _looks_like_bad_interest_value(value: str) -> bool:
    return bool(
        not value
        or re.search(r"\b(?:because of my|injury|injured|pain|less|rarely|not often|used to)\b", value)
        or value.startswith(("to ", "that ", "if ", "when "))
    )




def _format_label_list(labels: list[str]) -> str:
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"


def _format_memory_interpreter_prompt() -> str:
    return """You extract stable personal memory candidates from one user message.

Return JSON only with this shape:
{
  "candidates": [
    {
      "memory_type": "generic_interest | activity_preference | schedule_preference | dislike | constraint | goal | identity_fact | routine",
      "subject": "user",
      "predicate": "likes | interested_in | prefers | dislikes | wants_to_improve | avoids | reduce",
      "object": "short normalized object",
      "qualifiers": {},
      "polarity": "positive | negative | neutral",
      "confidence": 0.0,
      "stability": "stable | tentative",
      "scheduling_relevance": "none | weak | strong",
      "sensitivity": "low | high",
      "evidence": "brief source phrase"
    }
  ]
}

Rules:
- Extract personal facts/preferences/interests/goals only.
- Do not extract commands, todos, calendar actions, or one-off requests.
- Mark medical, financial, legal, relationship, password, and daily routine facts as high sensitivity.
- Use confidence >= 0.75 only for direct current statements like "I'm interested in joking".
- For "used to", "rarely", "trying to do less", or "not interested", do not emit a positive like.

The user's message arrives as JSON: {"message": "..."}
"""
