"""When a pending approval stops being approvable.

An approval nobody decides on expires after APPROVAL_TTL (24 hours). A change that is about a
particular time expires sooner, once that time comes: approving "add rehearse pitch, Wed 8 AM" on
Thursday would put an event in the past. Times are the user's local calendar times.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Iterable, Iterator, Optional

from kairo.storage.control_store import APPROVAL_TTL
from kairo.utils.clock import calendar_zone


def approval_deadline(actions: Iterable[tuple[str, dict[str, Any]]], *, now: Optional[datetime] = None) -> datetime:
    """The deadline for an approval holding `actions` ((action_type, payload) pairs), in UTC.

    The earliest of: APPROVAL_TTL from now, and each time a change is about that is still ahead.
    A time already past when the approval is made (logging something that happened) is not a deadline.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    deadline = now + APPROVAL_TTL
    for action_type, payload in actions:
        for moment in _times_referred_to(action_type, payload or {}):
            if now < moment < deadline:
                deadline = moment
    return deadline


def _times_referred_to(action_type: str, payload: dict[str, Any]) -> Iterator[datetime]:
    """The local times a change is about: the new time of an event being added or moved."""
    if action_type == "schedule_add":
        for entry in payload.get("entries") or []:
            # A repeating event has no single time that makes approving it pointless.
            if isinstance(entry, dict) and not entry.get("recurrence") and entry.get("weekday") is None:
                yield from _at(entry.get("date"), entry.get("start"))
    elif action_type == "schedule_update":
        for patch in payload.get("updates") or []:
            if isinstance(patch, dict):
                yield from _at(patch.get("date"), patch.get("start"))
    elif action_type == "schedule_add_exception":
        yield from _at(payload.get("skip_date"), None)
    elif action_type == "schedule_add_override":
        override = payload.get("override") or {}
        if isinstance(override, dict):
            yield from _at(override.get("original_date"), override.get("start"))


def _at(day: Any, start: Any) -> Iterator[datetime]:
    """The local moment for a date and an optional "HH:MM" start; a date alone means the end of that day."""
    try:
        parsed_day = date.fromisoformat(str(day))
    except (TypeError, ValueError):
        return
    clock = time(23, 59, 59)
    if start:
        try:
            clock = time.fromisoformat(str(start))
        except ValueError:
            pass
    yield datetime.combine(parsed_day, clock, tzinfo=calendar_zone()).astimezone(timezone.utc)
