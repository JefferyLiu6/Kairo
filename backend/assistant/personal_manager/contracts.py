"""
Typed JSON contracts for the personal manager's model calls.

Model-facing output models are strict: required fields have no defaults, so a
reply that omits `confidence` fails validation instead of silently becoming 1.0
(the internal PMExtraction default, meant for the deterministic parser).
Each `to_*()` converts a validated reply into the type the workflow already uses.

`entities` stays an open object: its shape depends on the intent and is
normalised by extraction._normalize_model_entities.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from assistant.shared.llm import Contract

from .domain.types import PMExtraction, PMIntent, PMPlanExtraction, PMTaskExtraction

MODEL_SOURCE = "model_structured"
Confidence = Annotated[float, Field(ge=0, le=1)]  # required: no default


class MessageInput(Contract):
    """Input for every PM model call: just the user's message, as JSON."""

    message: str


# ── Request and plan extraction ───────────────────────────────────────────────

class ModelRequest(Contract):
    intent: PMIntent
    entities: dict[str, Any] = Field(default_factory=dict)
    confidence: Confidence
    missing_fields: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""
    source: Literal["model_structured"] = MODEL_SOURCE

    def to_extraction(self) -> PMExtraction:
        return PMExtraction(
            intent=self.intent,
            entities=dict(self.entities),
            confidence=self.confidence,
            missing_fields=list(self.missing_fields),
            reasoning_summary=self.reasoning_summary,
            source=MODEL_SOURCE,
        )


class ModelTask(Contract):
    task_id: str
    intent: PMIntent
    entities: dict[str, Any] = Field(default_factory=dict)
    confidence: Confidence
    missing_fields: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    source: Literal["model_structured"] = MODEL_SOURCE
    reasoning_summary: str = ""


class ModelPlan(Contract):
    tasks: list[ModelTask]
    global_missing_fields: list[str] = Field(default_factory=list)
    confidence: Confidence
    source: Literal["model_structured"] = MODEL_SOURCE

    def to_plan(self) -> PMPlanExtraction:
        return PMPlanExtraction(
            tasks=[PMTaskExtraction(**task.model_dump()) for task in self.tasks],
            global_missing_fields=list(self.global_missing_fields),
            confidence=self.confidence,
            source=MODEL_SOURCE,
        )


# ── Semantic memory ───────────────────────────────────────────────────────────

class MemoryCandidateOut(Contract):
    memory_type: Literal[
        "generic_interest", "activity_preference", "schedule_preference", "dislike",
        "constraint", "goal", "identity_fact", "routine",
    ]
    subject: str = "user"
    predicate: Literal["likes", "interested_in", "prefers", "dislikes", "wants_to_improve", "avoids", "reduce"]
    object: str
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    polarity: Literal["positive", "negative", "neutral"]
    confidence: Confidence
    stability: Literal["stable", "tentative"] = "stable"
    scheduling_relevance: Literal["none", "weak", "strong"] = "none"
    sensitivity: Literal["low", "high"] = "low"
    evidence: str = ""


class MemoryCandidatesOut(Contract):
    candidates: list[MemoryCandidateOut]


# ── Passive fact capture ──────────────────────────────────────────────────────

class PassiveFactOut(Contract):
    should_save: bool
    confidence: Confidence
    fact: str
    storage: Literal["profile", "private"]
    reasoning: str = ""
