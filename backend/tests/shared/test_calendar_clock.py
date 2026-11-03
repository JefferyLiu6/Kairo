import asyncio
from datetime import date, datetime, timezone

import pytest

from assistant.shared import calendar_clock as clock
from assistant.personal_manager.parsing.datetime import _parse_date
from assistant.personal_manager.persistence.store import (
    ScheduleData, ScheduleEntry, RecurrenceRule, save_schedule, get_upcoming_events,
)
from assistant.personal_manager.presentation.formatters import _format_date_natural


@pytest.fixture
def sunday_evening(monkeypatch):
    # Monday on a UTC server, still Sunday evening in Toronto.
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 28, 2, 30, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(clock, "datetime", FrozenDatetime)
    monkeypatch.setenv("KAIRO_TIMEZONE", "America/Toronto")


def test_calendar_dates_use_local_day(sunday_evening, tmp_path):
    assert clock.local_today() == date(2026, 9, 27)
    assert _parse_date("today") == "2026-09-27"
    assert _parse_date("tomorrow") == "2026-09-28"
    assert _format_date_natural("2026-09-28") == "tomorrow"
    save_schedule(ScheduleData(entries=[
        ScheduleEntry(id="run", title="Morning run", date="2026-09-28", start="07:00",
                      recurrence=RecurrenceRule(freq="weekly", by_day=["MO", "WE", "FR"])),
        ScheduleEntry(id="lunch", title="Lunch with Maya", date="2026-09-28", start="12:30"),
    ]), "pm-clock-test", str(tmp_path))
    assert get_upcoming_events("pm-clock-test", str(tmp_path), days=1) == []
    tomorrow = get_upcoming_events("pm-clock-test", str(tmp_path), days=2)
    assert len(tomorrow) == 2
    assert all(event["date"] == "2026-09-28" for event in tomorrow)


def test_timezone_is_isolated_through_stream_and_sync_work(sunday_evening):
    results = {}

    async def app(scope, receive, send):
        name = scope["name"]
        first = clock.local_today()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await asyncio.sleep(0)
        worker_day = await asyncio.to_thread(clock.local_today)
        await send({"type": "http.response.body", "body": b"done"})
        results[name] = (first, clock.local_today(), worker_day)

    async def noop(*args):
        pass

    async def run():
        middleware = clock.CalendarTimezoneMiddleware(app)
        await asyncio.gather(*[
            middleware({"type": "http", "name": name, "headers": [(b"x-timezone", name.encode())]}, noop, noop)
            for name in ["America/Toronto", "Asia/Tokyo", "invalid/timezone"]
        ])
        assert clock._request_zone.get() is None

    asyncio.run(run())
    assert results["America/Toronto"] == (date(2026, 9, 27),) * 3
    assert results["Asia/Tokyo"] == (date(2026, 9, 28),) * 3
    assert results["invalid/timezone"] == (date(2026, 9, 27),) * 3


@pytest.mark.parametrize("instant, expected", [
    (datetime(2026, 3, 8, 4, 30, tzinfo=timezone.utc), date(2026, 3, 7)),
    (datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc), date(2026, 3, 8)),
    (datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc), date(2026, 11, 1)),
    (datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc), date(2026, 11, 1)),
])
def test_calendar_day_across_daylight_saving(monkeypatch, instant, expected):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz)
    monkeypatch.setattr(clock, "datetime", FrozenDatetime)
    monkeypatch.setenv("KAIRO_TIMEZONE", "America/Toronto")
    assert clock.local_today() == expected
