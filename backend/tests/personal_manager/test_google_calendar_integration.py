from __future__ import annotations

import importlib
from datetime import date, datetime, timedelta

from assistant.personal_manager.calendar.google import GoogleCalendarProvider
from assistant.personal_manager.calendar.service import CalendarService, format_google_calendar_for_context
from assistant.personal_manager.calendar.store import (
    disconnect_calendar_account,
    get_calendar_account,
    list_calendar_account_sessions,
    list_mirror_events,
    mark_calendar_sync_failed,
    mark_calendar_sync_finished,
    mark_calendar_sync_started,
    mark_mirror_deleted,
    upsert_calendar_account,
    upsert_mirror_event,
)
from assistant.personal_manager.calendar.types import CalendarEvent, CalendarEventPatch, CalendarSyncResult
from assistant.personal_manager.persistence.store import ScheduleData, ScheduleEntry, load_schedule, save_schedule
from assistant.personal_manager.resolvers.schedule import resolve_schedule_targets
from assistant.personal_manager.workflow import (
    execute_pm_action,
    extract_structured_pm_request,
    plan_pm_actions,
)
from assistant.personal_manager.domain.types import PMAction, PMIntent


class _FakeRequest:
    def __init__(self, response_or_exc):
        self._response_or_exc = response_or_exc

    def execute(self):
        if isinstance(self._response_or_exc, Exception):
            raise self._response_or_exc
        return self._response_or_exc


class _FakeEvents:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.insert_calls: list[dict] = []
        self.patch_calls: list[dict] = []
        self.delete_calls: list[dict] = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeRequest(self.responses.pop(0))

    def insert(self, **kwargs):
        self.insert_calls.append(kwargs)
        return _FakeRequest({
            "id": "created1",
            "etag": "etag-created",
            "iCalUID": "ical-created",
            "summary": kwargs["body"]["summary"],
            "status": "confirmed",
            "start": kwargs["body"]["start"],
            "end": kwargs["body"]["end"],
        })

    def patch(self, **kwargs):
        self.patch_calls.append(kwargs)
        return _FakeRequest({
            "id": kwargs["eventId"],
            "etag": "etag-updated",
            "iCalUID": "ical-updated",
            "summary": kwargs["body"].get("summary", "Updated"),
            "status": "confirmed",
            "start": kwargs["body"].get("start", {"dateTime": "2026-04-20T10:00:00-04:00"}),
            "end": kwargs["body"].get("end", {"dateTime": "2026-04-20T11:00:00-04:00"}),
        })

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)
        return _FakeRequest({})


class _FakeService:
    def __init__(self, responses):
        self.events_resource = _FakeEvents(responses)

    def events(self):
        return self.events_resource


class _Resp:
    status = 410


class _GoneError(Exception):
    resp = _Resp()


class _Config:
    def __init__(self, session_id: str, data_dir: str):
        self.session_id = session_id
        self.data_dir = data_dir


def _account(tmp_path, session_id: str = "pm-demo", scopes: list[str] | None = None):
    return upsert_calendar_account(
        session_id,
        str(tmp_path),
        provider="google",
        calendar_id="primary",
        access_token="access",
        refresh_token="refresh",
        token_expiry=None,
        scopes=scopes or ["https://www.googleapis.com/auth/calendar.events.readonly"],
    )


def test_calendar_store_upserts_and_filters_mirror_events(tmp_path):
    account = _account(tmp_path)

    event = upsert_mirror_event(
        "pm-demo",
        str(tmp_path),
        account_id=account.id,
        provider="google",
        provider_event_id="g1",
        title="Dentist appointment",
        start_at="2026-04-20T09:00:00-04:00",
        end_at="2026-04-20T10:00:00-04:00",
        timezone_name="America/Toronto",
        raw={"id": "g1"},
    )

    events = list_mirror_events(
        "pm-demo",
        str(tmp_path),
        start=date(2026, 4, 20),
        end=date(2026, 4, 21),
    )

    assert events == [event]
    mark_mirror_deleted("pm-demo", str(tmp_path), account_id=account.id, provider_event_id="g1")
    assert list_mirror_events("pm-demo", str(tmp_path), start=date(2026, 4, 20), end=date(2026, 4, 21)) == []


def test_calendar_store_tracks_sync_metadata(tmp_path):
    account = _account(tmp_path)

    mark_calendar_sync_started("pm-demo", str(tmp_path), account.id)
    started = get_calendar_account("pm-demo", str(tmp_path), account.id)
    assert started is not None
    assert started.sync_status == "syncing"

    mark_calendar_sync_failed(
        "pm-demo",
        str(tmp_path),
        account.id,
        error="temporary failure",
        next_sync_after="2026-04-20T09:00:00+00:00",
    )
    failed = get_calendar_account("pm-demo", str(tmp_path), account.id)
    assert failed is not None
    assert failed.sync_status == "error"
    assert failed.last_sync_error == "temporary failure"
    assert failed.model_dump()["lastSyncError"] == "temporary failure"

    mark_calendar_sync_finished("pm-demo", str(tmp_path), account.id)
    finished = get_calendar_account("pm-demo", str(tmp_path), account.id)
    assert finished is not None
    assert finished.sync_status == "idle"
    assert finished.last_sync_at is not None
    assert finished.next_sync_after is None
    assert list_calendar_account_sessions(str(tmp_path)) == ["pm-demo"]


def test_calendar_service_auto_sync_runs_once_then_skips_fresh_account(tmp_path, monkeypatch):
    _account(tmp_path)
    calls: list[str] = []

    class FakeProvider:
        def __init__(self, account, data_dir):
            self.account = account
            self.data_dir = data_dir

        def sync(self):
            calls.append(self.account.id)
            return CalendarSyncResult(
                account_id=self.account.id,
                provider="google",
                synced=3,
                full_sync=True,
            )

    calendar_service_module = importlib.import_module("assistant.personal_manager.calendar.service")
    monkeypatch.setattr(calendar_service_module, "GoogleCalendarProvider", FakeProvider)

    first = CalendarService("pm-demo", str(tmp_path)).sync_google_accounts_if_stale(
        stale_after_seconds=60,
    )
    saved = get_calendar_account("pm-demo", str(tmp_path), calls[0])
    assert first[0].synced == 3
    assert saved is not None
    assert saved.last_sync_at is not None
    assert saved.sync_status == "idle"

    calls.clear()
    second = CalendarService("pm-demo", str(tmp_path)).sync_google_accounts_if_stale(
        stale_after_seconds=60,
    )
    assert second == []
    assert calls == []


def test_google_provider_initial_sync_stores_events_and_sync_token(tmp_path):
    account = _account(tmp_path)
    service = _FakeService([
        {
            "items": [
                {
                    "id": "g1",
                    "etag": "etag1",
                    "iCalUID": "ical1",
                    "summary": "Team sync",
                    "status": "confirmed",
                    "start": {"dateTime": "2026-04-20T14:00:00-04:00", "timeZone": "America/Toronto"},
                    "end": {"dateTime": "2026-04-20T14:30:00-04:00", "timeZone": "America/Toronto"},
                }
            ],
            "nextSyncToken": "sync-1",
        }
    ])

    result = GoogleCalendarProvider(account, str(tmp_path), service=service).sync()

    events = list_mirror_events("pm-demo", str(tmp_path))
    saved = get_calendar_account("pm-demo", str(tmp_path), account.id)
    assert result.synced == 1
    assert result.full_sync is True
    assert service.events_resource.calls[0]["showDeleted"] is True
    assert events[0].provider_event_id == "g1"
    assert events[0].title == "Team sync"
    assert saved is not None
    assert saved.sync_token == "sync-1"


def test_google_provider_incremental_sync_updates_and_deletes(tmp_path):
    account = _account(tmp_path)
    upsert_mirror_event(
        "pm-demo",
        str(tmp_path),
        account_id=account.id,
        provider="google",
        provider_event_id="g1",
        title="Old title",
        start_at="2026-04-20T14:00:00-04:00",
        end_at="2026-04-20T14:30:00-04:00",
        timezone_name="America/Toronto",
    )
    upsert_mirror_event(
        "pm-demo",
        str(tmp_path),
        account_id=account.id,
        provider="google",
        provider_event_id="g2",
        title="Cancelled",
        start_at="2026-04-21T14:00:00-04:00",
        end_at="2026-04-21T14:30:00-04:00",
        timezone_name="America/Toronto",
    )
    account = get_calendar_account("pm-demo", str(tmp_path), account.id)
    assert account is not None
    from assistant.personal_manager.calendar.store import save_sync_token

    save_sync_token("pm-demo", str(tmp_path), account.id, "sync-1")
    account = get_calendar_account("pm-demo", str(tmp_path), account.id)
    assert account is not None
    service = _FakeService([
        {
            "items": [
                {
                    "id": "g1",
                    "summary": "New title",
                    "status": "confirmed",
                    "start": {"dateTime": "2026-04-20T15:00:00-04:00"},
                    "end": {"dateTime": "2026-04-20T15:30:00-04:00"},
                },
                {"id": "g2", "status": "cancelled"},
            ],
            "nextSyncToken": "sync-2",
        }
    ])

    result = GoogleCalendarProvider(account, str(tmp_path), service=service).sync()

    events = list_mirror_events("pm-demo", str(tmp_path), include_deleted=True)
    by_provider_id = {event.provider_event_id: event for event in events}
    saved = get_calendar_account("pm-demo", str(tmp_path), account.id)
    assert result.full_sync is False
    assert service.events_resource.calls[0]["syncToken"] == "sync-1"
    assert by_provider_id["g1"].title == "New title"
    assert by_provider_id["g2"].deleted_at is not None
    assert saved is not None
    assert saved.sync_token == "sync-2"


def test_google_provider_410_rebuilds_mirror_with_full_sync(tmp_path):
    account = _account(tmp_path)
    from assistant.personal_manager.calendar.store import save_sync_token

    save_sync_token("pm-demo", str(tmp_path), account.id, "stale")
    upsert_mirror_event(
        "pm-demo",
        str(tmp_path),
        account_id=account.id,
        provider="google",
        provider_event_id="old",
        title="Old",
        start_at="2026-04-20T09:00:00-04:00",
        end_at="2026-04-20T10:00:00-04:00",
        timezone_name="America/Toronto",
    )
    account = get_calendar_account("pm-demo", str(tmp_path), account.id)
    assert account is not None
    service = _FakeService([
        _GoneError(),
        {
            "items": [
                {
                    "id": "new",
                    "summary": "Fresh",
                    "status": "confirmed",
                    "start": {"date": "2026-04-22"},
                    "end": {"date": "2026-04-23"},
                }
            ],
            "nextSyncToken": "fresh-token",
        },
    ])

    result = GoogleCalendarProvider(account, str(tmp_path), service=service).sync()

    events = list_mirror_events("pm-demo", str(tmp_path))
    assert result.full_sync is True
    assert [event.provider_event_id for event in events] == ["new"]


def test_google_provider_create_update_delete_events(tmp_path):
    account = _account(
        tmp_path,
        scopes=["https://www.googleapis.com/auth/calendar.events"],
    )
    service = _FakeService([])
    provider = GoogleCalendarProvider(account, str(tmp_path), service=service)

    created = provider.create_event(
        CalendarEvent(
            session_id="pm-demo",
            account_id=account.id,
            title="Planning",
            start_at=datetime.fromisoformat("2026-04-20T09:00:00-04:00"),
            end_at=datetime.fromisoformat("2026-04-20T10:00:00-04:00"),
            timezone="America/Toronto",
        )
    )
    updated = provider.update_event(
        created.provider_event_id,
        CalendarEventPatch(
            title="Updated planning",
            start_at=datetime.fromisoformat("2026-04-20T11:00:00-04:00"),
            end_at=datetime.fromisoformat("2026-04-20T12:00:00-04:00"),
            timezone="America/Toronto",
        ),
    )
    provider.delete_event(created.provider_event_id)

    assert service.events_resource.insert_calls[0]["calendarId"] == "primary"
    assert service.events_resource.patch_calls[0]["eventId"] == "created1"
    assert service.events_resource.delete_calls[0]["eventId"] == "created1"
    assert updated.title == "Updated planning"
    assert list_mirror_events("pm-demo", str(tmp_path), include_deleted=True)[0].deleted_at is not None


def test_google_rrule_until_includes_final_local_day(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_TIMEZONE", "America/Toronto")
    calendar_service_module = importlib.import_module("assistant.personal_manager.calendar.service")

    rrule = calendar_service_module._recurrence_to_rrule({
        "freq": "daily",
        "by_day": [],
        "interval": 1,
        "until": "2026-05-31",
    })[0]

    assert rrule == "RRULE:FREQ=DAILY;UNTIL=20260601T035959Z"


def test_google_calendar_context_lists_mirrored_events(tmp_path):
    account = _account(tmp_path)
    event_day = date.today() + timedelta(days=1)
    start_at = f"{event_day.isoformat()}T10:00:00-04:00"
    end_at = f"{event_day.isoformat()}T10:45:00-04:00"
    upsert_mirror_event(
        "pm-demo",
        str(tmp_path),
        account_id=account.id,
        provider="google",
        provider_event_id="g1",
        title="Portfolio review",
        start_at=start_at,
        end_at=end_at,
        timezone_name="America/Toronto",
    )

    context = format_google_calendar_for_context("pm-demo", str(tmp_path), days=30)

    assert "## Google Calendar" in context
    assert "Portfolio review" in context
    assert f"{event_day.isoformat()} 10:00-10:45 America/Toronto" in context


def test_calendar_service_hides_events_after_disconnect(tmp_path):
    account = _account(tmp_path)
    upsert_mirror_event(
        "pm-demo",
        str(tmp_path),
        account_id=account.id,
        provider="google",
        provider_event_id="g1",
        title="Hidden after disconnect",
        start_at="2026-04-20T09:00:00-04:00",
        end_at="2026-04-20T09:30:00-04:00",
        timezone_name="America/Toronto",
    )

    assert CalendarService("pm-demo", str(tmp_path)).list_events()
    disconnect_calendar_account("pm-demo", str(tmp_path), account.id)

    assert CalendarService("pm-demo", str(tmp_path)).list_events() == []


def test_pm_schedule_add_writes_to_google_when_write_account_connected(tmp_path, monkeypatch):
    _account(
        tmp_path,
        scopes=["https://www.googleapis.com/auth/calendar.events"],
    )
    created: list[dict] = []

    def fake_create(self, entry):
        created.append(entry)
        return object()

    monkeypatch.setattr(CalendarService, "create_google_event_from_entry", fake_create)

    result = execute_pm_action(
        PMAction(
            "schedule_add",
            {
                "entries": [
                    {
                        "title": "Planning",
                        "date": "2026-04-20",
                        "start": "09:00",
                        "end": "10:00",
                        "notes": "",
                    }
                ]
            },
        ),
        _Config("pm-demo", str(tmp_path)),
    )

    assert result["ok"] is True
    assert "Google Calendar" in result["message"]
    assert created[0]["title"] == "Planning"
    assert load_schedule("pm-demo", str(tmp_path)).entries == []


def test_pm_recurring_schedule_add_writes_to_google_when_write_account_connected(tmp_path, monkeypatch):
    _account(
        tmp_path,
        scopes=["https://www.googleapis.com/auth/calendar.events"],
    )
    created: list[dict] = []

    def fake_create(self, entry):
        created.append(entry)
        return object()

    monkeypatch.setattr(CalendarService, "create_google_event_from_entry", fake_create)

    result = execute_pm_action(
        PMAction(
            "schedule_add",
            {
                "entries": [
                    {
                        "title": "Breakfast",
                        "date": "",
                        "recurrence": {"freq": "daily", "by_day": [], "interval": 1, "until": "2026-05-31"},
                        "start": "08:30",
                        "end": "09:30",
                        "notes": "",
                    }
                ]
            },
        ),
        _Config("pm-demo", str(tmp_path)),
    )

    assert result["ok"] is True
    assert "Google Calendar" in result["message"]
    assert created[0]["recurrence"]["freq"] == "daily"
    assert load_schedule("pm-demo", str(tmp_path)).entries == []


def test_pm_bulk_delete_google_recurring_instances_deletes_parent_once(tmp_path, monkeypatch):
    account = _account(
        tmp_path,
        scopes=["https://www.googleapis.com/auth/calendar.events"],
    )
    for day in range(1, 4):
        upsert_mirror_event(
            "pm-demo",
            str(tmp_path),
            account_id=account.id,
            provider="google",
            provider_event_id=f"series1_202605{day:02d}T083000",
            title="Eat breakfast",
            start_at=f"2026-05-{day:02d}T08:30:00-04:00",
            end_at=f"2026-05-{day:02d}T09:30:00-04:00",
            timezone_name="America/Toronto",
            raw={"recurringEventId": "series1"},
        )
    upsert_mirror_event(
        "pm-demo",
        str(tmp_path),
        account_id=account.id,
        provider="google",
        provider_event_id="series1_20260501T093000",
        title="Eat breakfast",
        start_at="2026-05-01T09:30:00-04:00",
        end_at="2026-05-01T10:30:00-04:00",
        timezone_name="America/Toronto",
        raw={"recurringEventId": "series1"},
    )
    deleted: list[str] = []
    monkeypatch.setattr(CalendarService, "delete_google_event", lambda _self, event_id: deleted.append(event_id))
    extraction = extract_structured_pm_request(
        "delete eat breakfast at 8:30 am everyday next month on calendar",
        _Config("pm-demo", str(tmp_path)),
    )

    actions = plan_pm_actions(extraction.intent, extraction.entities, _Config("pm-demo", str(tmp_path)))
    result = execute_pm_action(actions[0], _Config("pm-demo", str(tmp_path)))

    active_provider_ids = {
        event.provider_event_id
        for event in list_mirror_events("pm-demo", str(tmp_path))
    }
    assert actions[0].action_type == "schedule_remove"
    assert actions[0].summary == "Remove 3 schedule events: Eat breakfast"
    assert len(actions[0].payload["googleEvents"]) == 3
    assert deleted == ["series1"]
    assert result["message"] == "Removed 3 events from Google Calendar."
    assert active_provider_ids == {"series1_20260501T093000"}


def test_pm_schedule_list_prefers_google_when_connected(tmp_path):
    account = _account(
        tmp_path,
        scopes=["https://www.googleapis.com/auth/calendar.events"],
    )
    save_schedule(
        ScheduleData(
            entries=[
                ScheduleEntry(
                    id="local1",
                    title="Old local event",
                    date="2026-04-20",
                    start="09:00",
                    end="10:00",
                )
            ]
        ),
        "pm-demo",
        str(tmp_path),
    )
    upsert_mirror_event(
        "pm-demo",
        str(tmp_path),
        account_id=account.id,
        provider="google",
        provider_event_id="g1",
        title="Google source event",
        start_at="2026-04-20T11:00:00-04:00",
        end_at="2026-04-20T12:00:00-04:00",
        timezone_name="America/Toronto",
    )

    result = execute_pm_action(
        PMAction("list_state", {"target": "schedule"}),
        _Config("pm-demo", str(tmp_path)),
    )

    assert "Google source event" in result["message"]
    assert "Old local event" not in result["message"]


def test_schedule_resolver_returns_google_mirror_target(tmp_path):
    account = _account(
        tmp_path,
        scopes=["https://www.googleapis.com/auth/calendar.events"],
    )
    upsert_mirror_event(
        "pm-demo",
        str(tmp_path),
        account_id=account.id,
        provider="google",
        provider_event_id="g1",
        title="Dentist",
        start_at="2026-04-20T11:00:00-04:00",
        end_at="2026-04-20T12:00:00-04:00",
        timezone_name="America/Toronto",
        raw={"recurringEventId": "series1"},
    )

    resolved = resolve_schedule_targets("pm-demo", str(tmp_path), {"query": "dentist"})

    assert resolved.ok is True
    assert resolved.targets[0].kind == "google_mirror"
    assert resolved.targets[0].google is not None
    assert resolved.targets[0].google.provider_event_id == "g1"
    assert resolved.targets[0].google.recurring_event_id == "series1"


def test_pm_schedule_update_resolves_google_before_local_when_connected(tmp_path):
    account = _account(
        tmp_path,
        scopes=["https://www.googleapis.com/auth/calendar.events"],
    )
    save_schedule(
        ScheduleData(
            entries=[
                ScheduleEntry(
                    id="local1",
                    title="Dentist",
                    date="2026-04-20",
                    start="09:00",
                    end="10:00",
                )
            ]
        ),
        "pm-demo",
        str(tmp_path),
    )
    upsert_mirror_event(
        "pm-demo",
        str(tmp_path),
        account_id=account.id,
        provider="google",
        provider_event_id="g1",
        title="Dentist",
        start_at="2026-04-20T11:00:00-04:00",
        end_at="2026-04-20T12:00:00-04:00",
        timezone_name="America/Toronto",
    )

    actions = plan_pm_actions(
        PMIntent.UPDATE_SCHEDULE_EVENT,
        {"query": "dentist", "date": "2026-04-21", "start": "13:00"},
        _Config("pm-demo", str(tmp_path)),
    )

    assert actions[0].payload["googleEvents"][0]["providerEventId"] == "g1"
    assert actions[0].payload["updates"][0]["id"] != "local1"


def test_google_calendar_accounts_route_redacts_tokens(tmp_path, monkeypatch, authed_client):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    # Create the account under the mock user's id (authed_client uses "test-user-id")
    account = _account(tmp_path, session_id="test-user-id")

    res = authed_client.get("/personal-manager/google-calendar/accounts")

    assert res.status_code == 200
    body = res.json()
    assert body["accounts"][0]["id"] == account.id
    assert "accessToken" not in res.text
    assert "refresh" not in res.text


def test_google_calendar_events_route_returns_mirror_events(tmp_path, monkeypatch, authed_client):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    account = _account(tmp_path, session_id="test-user-id")
    upsert_mirror_event(
        "test-user-id",
        str(tmp_path),
        account_id=account.id,
        provider="google",
        provider_event_id="g1",
        provider_etag="etag1",
        ical_uid="uid1",
        title="Design review",
        start_at="2026-04-20T09:00:00-04:00",
        end_at="2026-04-20T09:30:00-04:00",
        timezone_name="America/Toronto",
        notes="Room 5",
        location="Office",
        raw={"private": "not returned"},
    )

    res = authed_client.get(
        "/personal-manager/google-calendar/events",
        params={"start": "2026-04-20", "end": "2026-04-21"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["events"][0]["title"] == "Design review"
    assert body["events"][0]["providerEventId"] == "g1"
    assert "raw" not in body["events"][0]


def test_google_oauth_flow_disables_auto_pkce_for_web_client(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CALENDAR_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv(
        "GOOGLE_CALENDAR_REDIRECT_URI",
        "http://127.0.0.1:8766/personal-manager/google-calendar/callback",
    )

    app_module = importlib.import_module("assistant.http.pm_app")
    flow = app_module._build_google_oauth_flow(["https://www.googleapis.com/auth/calendar.events"])

    assert flow.autogenerate_code_verifier is False


def test_google_calendar_sync_route_uses_service(tmp_path, monkeypatch, authed_client):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seen: list[str] = []

    def fake_sync(self):
        seen.append(self.session_id)
        return [CalendarSyncResult(account_id="acct1", provider="google", synced=2, full_sync=False)]

    app_module = importlib.import_module("assistant.http.pm_app")
    monkeypatch.setattr(app_module.CalendarService, "sync_google_accounts", fake_sync)

    res = authed_client.post("/personal-manager/google-calendar/sync")

    assert res.status_code == 200
    assert seen == ["test-user-id"]
    assert res.json()["sync"][0]["synced"] == 2


def test_google_calendar_auto_sync_route_uses_stale_gate(tmp_path, monkeypatch, authed_client):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seen: list[tuple[str, int | None]] = []

    def fake_auto_sync(self, *, stale_after_seconds=None):
        seen.append((self.session_id, stale_after_seconds))
        return [CalendarSyncResult(account_id="acct1", provider="google", synced=1, full_sync=False)]

    app_module = importlib.import_module("assistant.http.pm_app")
    monkeypatch.setattr(app_module.CalendarService, "sync_google_accounts_if_stale", fake_auto_sync)

    res = authed_client.post(
        "/personal-manager/google-calendar/auto-sync",
        params={"staleSeconds": "12"},
    )

    assert res.status_code == 200
    assert seen == [("test-user-id", 12)]
    assert res.json()["skipped"] is False
    assert res.json()["sync"][0]["synced"] == 1
