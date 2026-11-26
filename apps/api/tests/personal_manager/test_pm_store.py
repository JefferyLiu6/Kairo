from __future__ import annotations

import json
from datetime import date, timedelta

from kairo.storage.store import RecurrenceRule, ScheduleData, ScheduleEntry, format_schedule_for_context, get_upcoming_events, load_schedule, save_schedule, schedule_replace


def test_an_entry_stored_with_a_weekday_number_is_read_as_a_weekly_repeat(tmp_path):
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
    entry = load_schedule("pm-demo", str(tmp_path)).entries[0]
    assert entry.recurrence == RecurrenceRule(freq="weekly", by_day=["MO"])
    # What is written back holds the repeat and no weekday number.
    stored = json.loads((tmp_path / "users" / "pm-demo" / "schedule.json").read_text())["entries"][0]
    assert "weekday" not in stored and stored["recurrence"]["by_day"] == ["MO"]


def test_a_weekday_number_does_not_replace_a_repeat_the_entry_already_has():
    entry = ScheduleEntry.model_validate(
        {"title": "Gym", "weekday": 1, "recurrence": {"freq": "weekly", "by_day": ["TU", "TH"]}}
    )

    assert entry.recurrence == RecurrenceRule(freq="weekly", by_day=["TU", "TH"])


def test_an_entry_with_no_weekday_number_has_no_repeat():
    assert ScheduleEntry.model_validate({"title": "Dentist", "date": "2026-12-01", "weekday": None}).recurrence is None


def test_a_stored_weekday_number_counts_from_sunday(tmp_path):
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
