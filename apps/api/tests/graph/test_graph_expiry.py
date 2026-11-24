"""An approval nobody decided on in time expires: approving it later changes nothing and says so."""
from __future__ import annotations

import sqlite3

from kairo.storage.control_store import EXPIRED_MESSAGE, list_all_audit_events, list_approval_requests, pm_db_path
from kairo.storage.store import ScheduleData, ScheduleEntry, load_schedule, save_schedule

from tests.graph.conftest import USER, call

TO_SCHEDULE = call("hand_off", domain="schedule")


def ask_to_move_the_call(chat):
    save_schedule(ScheduleData(entries=[
        ScheduleEntry(id="disc0001", title="discovery call", date="2026-11-18", start="11:00", end="12:00"),
    ]), USER, chat.data_dir)
    chat.model.extend([TO_SCHEDULE, call("update_event", event_id="disc0001", new_title=None, new_date=None,
                                         new_start="16:00", new_end=None)])
    chat("move the discovery call to 4pm")
    (pending,) = list_approval_requests(USER, chat.data_dir, status="pending")
    return pending


def let_it_lapse(chat, approval_id):
    """Put the deadline before the pinned clock the chat runs on."""
    with sqlite3.connect(pm_db_path(USER, chat.data_dir)) as conn:
        conn.execute("UPDATE approval_requests SET expires_at = '2026-11-12T00:00:00+00:00' WHERE id = ?",
                     (approval_id,))


def call_starts(chat):
    return [e.start for e in load_schedule(USER, chat.data_dir).entries]


def test_a_new_approval_gets_a_deadline(chat):
    pending = ask_to_move_the_call(chat)
    # A day after Thursday 10 AM in Toronto (the chat's clock ticks, so allow a few milliseconds).
    assert pending.expires_at.startswith("2026-11-13T15:00:00")


def test_approving_an_expired_request_by_its_id_changes_nothing(chat):
    pending = ask_to_move_the_call(chat)
    let_it_lapse(chat, pending.id)

    assert chat(f"approve {pending.id}") == EXPIRED_MESSAGE
    assert call_starts(chat) == ["11:00"]
    (expired,) = list_approval_requests(USER, chat.data_dir, status="expired")
    assert expired.id == pending.id
    assert any(e["eventType"] == "approval_expired" and e["approvalId"] == pending.id
               for e in list_all_audit_events(USER, chat.data_dir))


def test_a_bare_approve_after_the_request_expired_says_it_expired(chat):
    pending = ask_to_move_the_call(chat)
    let_it_lapse(chat, pending.id)

    assert chat("approve") == EXPIRED_MESSAGE
    assert call_starts(chat) == ["11:00"]
