"""Typed PM action payloads."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kairo.core.domain.types import PMAction


class _CommandModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class TodoAddCommand(_CommandModel):
    title: str
    due: Optional[str] = None


class TodoCompleteCommand(_CommandModel):
    id: str = ""
    query: str = ""


class TodoRemoveCommand(TodoCompleteCommand):
    pass


class RecurrenceCommand(_CommandModel):
    freq: str = "weekly"
    interval: int = 1
    by_day: list[str] = Field(default_factory=list)
    until: Optional[str] = None


class ScheduleEntryCommand(_CommandModel):
    title: str
    date: str = ""
    series_id: str = ""
    recurrence: Optional[RecurrenceCommand] = None
    start: str = ""
    end: str = ""
    notes: str = ""


class ScheduleUpdatePatchCommand(_CommandModel):
    id: str
    title: Optional[str] = None
    date: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    notes: Optional[str] = None


class OccurrenceOverrideCommand(_CommandModel):
    original_date: str
    cancelled: Optional[bool] = None
    title: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("original_date")
    @classmethod
    def _original_date_required(cls, value: str) -> str:
        if not value:
            raise ValueError("schedule occurrence override requires original_date")
        return value


class ScheduleAddCommand(_CommandModel):
    entries: list[ScheduleEntryCommand]

    @field_validator("entries")
    @classmethod
    def _entries_required(cls, value: list[ScheduleEntryCommand]) -> list[ScheduleEntryCommand]:
        if not value:
            raise ValueError("schedule_add requires at least one entry")
        return value


class GoogleCalendarRefCommand(_CommandModel):
    id: str = ""
    provider_event_id: str = Field(default="", alias="providerEventId")
    recurring_event_id: str = Field(default="", alias="recurringEventId")
    account_id: str = Field(default="", alias="accountId")
    title: str = ""
    date: str = ""
    start: str = ""
    end: str = ""

    @model_validator(mode="after")
    def _provider_ref_required(self) -> "GoogleCalendarRefCommand":
        if not self.provider_event_id and not self.id:
            raise ValueError("google calendar ref requires id or providerEventId")
        return self


class ScheduleUpdateCommand(_CommandModel):
    updates: list[ScheduleUpdatePatchCommand]
    google_events: list[GoogleCalendarRefCommand] = Field(default_factory=list, alias="googleEvents")

    @field_validator("updates")
    @classmethod
    def _updates_required(cls, value: list[ScheduleUpdatePatchCommand]) -> list[ScheduleUpdatePatchCommand]:
        if not value:
            raise ValueError("schedule_update requires at least one update")
        return value


class ScheduleRemoveCommand(_CommandModel):
    ids: list[str]
    google_events: list[GoogleCalendarRefCommand] = Field(default_factory=list, alias="googleEvents")

    @field_validator("ids")
    @classmethod
    def _ids_required(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("schedule_remove requires at least one id")
        return value


class ScheduleSkipOccurrenceCommand(_CommandModel):
    series_id: str
    skip_date: str


class ScheduleModifyOccurrenceCommand(_CommandModel):
    series_id: str
    override: OccurrenceOverrideCommand


class ScheduleCancelSeriesFromCommand(_CommandModel):
    series_id: str
    from_date: str


class HabitCommand(_CommandModel):
    operation: str = "list"
    id: str = ""
    query: str = ""
    name: str = ""
    checkin_date: Optional[str] = None


class JournalCommand(_CommandModel):
    operation: Literal["append", "read", "search"] = "append"
    body: str = ""
    query: str = ""


class MemoryCommand(_CommandModel):
    operation: str = "save_fact"
    fact: str = ""
    note: str = ""
    query: str = ""
    sensitive: bool = False


class ExplainCommand(_CommandModel):
    message: str


class ListStateCommand(_CommandModel):
    target: Literal["todos", "schedule", "habits", "journal"] = "todos"


_COMMANDS_BY_ACTION: dict[str, type[_CommandModel]] = {
    "explain": ExplainCommand,
    "todo_add": TodoAddCommand,
    "todo_complete": TodoCompleteCommand,
    "todo_remove": TodoRemoveCommand,
    "schedule_add": ScheduleAddCommand,
    "schedule_update": ScheduleUpdateCommand,
    "schedule_remove": ScheduleRemoveCommand,
    "schedule_add_exception": ScheduleSkipOccurrenceCommand,
    "schedule_add_override": ScheduleModifyOccurrenceCommand,
    "schedule_cancel_series_from": ScheduleCancelSeriesFromCommand,
    "list_state": ListStateCommand,
    "habit_add": HabitCommand,
    "habit_checkin": HabitCommand,
    "habit_streak": HabitCommand,
    "habit_list": HabitCommand,
    "habit_remove": HabitCommand,
    "journal_append": JournalCommand,
    "journal_read": JournalCommand,
    "journal_search": JournalCommand,
    "private_note_append": MemoryCommand,
    "remember": MemoryCommand,
    "web_search_blocked": MemoryCommand,
}


def action_from_command(
    action_type: str,
    command: BaseModel,
    *,
    risk_level: str = "low",
    requires_approval: bool = False,
    summary: str = "",
) -> PMAction:
    return PMAction(
        action_type,
        command.model_dump(mode="json", by_alias=True, exclude_unset=True),
        risk_level=risk_level,
        requires_approval=requires_approval,
        summary=summary,
    )


def validate_action_payload(action: PMAction) -> BaseModel | None:
    command_type = _COMMANDS_BY_ACTION.get(action.action_type)
    if command_type is None:
        return None
    return command_type.model_validate(action.payload)
