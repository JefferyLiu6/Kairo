"""Deterministic schedule parsing, pinned after legacy/ was retired (Phase 2).

legacy/workflow_parsing.py had stale copies of the date helpers: server-clock
dates, no "tdy", and "every morning" not treated as daily. These lock in the
current behaviour so the old copies cannot creep back.
"""
from __future__ import annotations

import pytest

from kairo.utils.parsing.cleanup import _clean_schedule_title
from kairo.utils.parsing.schedule_text import _parse_schedule_entries
from kairo.utils.clock import local_today


def test_relative_dates_use_the_users_timezone_not_the_server_clock(monkeypatch):
    # Kiritimati (UTC+14) is on a different date from the test clock most of the day.
    monkeypatch.setenv("KAIRO_TIMEZONE", "Pacific/Kiritimati")
    [entry] = _parse_schedule_entries("dentist today at 3pm")
    assert entry["date"] == local_today().isoformat()


def test_tdy_is_a_date_and_is_stripped_from_the_title():
    [entry] = _parse_schedule_entries("dentist tdy at 3pm")
    assert entry["date"] == local_today().isoformat()
    assert entry["title"] == "dentist"
    assert entry["start"] == "15:00"


@pytest.mark.parametrize("text, title", [
    ("breakfast every morning at 7am", "breakfast"),
    ("gym every evening at 6pm", "gym"),
])
def test_every_morning_or_evening_is_a_daily_series(text, title):
    [entry] = _parse_schedule_entries(text)
    assert entry["recurrence"]["freq"] == "daily"
    assert entry["title"] == title


@pytest.mark.parametrize("text, title", [
    ("lunch with Sam tomorrow at 12pm", "lunch with Sam"),
    ("morning standup tomorrow at 9am", "morning standup"),
])
def test_time_of_day_words_are_kept_when_they_are_the_title(text, title):
    assert _clean_schedule_title(text) == title
