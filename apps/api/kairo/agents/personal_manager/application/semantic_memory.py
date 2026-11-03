"""Semantic-memory interpretation: the model reads the message, the deterministic rules back it up.

The candidate type, the deterministic rules and the save policy live in
kairo.tools.memory.semantic, so the memory executor can use them without the agent.
"""
from __future__ import annotations

from typing import Any

from kairo.agents.personal_manager.contracts import MemoryCandidateOut, MemoryCandidatesOut
from kairo.agents.personal_manager.extractors.model import _should_try_model_extraction, call_pm_model
from kairo.tools.memory.semantic import (
    SemanticMemoryCandidate,
    _clean_memory_object,
    _dedupe_candidates,
    _interpret_deterministic,
)


def interpret_semantic_memory_candidates(message: str, config: Any | None = None) -> list[SemanticMemoryCandidate]:
    """Interpret messy user language into normalized memory candidates."""
    model_candidates = _interpret_with_model(message, config)
    if model_candidates:
        return _dedupe_candidates(model_candidates)
    return _interpret_deterministic(message)


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
