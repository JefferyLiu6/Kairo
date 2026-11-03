"""
What Kairo is waiting for in a thread — one typed shape per kind of question.

Stored as the working-memory payload, keyed by `type`. Writers build one of these
models (so an incomplete pending state cannot be saved) and readers parse through
`PENDING` (so a corrupt row is detected on load, not as a KeyError mid-turn).

The working-memory mode and expected reply are derived from the kind in
`PENDING_MODES`, instead of each caller passing them separately.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .types import PMIntent, PMPlanExtraction


class _Pending(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Choice(BaseModel):
    """A numbered option shown to the user. Sources add their own scoring fields."""

    model_config = ConfigDict(extra="allow")

    id: str
    label: str
    values: dict[str, Any] = Field(default_factory=dict)


class FreeformPending(_Pending):
    """Waiting for a free-text answer to complete one request."""

    type: Literal["freeform"] = "freeform"
    intent: PMIntent
    entities: dict[str, Any] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)


class PlanPending(_Pending):
    """Waiting for the missing fields of one task in a plan."""

    type: Literal["plan"] = "plan"
    plan: PMPlanExtraction
    blocking_task_id: str
    missing: list[str]


class FieldChoicesPending(_Pending):
    """Waiting for the user to pick one of the numbered options."""

    type: Literal["field_choices"] = "field_choices"
    plan: PMPlanExtraction
    blocking_task_id: str
    missing: list[str]
    choices: list[Choice] = Field(min_length=1)


class DisambiguationPending(_Pending):
    """Waiting for the user to say which activity they meant."""

    type: Literal["disambiguation"] = "disambiguation"
    candidates: list[dict[str, Any]]
    parsed_date: str
    parsed_start: str
    reply: str
    original_message: str


class ConfirmationPending(_Pending):
    """Waiting for yes / no / a correction before acting on an inferred plan."""

    type: Literal["confirmation"] = "confirmation"
    plan: PMPlanExtraction
    blocking_task_id: str
    confirmation_summary: str
    original_message: str
    provenance: dict[str, Any]
    relevance: Optional[dict[str, Any]] = None


class TodoListPending(_Pending):
    """A task list was just shown; "the second one" can refer to it. Not a question."""

    type: Literal["todo_list"] = "todo_list"
    candidates: list[dict[str, Any]]


class TodoSelectionPending(_Pending):
    """Waiting for the user to pick which task a request was about."""

    type: Literal["todo_selection"] = "todo_selection"
    plan: PMPlanExtraction
    candidates: list[dict[str, Any]] = Field(min_length=1)


Pending = Annotated[
    Union[
        FreeformPending, PlanPending, FieldChoicesPending, DisambiguationPending,
        ConfirmationPending, TodoListPending, TodoSelectionPending,
    ],
    Field(discriminator="type"),
]
PENDING: TypeAdapter[Pending] = TypeAdapter(Pending)

# kind -> (working-memory mode, expected reply)
PENDING_MODES: dict[str, tuple[str, str]] = {
    "freeform": ("awaiting_freeform", "freeform_answer"),
    "plan": ("awaiting_clarification", "date_time_or_freeform"),
    "field_choices": ("awaiting_choice", "choice_index_or_freeform"),
    "disambiguation": ("awaiting_disambiguation", "activity_label"),
    "confirmation": ("awaiting_confirmation", "yes_no_or_correction"),
    "todo_list": ("awaiting_disambiguation", "task_id_title_or_number"),
    "todo_selection": ("awaiting_disambiguation", "task_id_title_or_number"),
}


def parse_pending(payload: dict[str, Any]) -> Pending:
    """Validate a stored payload. Rows written before `type` existed are freeform."""
    data = {k: v for k, v in payload.items() if not k.startswith("_")}
    if "type" not in data and "intent" in data:
        data["type"] = "freeform"
    return PENDING.validate_python(data)


def is_question(pending: Pending) -> bool:
    """Whether the user owes an answer; a shown task list is only reference context."""
    return not isinstance(pending, TodoListPending)
