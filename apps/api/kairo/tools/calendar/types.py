"""Canonical calendar types for Kairo calendar integrations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Protocol

from pydantic import BaseModel, Field


class CalendarEvent(BaseModel):
    """Provider-neutral event shape used by the PM calendar service."""

    id: str = ""
    session_id: str
    account_id: str
    provider: str = "google"
    provider_event_id: str = ""
    provider_etag: str = ""
    ical_uid: str = ""
    title: str
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    timezone: str = "America/Toronto"
    status: str = "confirmed"
    notes: str = ""
    location: str = ""
    recurrence: list[str] = Field(default_factory=list)  # e.g. ["RRULE:FREQ=WEEKLY;BYDAY=MO,WE"]
    raw: dict[str, Any] = Field(default_factory=dict)


class CalendarEventPatch(BaseModel):
    title: Optional[str] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    timezone: Optional[str] = None
    notes: Optional[str] = None
    location: Optional[str] = None


@dataclass(frozen=True)
class CalendarSyncResult:
    account_id: str
    provider: str
    synced: int
    full_sync: bool


class CalendarProvider(Protocol):
    name: str

    def sync(self) -> CalendarSyncResult: ...
    def create_event(self, event: CalendarEvent) -> CalendarEvent: ...
    def update_event(self, provider_event_id: str, patch: CalendarEventPatch) -> CalendarEvent: ...
    def delete_event(self, provider_event_id: str) -> None: ...
