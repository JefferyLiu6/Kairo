"""Calendar service layer for Kairo."""
from __future__ import annotations

import os
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from .google import GOOGLE_CALENDAR_EVENTS_SCOPE, GoogleCalendarProvider
from .store import (
    CalendarAccount,
    CalendarMirrorEvent,
    list_calendar_accounts,
    list_mirror_events,
    mark_calendar_sync_failed,
    mark_calendar_sync_finished,
    mark_calendar_sync_started,
)
from .types import CalendarEvent, CalendarEventPatch, CalendarSyncResult


class CalendarWriteUnavailableError(RuntimeError):
    pass


_SYNC_LOCKS: dict[str, threading.Lock] = {}
_SYNC_LOCKS_GUARD = threading.Lock()


class CalendarService:
    def __init__(self, session_id: str, data_dir: str) -> None:
        self.session_id = session_id
        self.data_dir = data_dir

    def sync_google_accounts(self) -> list[CalendarSyncResult]:
        results: list[CalendarSyncResult] = []
        accounts = list_calendar_accounts(
            self.session_id,
            self.data_dir,
            provider="google",
            active_only=True,
        )
        for account in accounts:
            result = self._sync_google_account(account, raise_errors=True)
            if result is not None:
                results.append(result)
        return results

    def sync_google_accounts_if_stale(
        self,
        *,
        stale_after_seconds: Optional[int] = None,
    ) -> list[CalendarSyncResult]:
        results: list[CalendarSyncResult] = []
        stale_after = (
            _env_int("GOOGLE_CALENDAR_AUTO_SYNC_STALE_SECONDS", 60)
            if stale_after_seconds is None
            else max(0, stale_after_seconds)
        )
        accounts = list_calendar_accounts(
            self.session_id,
            self.data_dir,
            provider="google",
            active_only=True,
        )
        for account in accounts:
            if not _account_needs_sync(account, stale_after_seconds=stale_after):
                continue
            result = self._sync_google_account(account, raise_errors=False)
            if result is not None:
                results.append(result)
        return results

    def list_events(
        self,
        *,
        start: Optional[date | datetime] = None,
        end: Optional[date | datetime] = None,
        limit: int = 200,
    ) -> list[CalendarMirrorEvent]:
        active_account_ids = {
            account.id
            for account in list_calendar_accounts(
                self.session_id,
                self.data_dir,
                provider="google",
                active_only=True,
            )
        }
        if not active_account_ids:
            return []
        events = list_mirror_events(
            self.session_id,
            self.data_dir,
            start=start,
            end=end,
            limit=limit,
        )
        return [event for event in events if event.account_id in active_account_ids]

    def has_google_write_account(self) -> bool:
        return self._google_write_account() is not None

    def has_google_account(self) -> bool:
        return bool(
            list_calendar_accounts(
                self.session_id,
                self.data_dir,
                provider="google",
                active_only=True,
            )
        )

    def create_google_event_from_entry(self, entry: dict[str, Any]) -> CalendarEvent:
        provider = self._google_write_provider()
        recurrence_rule = entry.get("recurrence")
        # For recurring entries: use the series anchor date (today) if no explicit date
        anchor_date = entry.get("date") or date.today().isoformat()
        start_at = _entry_datetime(anchor_date, entry.get("start"))
        end_at = _entry_datetime(anchor_date, entry.get("end"))
        if start_at is None or end_at is None:
            raise CalendarWriteUnavailableError("Google Calendar writes require a start time and end time")
        rrule_strings = _recurrence_to_rrule(recurrence_rule) if recurrence_rule else []
        event = CalendarEvent(
            session_id=self.session_id,
            account_id=provider.account.id,
            provider="google",
            title=str(entry.get("title") or "Scheduled block"),
            start_at=start_at,
            end_at=end_at,
            timezone=_default_timezone(),
            notes=str(entry.get("notes") or ""),
            location=str(entry.get("location") or ""),
            recurrence=rrule_strings,
        )
        return provider.create_event(event)

    def update_google_event(self, provider_event_id: str, patch: dict[str, Any]) -> CalendarEvent:
        provider = self._google_write_provider()
        timezone_name = _default_timezone()
        start_at = _entry_datetime(patch.get("date"), patch.get("start"))
        end_at = _entry_datetime(patch.get("date"), patch.get("end"))
        event_patch = CalendarEventPatch(
            title=patch.get("title"),
            start_at=start_at,
            end_at=end_at,
            timezone=timezone_name,
            notes=patch.get("notes"),
            location=patch.get("location"),
        )
        return provider.update_event(provider_event_id, event_patch)

    def delete_google_event(self, provider_event_id: str) -> None:
        provider = self._google_write_provider()
        provider.delete_event(provider_event_id)

    def _google_write_provider(self) -> GoogleCalendarProvider:
        account = self._google_write_account()
        if account is None:
            raise CalendarWriteUnavailableError(
                "No active Google Calendar account with write permission is connected"
            )
        return GoogleCalendarProvider(account, self.data_dir)

    def _google_write_account(self):
        accounts = list_calendar_accounts(
            self.session_id,
            self.data_dir,
            provider="google",
            active_only=True,
        )
        for account in accounts:
            if GOOGLE_CALENDAR_EVENTS_SCOPE in account.scopes.split():
                return account
        return None

    def _sync_google_account(
        self,
        account: CalendarAccount,
        *,
        raise_errors: bool,
    ) -> Optional[CalendarSyncResult]:
        lock = _sync_lock(account)
        if not lock.acquire(blocking=False):
            return None
        try:
            mark_calendar_sync_started(self.session_id, self.data_dir, account.id)
            result = GoogleCalendarProvider(account, self.data_dir).sync()
            mark_calendar_sync_finished(self.session_id, self.data_dir, account.id)
            return result
        except Exception as exc:
            next_sync_after = (
                datetime.now(timezone.utc)
                + timedelta(seconds=_env_int("GOOGLE_CALENDAR_SYNC_ERROR_BACKOFF_SECONDS", 300))
            ).isoformat()
            mark_calendar_sync_failed(
                self.session_id,
                self.data_dir,
                account.id,
                error=str(exc),
                next_sync_after=next_sync_after,
            )
            if raise_errors:
                raise
            return None
        finally:
            lock.release()


def is_google_calendar_connected(session_id: str, data_dir: str) -> bool:
    try:
        return CalendarService(session_id, data_dir).has_google_account()
    except Exception:
        return False


def format_google_calendar_for_context(
    session_id: str,
    data_dir: str,
    *,
    days: int = 14,
    limit: int = 40,
) -> str:
    """Return a compact context block from mirrored Google Calendar events."""
    today = date.today()
    end = today + timedelta(days=max(1, days))
    try:
        events = CalendarService(session_id, data_dir).list_events(
            start=today,
            end=end,
            limit=limit,
        )
    except Exception:
        return "Google Calendar: (not connected)"
    if not events:
        accounts = list_calendar_accounts(
            session_id,
            data_dir,
            provider="google",
            active_only=True,
        )
        return "Google Calendar: (connected, no mirrored upcoming events)" if accounts else "Google Calendar: (not connected)"

    lines = ["## Google Calendar"]
    for event in events[:limit]:
        when = _format_when(event)
        lines.append(f"- [google:{event.id}] {event.title} | {when}".strip())
    return "\n".join(lines)


def _format_when(event: CalendarMirrorEvent) -> str:
    start_date, start_time = _split_iso_like(event.start_at)
    end_date, end_time = _split_iso_like(event.end_at)
    tz = f" {event.timezone}" if event.timezone else ""
    if start_time and end_time and start_date == end_date:
        return f"{start_date} {start_time}-{end_time}{tz}"
    if start_time:
        return f"{start_date} {start_time}{tz}"
    if start_date:
        return f"{start_date}{tz}"
    return "unscheduled"


def _split_iso_like(value: str) -> tuple[str, str]:
    if not value:
        return "", ""
    if "T" not in value:
        return value[:10], ""
    date_part, time_part = value.split("T", 1)
    time_part = time_part.replace("Z", "")
    for sep in ("+", "-"):
        if sep in time_part[1:]:
            time_part = time_part.split(sep, 1)[0]
            break
    return date_part[:10], time_part[:5]


def _recurrence_to_rrule(recurrence: Any) -> list[str]:
    """Translate a RecurrenceRule dict (or freq string) to a Google Calendar RRULE string list."""
    if isinstance(recurrence, str):
        recurrence = {"freq": recurrence, "by_day": [], "interval": 1}
    if not isinstance(recurrence, dict):
        return []
    by_day: list[str] = recurrence.get("by_day") or []
    freq: str = (recurrence.get("freq") or "weekly").upper()
    interval: int = int(recurrence.get("interval") or 1)
    until: Optional[str] = recurrence.get("until")

    # Use DAILY when all 7 days are selected or freq is already daily
    if freq == "DAILY" or set(by_day) == {"SU", "MO", "TU", "WE", "TH", "FR", "SA"}:
        parts = ["FREQ=DAILY"]
    else:
        parts = [f"FREQ={freq}"]
        if by_day:
            parts.append(f"BYDAY={','.join(by_day)}")

    if interval > 1:
        parts.append(f"INTERVAL={interval}")
    if until:
        parts.append(f"UNTIL={_rrule_until_end_of_day_utc(until)}")

    return [f"RRULE:{';'.join(parts)}"]


def _rrule_until_end_of_day_utc(until: str) -> str:
    """Convert an inclusive YYYY-MM-DD recurrence end date to Google RRULE UTC UNTIL."""
    text = str(until or "").strip()
    if not text:
        return text
    try:
        end_date = date.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text.replace("-", "")
        return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    try:
        tz = ZoneInfo(_default_timezone())
    except Exception:
        tz = timezone.utc
    local_end = datetime(
        end_date.year,
        end_date.month,
        end_date.day,
        23,
        59,
        59,
        tzinfo=tz,
    )
    return local_end.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _default_timezone() -> str:
    return os.environ.get("GOOGLE_CALENDAR_TIMEZONE", "America/Toronto").strip() or "America/Toronto"


def _entry_datetime(date_value: Any, time_value: Any) -> Optional[datetime]:
    date_text = str(date_value or "").strip()
    time_text = str(time_value or "").strip()
    if not date_text or not time_text:
        return None
    try:
        naive = datetime.fromisoformat(f"{date_text}T{time_text}")
    except ValueError:
        return None
    try:
        return naive.replace(tzinfo=ZoneInfo(_default_timezone()))
    except Exception:
        return naive


def _sync_lock(account: CalendarAccount) -> threading.Lock:
    key = f"{account.session_id}:{account.provider}:{account.id}"
    with _SYNC_LOCKS_GUARD:
        lock = _SYNC_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SYNC_LOCKS[key] = lock
        return lock


def _account_needs_sync(account: CalendarAccount, *, stale_after_seconds: int) -> bool:
    now = datetime.now(timezone.utc)
    next_sync_after = _parse_iso_datetime(account.next_sync_after)
    if next_sync_after is not None and next_sync_after > now:
        return False
    last_sync_at = _parse_iso_datetime(account.last_sync_at)
    if last_sync_at is None:
        return True
    return (now - last_sync_at).total_seconds() >= stale_after_seconds


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default
