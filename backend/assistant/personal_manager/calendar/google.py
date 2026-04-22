"""Google Calendar provider for the Kairo calendar service."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from .store import (
    CalendarAccount,
    mark_mirror_deleted,
    save_sync_token,
    update_calendar_tokens,
    upsert_mirror_event,
    wipe_account_mirror,
)
from .types import CalendarEvent, CalendarEventPatch, CalendarSyncResult


GOOGLE_CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
GOOGLE_CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"


class GoogleCalendarDependencyError(RuntimeError):
    pass


def build_google_calendar_service(account: CalendarAccount, data_dir: str):
    """Build an authenticated Google Calendar API client and refresh tokens if needed."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise GoogleCalendarDependencyError(
            "Google Calendar dependencies are not installed. Install "
            "google-api-python-client, google-auth, and google-auth-oauthlib."
        ) from exc

    import os

    client_id = os.environ.get("GOOGLE_CALENDAR_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CALENDAR_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("GOOGLE_CALENDAR_CLIENT_ID and GOOGLE_CALENDAR_CLIENT_SECRET are required")

    creds = Credentials(
        token=account.access_token or None,
        refresh_token=account.refresh_token or None,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=account.scopes.split(),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        update_calendar_tokens(
            account.session_id,
            data_dir,
            account.id,
            access_token=creds.token or "",
            refresh_token=creds.refresh_token,
            token_expiry=creds.expiry.isoformat() if creds.expiry else None,
        )
    return build("calendar", "v3", credentials=creds)


class GoogleCalendarProvider:
    name = "google"

    def __init__(
        self,
        account: CalendarAccount,
        data_dir: str,
        *,
        service: Optional[Any] = None,
    ) -> None:
        self.account = account
        self.data_dir = data_dir
        self.service = service if service is not None else build_google_calendar_service(account, data_dir)

    def sync(self) -> CalendarSyncResult:
        if self.account.sync_token:
            try:
                count = self._incremental_sync()
                return CalendarSyncResult(
                    account_id=self.account.id,
                    provider=self.name,
                    synced=count,
                    full_sync=False,
                )
            except Exception as exc:
                if _status_code(exc) != 410:
                    raise
                wipe_account_mirror(self.account.session_id, self.data_dir, self.account.id)
                save_sync_token(self.account.session_id, self.data_dir, self.account.id, None)
        count = self._initial_sync()
        return CalendarSyncResult(
            account_id=self.account.id,
            provider=self.name,
            synced=count,
            full_sync=True,
        )

    def _initial_sync(self) -> int:
        count = 0
        page_token = None
        while True:
            request = self.service.events().list(
                calendarId=self.account.calendar_id,
                singleEvents=True,
                showDeleted=True,
                maxResults=2500,
                pageToken=page_token,
            )
            response = request.execute()
            count += self._apply_events(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                save_sync_token(
                    self.account.session_id,
                    self.data_dir,
                    self.account.id,
                    response.get("nextSyncToken"),
                )
                return count

    def _incremental_sync(self) -> int:
        count = 0
        page_token = None
        while True:
            request = self.service.events().list(
                calendarId=self.account.calendar_id,
                singleEvents=True,
                showDeleted=True,
                syncToken=self.account.sync_token,
                pageToken=page_token,
            )
            response = request.execute()
            count += self._apply_events(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                save_sync_token(
                    self.account.session_id,
                    self.data_dir,
                    self.account.id,
                    response.get("nextSyncToken"),
                )
                return count

    def _apply_events(self, events: list[dict[str, Any]]) -> int:
        count = 0
        for event in events:
            provider_event_id = str(event.get("id") or "")
            if not provider_event_id:
                continue
            if event.get("status") == "cancelled":
                mark_mirror_deleted(
                    self.account.session_id,
                    self.data_dir,
                    account_id=self.account.id,
                    provider_event_id=provider_event_id,
                )
            else:
                upsert_mirror_event(
                    self.account.session_id,
                    self.data_dir,
                    **google_event_to_mirror_kwargs(event, self.account),
                )
            count += 1
        return count

    def create_event(self, event: CalendarEvent) -> CalendarEvent:
        body = calendar_event_to_google_body(event)
        created = self.service.events().insert(
            calendarId=self.account.calendar_id,
            body=body,
        ).execute()
        mirror = upsert_mirror_event(
            self.account.session_id,
            self.data_dir,
            **google_event_to_mirror_kwargs(created, self.account),
        )
        return CalendarEvent(
            id=mirror.id,
            session_id=mirror.session_id,
            account_id=mirror.account_id,
            provider=mirror.provider,
            provider_event_id=mirror.provider_event_id,
            provider_etag=mirror.provider_etag,
            ical_uid=mirror.ical_uid,
            title=mirror.title,
            start_at=_parse_datetime_or_none(mirror.start_at),
            end_at=_parse_datetime_or_none(mirror.end_at),
            timezone=mirror.timezone,
            status=mirror.status,
            notes=mirror.notes,
            location=mirror.location,
            raw=mirror.raw,
        )

    def update_event(self, provider_event_id: str, patch: CalendarEventPatch) -> CalendarEvent:
        updated = self.service.events().patch(
            calendarId=self.account.calendar_id,
            eventId=provider_event_id,
            body=calendar_patch_to_google_body(patch),
        ).execute()
        mirror = upsert_mirror_event(
            self.account.session_id,
            self.data_dir,
            **google_event_to_mirror_kwargs(updated, self.account),
        )
        return CalendarEvent(
            id=mirror.id,
            session_id=mirror.session_id,
            account_id=mirror.account_id,
            provider=mirror.provider,
            provider_event_id=mirror.provider_event_id,
            provider_etag=mirror.provider_etag,
            ical_uid=mirror.ical_uid,
            title=mirror.title,
            start_at=_parse_datetime_or_none(mirror.start_at),
            end_at=_parse_datetime_or_none(mirror.end_at),
            timezone=mirror.timezone,
            status=mirror.status,
            notes=mirror.notes,
            location=mirror.location,
            raw=mirror.raw,
        )

    def delete_event(self, provider_event_id: str) -> None:
        self.service.events().delete(
            calendarId=self.account.calendar_id,
            eventId=provider_event_id,
        ).execute()
        mark_mirror_deleted(
            self.account.session_id,
            self.data_dir,
            account_id=self.account.id,
            provider_event_id=provider_event_id,
        )


def google_event_to_mirror_kwargs(
    event: dict[str, Any],
    account: CalendarAccount,
) -> dict[str, Any]:
    start = event.get("start") if isinstance(event.get("start"), dict) else {}
    end = event.get("end") if isinstance(event.get("end"), dict) else {}
    return {
        "account_id": account.id,
        "provider": "google",
        "provider_event_id": str(event.get("id") or ""),
        "provider_etag": str(event.get("etag") or ""),
        "ical_uid": str(event.get("iCalUID") or ""),
        "title": str(event.get("summary") or "(untitled)"),
        "start_at": str(start.get("dateTime") or start.get("date") or ""),
        "end_at": str(end.get("dateTime") or end.get("date") or ""),
        "timezone_name": str(start.get("timeZone") or end.get("timeZone") or ""),
        "status": str(event.get("status") or "confirmed"),
        "notes": str(event.get("description") or ""),
        "location": str(event.get("location") or ""),
        "raw": event,
    }


def calendar_event_to_google_body(event: CalendarEvent) -> dict[str, Any]:
    body: dict[str, Any] = {
        "summary": event.title,
        "description": event.notes,
        "location": event.location,
    }
    if event.start_at:
        body["start"] = {
            "dateTime": event.start_at.isoformat(),
            "timeZone": event.timezone,
        }
    if event.end_at:
        body["end"] = {
            "dateTime": event.end_at.isoformat(),
            "timeZone": event.timezone,
        }
    if event.recurrence:
        body["recurrence"] = event.recurrence
    return body


def calendar_patch_to_google_body(patch: CalendarEventPatch) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if patch.title is not None:
        body["summary"] = patch.title
    if patch.notes is not None:
        body["description"] = patch.notes
    if patch.location is not None:
        body["location"] = patch.location
    timezone_name = patch.timezone or "America/Toronto"
    if patch.start_at is not None:
        body["start"] = {"dateTime": patch.start_at.isoformat(), "timeZone": timezone_name}
    if patch.end_at is not None:
        body["end"] = {"dateTime": patch.end_at.isoformat(), "timeZone": timezone_name}
    return body


def _status_code(exc: Exception) -> Optional[int]:
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    if status is not None:
        try:
            return int(status)
        except (TypeError, ValueError):
            return None
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        try:
            return int(status_code)
        except (TypeError, ValueError):
            return None
    return None


def _parse_datetime_or_none(value: str):
    if not value or "T" not in value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
