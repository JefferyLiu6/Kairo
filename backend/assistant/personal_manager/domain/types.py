"""Typed state objects for the personal-manager controlled workflow."""
from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class PMIntent(str, Enum):
    CREATE_TODO = "CREATE_TODO"
    COMPLETE_TODO = "COMPLETE_TODO"
    REMOVE_TODO = "REMOVE_TODO"
    CREATE_SCHEDULE_EVENT = "CREATE_SCHEDULE_EVENT"
    UPDATE_SCHEDULE_EVENT = "UPDATE_SCHEDULE_EVENT"
    REMOVE_SCHEDULE_EVENT = "REMOVE_SCHEDULE_EVENT"
    SKIP_OCCURRENCE = "SKIP_OCCURRENCE"        # skip one date, keep the series
    MODIFY_OCCURRENCE = "MODIFY_OCCURRENCE"    # move/edit just this occurrence
    CANCEL_SERIES_FROM = "CANCEL_SERIES_FROM"  # cancel all future occurrences
    LIST_STATE = "LIST_STATE"
    HABIT_ACTION = "HABIT_ACTION"
    JOURNAL_ACTION = "JOURNAL_ACTION"
    SAVE_MEMORY = "SAVE_MEMORY"
    APPROVE_ACTION = "APPROVE_ACTION"
    REJECT_ACTION = "REJECT_ACTION"
    GENERAL_COACHING = "GENERAL_COACHING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PMAction:
    action_type: str
    payload: dict[str, Any] = dataclass_field(default_factory=dict)
    risk_level: str = "low"
    requires_approval: bool = False
    summary: str = ""


class PMExtraction(BaseModel):
    intent: PMIntent = PMIntent.UNKNOWN
    entities: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    missing_fields: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""
    source: str = "deterministic"


class PMTaskExtraction(BaseModel):
    task_id: str = ""
    intent: PMIntent = PMIntent.UNKNOWN
    entities: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    missing_fields: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    source: str = "deterministic"
    reasoning_summary: str = ""


class PMPlanExtraction(BaseModel):
    tasks: list[PMTaskExtraction] = Field(default_factory=list)
    global_missing_fields: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = "deterministic"


@dataclass
class PMGraphState:
    user_message: str
    session_id: str
    context_snapshot: str = ""
    intent: PMIntent = PMIntent.UNKNOWN
    entities: dict[str, Any] = dataclass_field(default_factory=dict)
    extraction_confidence: float = 0.0
    extraction_source: str = ""
    missing_fields: list[str] = dataclass_field(default_factory=list)
    plan_tasks: list[PMTaskExtraction] = dataclass_field(default_factory=list)
    planned_actions: list[PMAction] = dataclass_field(default_factory=list)
    approval_info: Optional[dict[str, Any]] = None
    tool_results: list[dict[str, Any]] = dataclass_field(default_factory=list)
    final_reply: str = ""
