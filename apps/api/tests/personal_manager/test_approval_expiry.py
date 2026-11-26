"""Pending approvals expire: after 24 hours, or sooner once the time the change is about has come."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import time_machine

from kairo.agents.approvals.expiry import approval_deadline
from kairo.agents.approvals.flow import approve_pm_request, reject_pm_request
from kairo.storage.control_store import (
    APPROVAL_TTL,
    EXPIRED_MESSAGE,
    claim_approval_request,
    create_approval_request,
    expire_due_approvals,
    get_approval_request,
    init_control_store,
    list_all_audit_events,
    pm_db_path,
)
from kairo.storage.store import ScheduleData, ScheduleEntry, load_schedule, save_schedule

USER = "expiry-user"
TORONTO = ZoneInfo("America/Toronto")
THURSDAY_10AM = datetime(2026, 11, 12, 10, 0, tzinfo=TORONTO)


def utc(local: datetime) -> datetime:
    return local.astimezone(timezone.utc)


def add(date, start, **extra):
    return ("schedule_add", {"entries": [{"title": "rehearse pitch", "date": date, "start": start, **extra}]})


# ── When an approval expires ──────────────────────────────────────────────────

def test_a_change_with_no_time_of_its_own_expires_after_a_day():
    assert approval_deadline([("todo_remove", {"ids": ["t1"]})], now=THURSDAY_10AM) == utc(THURSDAY_10AM) + APPROVAL_TTL


def test_an_event_that_starts_before_the_day_is_up_expires_when_it_starts(monkeypatch):
    monkeypatch.setenv("KAIRO_TIMEZONE", "America/Toronto")
    deadline = approval_deadline([add("2026-11-12", "15:00")], now=THURSDAY_10AM)
    assert deadline == utc(datetime(2026, 11, 12, 15, 0, tzinfo=TORONTO))


def test_an_event_further_out_than_a_day_keeps_the_day(monkeypatch):
    monkeypatch.setenv("KAIRO_TIMEZONE", "America/Toronto")
    assert approval_deadline([add("2026-11-20", "15:00")], now=THURSDAY_10AM) == utc(THURSDAY_10AM) + APPROVAL_TTL


def test_a_time_already_past_is_not_a_deadline(monkeypatch):
    """Logging something that already happened can still be approved for the full day."""
    monkeypatch.setenv("KAIRO_TIMEZONE", "America/Toronto")
    assert approval_deadline([add("2026-11-12", "08:00")], now=THURSDAY_10AM) == utc(THURSDAY_10AM) + APPROVAL_TTL


def test_a_date_without_a_time_lasts_to_the_end_of_that_day(monkeypatch):
    monkeypatch.setenv("KAIRO_TIMEZONE", "America/Toronto")
    deadline = approval_deadline([("schedule_add_exception", {"series_id": "gym", "skip_date": "2026-11-12"})],
                                 now=THURSDAY_10AM)
    assert deadline == utc(datetime(2026, 11, 12, 23, 59, 59, tzinfo=TORONTO))


def test_a_repeating_event_has_no_single_time_to_expire_at(monkeypatch):
    monkeypatch.setenv("KAIRO_TIMEZONE", "America/Toronto")
    weekly = add("2026-11-12", "15:00", recurrence={"freq": "weekly", "by_day": ["TH"]})
    assert approval_deadline([weekly], now=THURSDAY_10AM) == utc(THURSDAY_10AM) + APPROVAL_TTL


def test_a_batch_expires_at_its_earliest_time(monkeypatch):
    monkeypatch.setenv("KAIRO_TIMEZONE", "America/Toronto")
    moved = ("schedule_update", {"updates": [{"id": "e1", "date": "2026-11-12", "start": "13:00"}]})
    deadline = approval_deadline([add("2026-11-12", "15:00"), moved, ("todo_remove", {"ids": ["t1"]})],
                                 now=THURSDAY_10AM)
    assert deadline == utc(datetime(2026, 11, 12, 13, 0, tzinfo=TORONTO))


# ── What expiring does ────────────────────────────────────────────────────────

def lunch_move(tmp_path, *, expires_at=None):
    save_schedule(ScheduleData(entries=[
        ScheduleEntry(id="lunch001", title="Lunch with Maya", date="2026-11-12", start="12:30", end="13:30"),
    ]), USER, str(tmp_path))
    return create_approval_request(
        USER, str(tmp_path), action_type="schedule_update",
        payload={"updates": [{"id": "lunch001", "start": "13:00", "end": "14:00"}], "_thread_id": "pm-thread"},
        summary="Update schedule event: Lunch with Maya", risk_level="medium", expires_at=expires_at,
    )


def lunch(tmp_path):
    (entry,) = load_schedule(USER, str(tmp_path)).entries
    return entry.start


def test_an_approval_stays_open_until_its_deadline(tmp_path):
    with time_machine.travel(THURSDAY_10AM, tick=False) as clock:
        record = lunch_move(tmp_path)
        assert record.expires_at == (utc(THURSDAY_10AM) + APPROVAL_TTL).isoformat()
        clock.shift(APPROVAL_TTL - timedelta(minutes=1))
        assert expire_due_approvals(USER, str(tmp_path)) == []
        approve_pm_request(record.id, str(tmp_path), session_id=USER)
    assert get_approval_request(USER, str(tmp_path), record.id).status == "executed"
    assert lunch(tmp_path) == "13:00"


def test_approving_after_the_deadline_changes_nothing_and_says_it_expired(tmp_path):
    with time_machine.travel(THURSDAY_10AM, tick=False) as clock:
        record = lunch_move(tmp_path)
        clock.shift(APPROVAL_TTL + timedelta(minutes=1))
        reply = approve_pm_request(record.id, str(tmp_path), session_id=USER)

    assert reply == EXPIRED_MESSAGE
    stored = get_approval_request(USER, str(tmp_path), record.id)
    assert stored.status == "expired" and stored.result == {"ok": False, "message": EXPIRED_MESSAGE}
    assert lunch(tmp_path) == "12:30"                      # the calendar was not touched


def test_an_expired_approval_cannot_be_claimed_or_rejected(tmp_path):
    with time_machine.travel(THURSDAY_10AM, tick=False) as clock:
        record = lunch_move(tmp_path)
        clock.shift(APPROVAL_TTL)
        assert claim_approval_request(str(tmp_path), record.id, session_id=USER) is None
        assert reject_pm_request(record.id, str(tmp_path), session_id=USER) == EXPIRED_MESSAGE
    assert get_approval_request(USER, str(tmp_path), record.id).status == "expired"


def test_expiring_is_recorded_once_in_the_audit_trail(tmp_path):
    with time_machine.travel(THURSDAY_10AM, tick=False) as clock:
        record = lunch_move(tmp_path)
        clock.shift(APPROVAL_TTL + timedelta(hours=1))
        (expired,) = expire_due_approvals(USER, str(tmp_path))
        assert expire_due_approvals(USER, str(tmp_path)) == []          # nothing left to expire

    assert expired.id == record.id
    events = [e for e in list_all_audit_events(USER, str(tmp_path)) if e["eventType"] == "approval_expired"]
    assert len(events) == 1 and events[0]["approvalId"] == record.id


def test_a_decided_approval_is_never_expired(tmp_path):
    with time_machine.travel(THURSDAY_10AM, tick=False) as clock:
        record = lunch_move(tmp_path)
        reject_pm_request(record.id, str(tmp_path), session_id=USER)
        clock.shift(APPROVAL_TTL * 2)
        assert expire_due_approvals(USER, str(tmp_path)) == []
    assert get_approval_request(USER, str(tmp_path), record.id).status == "rejected"


def test_approvals_saved_before_expiry_existed_get_a_day_from_when_they_were_made(tmp_path):
    data_dir = str(tmp_path)
    init_control_store(USER, data_dir)
    db = pm_db_path(USER, data_dir)
    with sqlite3.connect(db) as conn:                      # an older database: no expires_at column
        conn.execute("DROP TABLE approval_requests")
        conn.execute("""CREATE TABLE approval_requests (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                        action_type TEXT NOT NULL, payload_json TEXT NOT NULL, summary TEXT NOT NULL,
                        risk_level TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                        decided_at TEXT, result_json TEXT)""")
        conn.execute("INSERT INTO approval_requests VALUES ('old00001', ?, 'todo_remove', '{}', 'Remove todo', "
                     "'medium', 'pending', ?, NULL, NULL)", (USER, utc(THURSDAY_10AM).isoformat()))

    record = get_approval_request(USER, data_dir, "old00001")
    assert record.status == "pending" and record.expires_at == (utc(THURSDAY_10AM) + APPROVAL_TTL).isoformat()
