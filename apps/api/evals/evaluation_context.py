"""Typed exporter metadata, supplied outside candidate text; never LLM-extracted here."""

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, model_validator


class EvaluationContext(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    response_available: bool = True
    request_timestamp: str | None = None
    user_timezone: str | None = None
    stored_date: str | None = None

    @model_validator(mode="after")
    def validate_calendar(self):
        if self.request_timestamp is not None:
            instant = datetime.fromisoformat(self.request_timestamp.replace("Z", "+00:00"))
            if instant.tzinfo is None or instant.utcoffset() is None:
                raise ValueError("Request timestamp requires a UTC offset")
        if self.user_timezone is not None:
            try:
                ZoneInfo(self.user_timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("Unknown user timezone") from exc
        if self.stored_date is not None:
            parsed = date.fromisoformat(self.stored_date)
            if parsed.isoformat() != self.stored_date:
                raise ValueError("Stored date requires YYYY-MM-DD")
        return self


def validate_context(value: dict | None, response: str) -> EvaluationContext | None:
    if value is None:
        return None  # Legacy records retain the old path.
    if not isinstance(response, str):
        raise ValueError("Candidate response must be a string")
    context = EvaluationContext.model_validate(value)
    if not context.response_available and response:
        raise ValueError("Unavailable response must have empty candidate text")
    if context.response_available and not response.strip():
        raise ValueError("Available response must contain candidate text")
    return context
