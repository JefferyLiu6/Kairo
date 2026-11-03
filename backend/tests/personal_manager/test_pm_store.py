from __future__ import annotations

import json
from datetime import date, timedelta

from assistant.personal_manager.persistence.store import (
    RecurrenceRule,
    ScheduleData,
    ScheduleEntry,
    format_schedule_for_context,
    get_upcoming_events,
    save_schedule,
    schedule_replace,
)


def test_schedule_replace_accepts_schedule_object(tmp_path):
    result = schedule_replace(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {
                        "id": "fixed",
                        "title": "Standup",
                        "date": "",
                        "weekday": 1,
                        "start": "09:00",
                        "end": "09:15",
                        "notes": "",
                    }
                ],
            }
        ),
        "pm-demo",
        str(tmp_path),
    )

    assert result == "OK: schedule replaced with 1 entries"


def test_format_schedule_uses_sunday_zero_mapping(tmp_path):
    schedule_replace(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {
                        "id": "sun",
                        "title": "Sunday review",
                        "date": "",
                        "weekday": 0,
                        "start": "09:00",
                        "end": "10:00",
                        "notes": "",
                    }
                ],
            }
        ),
        "pm-demo",
        str(tmp_path),
    )

    context = format_schedule_for_context("pm-demo", str(tmp_path))
    assert "Sun" in context
    assert "Mon" not in context


def test_upcoming_events_expands_recurrence_rules(tmp_path):
    today = date.today()
    save_schedule(
        ScheduleData(
            entries=[
                ScheduleEntry(
                    id="breakfast",
                    title="Breakfast",
                    date=today.isoformat(),
                    start="08:30",
                    end="09:30",
                    recurrence=RecurrenceRule(
                        freq="daily",
                        interval=1,
                        until=(today + timedelta(days=1)).isoformat(),
                    ),
                )
            ]
        ),
        "pm-demo",
        str(tmp_path),
    )

    events = get_upcoming_events("pm-demo", str(tmp_path), days=2)

    assert [event["date"] for event in events] == [
        today.isoformat(),
        (today + timedelta(days=1)).isoformat(),
    ]
    assert all(event["title"] == "Breakfast" for event in events)
