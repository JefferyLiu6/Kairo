"""Google OAuth tokens: encrypted at rest, revoked and wiped on disconnect."""
from __future__ import annotations

import sqlite3

import httpx
import pytest
from cryptography.fernet import Fernet

from kairo.storage.calendar_store import (
    CalendarTokenKeyError,
    disconnect_calendar_account,
    get_calendar_account,
    init_calendar_store,
    list_mirror_events,
    save_sync_token,
    update_calendar_tokens,
    upsert_calendar_account,
    upsert_mirror_event,
)
from kairo.storage.control_store import pm_db_path
from kairo.tools.calendar import google
from kairo.tools.calendar.service import CalendarService

USER = "user-1"


def _connect(tmp_path, user: str = USER):
    return upsert_calendar_account(
        user, str(tmp_path), provider="google", calendar_id="primary",
        access_token="ya29.access-secret", refresh_token="1//refresh-secret",
        token_expiry=None, scopes=[google.GOOGLE_CALENDAR_READONLY_SCOPE],
    )


def _stored(tmp_path, user: str = USER) -> sqlite3.Row:
    conn = sqlite3.connect(pm_db_path(user, str(tmp_path)))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM calendar_accounts").fetchone()
    finally:
        conn.close()


def _write_raw(tmp_path, user: str = USER, **columns) -> None:
    conn = sqlite3.connect(pm_db_path(user, str(tmp_path)))
    with conn:
        assignments = ", ".join(f"{name} = ?" for name in columns)
        conn.execute(f"UPDATE calendar_accounts SET {assignments}", tuple(columns.values()))
    conn.close()


def _database_bytes(tmp_path, user: str = USER) -> bytes:
    path = pm_db_path(user, str(tmp_path))
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    with open(path, "rb") as handle:
        return handle.read()


def test_tokens_are_encrypted_in_the_database_and_plain_when_loaded(tmp_path):
    account = _connect(tmp_path)

    assert account.access_token == "ya29.access-secret"
    assert account.refresh_token == "1//refresh-secret"
    row = _stored(tmp_path)
    assert row["access_token"].startswith("enc:")
    assert row["refresh_token"].startswith("enc:")
    raw = _database_bytes(tmp_path)
    assert b"access-secret" not in raw and b"refresh-secret" not in raw


def test_tokens_are_not_part_of_what_the_api_returns(tmp_path):
    dumped = str(_connect(tmp_path).model_dump())
    assert "secret" not in dumped and "enc:" not in dumped


def test_a_refresh_without_a_new_refresh_token_keeps_the_old_one(tmp_path):
    account = _connect(tmp_path)
    update_calendar_tokens(USER, str(tmp_path), account.id, access_token="ya29.second")

    loaded = get_calendar_account(USER, str(tmp_path), account.id)
    assert loaded.access_token == "ya29.second"
    assert loaded.refresh_token == "1//refresh-secret"
    assert _stored(tmp_path)["access_token"].startswith("enc:")


def test_rows_written_before_encryption_are_encrypted_when_the_store_opens(tmp_path):
    account = _connect(tmp_path)
    _write_raw(tmp_path, access_token="ya29.old-plain", refresh_token="1//old-plain")

    loaded = get_calendar_account(USER, str(tmp_path), account.id)

    assert (loaded.access_token, loaded.refresh_token) == ("ya29.old-plain", "1//old-plain")
    row = _stored(tmp_path)
    assert row["access_token"].startswith("enc:") and row["refresh_token"].startswith("enc:")


def test_without_a_key_nothing_new_is_stored_and_old_rows_stay_readable(tmp_path, monkeypatch):
    account = _connect(tmp_path)
    _write_raw(tmp_path, access_token="ya29.old-plain", refresh_token="1//old-plain")
    monkeypatch.delenv("AUTH_ENCRYPTION_KEY")

    with pytest.raises(CalendarTokenKeyError):
        _connect(tmp_path, user="user-2")
    with pytest.raises(CalendarTokenKeyError):
        update_calendar_tokens(USER, str(tmp_path), account.id, access_token="ya29.new")

    loaded = get_calendar_account(USER, str(tmp_path), account.id)
    assert (loaded.access_token, loaded.refresh_token) == ("ya29.old-plain", "1//old-plain")


def test_tokens_under_a_lost_key_read_as_missing_instead_of_failing(tmp_path, monkeypatch):
    account = _connect(tmp_path)
    monkeypatch.setenv("AUTH_ENCRYPTION_KEY", Fernet.generate_key().decode())

    loaded = get_calendar_account(USER, str(tmp_path), account.id)

    assert loaded is not None and loaded.status == "active"
    assert (loaded.access_token, loaded.refresh_token) == ("", "")


def test_ciphertext_copied_to_another_user_does_not_decrypt(tmp_path):
    _connect(tmp_path)
    stolen = _stored(tmp_path)
    other = _connect(tmp_path, user="user-2")
    _write_raw(tmp_path, user="user-2",
               access_token=stolen["access_token"], refresh_token=stolen["refresh_token"])

    loaded = get_calendar_account("user-2", str(tmp_path), other.id)
    assert (loaded.access_token, loaded.refresh_token) == ("", "")


def _mirror(tmp_path, account_id: str) -> None:
    upsert_mirror_event(
        USER, str(tmp_path), account_id=account_id, provider="google",
        provider_event_id="g1", title="Dentist", start_at="2026-04-20T09:00:00-04:00",
        end_at="2026-04-20T09:30:00-04:00", timezone_name="America/Toronto",
    )


def test_disconnect_revokes_with_google_then_drops_tokens_and_copied_events(tmp_path, monkeypatch):
    account = _connect(tmp_path)
    save_sync_token(USER, str(tmp_path), account.id, "sync-1")
    _mirror(tmp_path, account.id)
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200)

    monkeypatch.setattr(google.httpx, "post", fake_post)

    assert CalendarService(USER, str(tmp_path)).disconnect_google_account(account.id) is True

    (url, kwargs), = calls
    assert url == "https://oauth2.googleapis.com/revoke"
    assert kwargs["data"] == {"token": "1//refresh-secret"}  # in the body, not the URL
    row = _stored(tmp_path)
    assert row["status"] == "disconnected"
    assert (row["access_token"], row["refresh_token"], row["sync_token"]) == ("", "", None)
    assert list_mirror_events(USER, str(tmp_path), include_deleted=True) == []


def test_disconnect_still_drops_the_tokens_when_google_cannot_be_reached(tmp_path, monkeypatch):
    account = _connect(tmp_path)

    def unreachable(url, **kwargs):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(google.httpx, "post", unreachable)

    assert CalendarService(USER, str(tmp_path)).disconnect_google_account(account.id) is True
    row = _stored(tmp_path)
    assert (row["status"], row["access_token"], row["refresh_token"]) == ("disconnected", "", "")


def test_disconnecting_twice_or_an_unknown_account_revokes_nothing(tmp_path, monkeypatch):
    account = _connect(tmp_path)
    calls = []
    monkeypatch.setattr(google.httpx, "post",
                        lambda url, **kwargs: calls.append(url) or httpx.Response(200))
    service = CalendarService(USER, str(tmp_path))

    assert service.disconnect_google_account(account.id) is True
    assert service.disconnect_google_account(account.id) is False
    assert service.disconnect_google_account("missing") is False
    assert len(calls) == 1


def test_accounts_disconnected_before_this_change_lose_their_leftover_tokens(tmp_path):
    _connect(tmp_path)
    _write_raw(tmp_path, status="disconnected", access_token="ya29.left", refresh_token="1//left",
               sync_token="sync-1")

    init_calendar_store(USER, str(tmp_path))

    row = _stored(tmp_path)
    assert (row["access_token"], row["refresh_token"], row["sync_token"]) == ("", "", None)


def test_a_late_token_refresh_cannot_write_tokens_back_after_disconnect(tmp_path):
    account = _connect(tmp_path)
    disconnect_calendar_account(USER, str(tmp_path), account.id)

    update_calendar_tokens(USER, str(tmp_path), account.id,
                           access_token="ya29.late", refresh_token="1//late")

    row = _stored(tmp_path)
    assert (row["access_token"], row["refresh_token"]) == ("", "")


def test_disconnect_route_revokes_and_wipes(authed_client, tmp_path, monkeypatch):
    account = _connect(tmp_path, user="test-user-id")
    calls = []
    monkeypatch.setattr(google.httpx, "post",
                        lambda url, **kwargs: calls.append(url) or httpx.Response(200))

    res = authed_client.delete(f"/personal-manager/google-calendar/accounts/{account.id}")

    assert res.status_code == 200 and res.json() == {"ok": True}
    assert calls == ["https://oauth2.googleapis.com/revoke"]
    assert _stored(tmp_path, user="test-user-id")["refresh_token"] == ""
