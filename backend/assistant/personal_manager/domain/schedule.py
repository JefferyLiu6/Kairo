"""First-class schedule target and resolution models."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class GoogleCalendarRef(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = ""
    provider_event_id: str = Field(default="", alias="providerEventId")
    recurring_event_id: str = Field(default="", alias="recurringEventId")
    account_id: str = Field(default="", alias="accountId")
    title: str = ""
    date: str = ""
    start: str = ""
    end: str = ""

    @classmethod
    def from_legacy(cls, value: dict[str, Any]) -> "GoogleCalendarRef":
        return cls.model_validate(value)

    def to_legacy(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


class ScheduleQuery(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = ""
    query: str = ""
    reference_date: str = ""
    range_start: str = ""
    range_end: str = ""
    start: str = ""
    end: str = ""
    ordinal: Optional[int] = None
    category: str = ""
    bulk: bool = False
    original_date: str = ""
    skip_date: str = ""


ScheduleTargetKind = Literal["one_off", "series", "occurrence", "google_mirror"]


class ScheduleTarget(BaseModel):
    kind: ScheduleTargetKind
    id: str
    title: str = ""
    date: str = ""
    start: str = ""
    end: str = ""
    series_id: str = ""
    occurrence_date: str = ""
    google: Optional[GoogleCalendarRef] = None

    @property
    def legacy_id(self) -> str:
        return self.id

    @property
    def legacy_title(self) -> str:
        return self.title


class ScheduleResolution(BaseModel):
    ok: bool
    targets: list[ScheduleTarget] = Field(default_factory=list)
    message: str = ""

    @property
    def ids(self) -> list[str]:
        return [target.legacy_id for target in self.targets]

    @property
    def titles(self) -> list[str]:
        return [target.legacy_title for target in self.targets]

    @property
    def google_events(self) -> list[dict[str, Any]]:
        return [
            target.google.to_legacy()
            for target in self.targets
            if target.google is not None
        ]

    def to_legacy(self) -> dict[str, Any]:
        if not self.ok:
            return {"ok": False, "message": self.message}
        payload: dict[str, Any] = {
            "ok": True,
            "ids": self.ids,
            "titles": self.titles,
        }
        google_events = self.google_events
        if google_events:
            payload["googleEvents"] = google_events
        return payload
