"""
Typed JSON contracts for the orchestrator's model stages.

Each stage has an input model (what the model receives, as JSON) and, when the
model answers in JSON, an output model (what the reply must look like). Output
models are strict and model-facing; `to_action()` / `to_verdict()` turn them into
the internal objects the rest of the pipeline uses.

prompts.py shows each output shape to the model by example; a test keeps those
examples and these models in step.
"""
from __future__ import annotations

from typing import Any, Literal, Optional, get_args

from pydantic import Field, StrictStr, field_validator, model_validator
from pydantic.dataclasses import dataclass

from assistant.shared.llm import Contract

TranslatorIntent = Literal[
    "show_schedule", "add_todo", "update_todo", "complete_todo", "delete_todo",
    "add_event", "update_event", "delete_event", "skip_occurrence",
    "add_habit", "show_habits", "habit_checkin",
    "journal_append", "show_journal", "show_todos", "save_memory", "direct",
]
Timeframe = Literal["today", "tomorrow", "this_week", "next_week", "specific_date"]

TRANSLATOR_INTENTS: tuple[str, ...] = get_args(TranslatorIntent)
TIMEFRAMES: tuple[str, ...] = get_args(Timeframe)


# ── Pipeline objects ──────────────────────────────────────────────────────────
# Validated dataclasses: positional construction still works, invalid values don't.

Verdict = Literal["pass", "retry", "fallback"]
FailureType = Literal["empty", "irrelevant", "read_failed", "write_failed", "null"]


@dataclass
class StructuredAction:
    """What the PM workflow is asked to do. `intent` is open: the pipeline adds its own
    (approval, continue_request) beyond the translator's TranslatorIntent."""

    intent: str
    timeframe: Optional[str]
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


@dataclass
class HarnessVerdict:
    verdict: Verdict
    confidence: float
    reason: str
    suggested_fix: str
    failure_type: FailureType


# ── Router ────────────────────────────────────────────────────────────────────

class RouterInput(Contract):
    message: str
    context: str


class RouterOutput(Contract):
    needs_pm: bool
    reason: str = ""


# ── Translator ────────────────────────────────────────────────────────────────

class TranslatorInput(Contract):
    message: str
    context: str


class TranslatorOutput(Contract):
    intent: TranslatorIntent
    timeframe: Optional[Timeframe] = None
    entities: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    pm_prompt: str = ""
    is_write: bool = False

    @field_validator("timeframe", mode="before")
    @classmethod
    def _null_timeframe(cls, value):
        # The prompt lists "null" as an option; accept it as the JSON null it means.
        return None if value in ("null", "") else value

    def to_action(self) -> StructuredAction:
        return StructuredAction(
            intent=self.intent,
            timeframe=self.timeframe,
            entities=list(self.entities),
            confidence=self.confidence,
            pm_prompt=self.pm_prompt,
            is_write=self.is_write,
        )


# ── Judge (harness) ───────────────────────────────────────────────────────────

class JudgeInput(Contract):
    user_message: str
    intent: str
    pm_output: str
    profile: str


class JudgeOutput(Contract):
    """Every field is required and exactly typed: a malformed verdict must fail closed."""

    verdict: Verdict
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    reason: StrictStr
    suggested_fix: StrictStr
    failure_type: FailureType

    @field_validator("confidence", mode="before")
    @classmethod
    def _number_not_bool(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("confidence must be a number")
        return value

    @model_validator(mode="after")
    def _retry_needs_a_prompt(self):
        if self.verdict == "retry" and not self.suggested_fix.strip():
            raise ValueError("a retry verdict needs a suggested_fix")
        return self

    def to_verdict(self) -> HarnessVerdict:
        return HarnessVerdict(
            verdict=self.verdict,
            confidence=float(self.confidence),
            reason=self.reason,
            suggested_fix=self.suggested_fix,
            failure_type=self.failure_type,
        )


# ── Humanizer ─────────────────────────────────────────────────────────────────

class PersonalizationSources(Contract):
    profile: str
    user_messages: list[str]


class HumanizeInput(Contract):
    user_message: str
    pm_result: str
    personalization_sources: PersonalizationSources
