"""Leaving Kairo: exporting what an account holds, and deleting the account with all of it."""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from kairo.api import account_data
from kairo.api.app import app
from kairo.storage import idempotency, identity
from kairo.storage.calendar_store import upsert_calendar_account
from kairo.storage.control_store import pm_db_path
from kairo.storage.journal import journal_append
from kairo.storage.store import PrivateMetadata, pm_dir, save_private
from kairo.storage.user_store import ensure_thread, get_user_by_email, init_users_db
from kairo.tools.calendar import google, service

PASSWORD = "long-test-passphrase"
EMAIL = "person@example.com"


@pytest.fixture
def client(tmp_path, monkeypatch, account_mail):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_CALENDAR_BACKGROUND_SYNC_SECONDS", "0")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    init_users_db(str(tmp_path))
    with TestClient(app) as c:
        c.data_dir = str(tmp_path)
        c.mail = account_mail
        yield c


def _signed_in(client, email: str = EMAIL):
    assert client.post("/auth/signup", json={
        "email": email, "password": PASSWORD, "display_name": "Person"}).status_code == 200
    link = [body for to, subject, body in client.mail if to == email and subject.startswith("Verify")][-1]
    token = re.search(r"#token=([A-Za-z0-9_-]+)", link)[1]
    assert client.post("/auth/verification/confirm", json={"token": token, "password": PASSWORD}).status_code == 200
    assert client.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    return get_user_by_email(client.data_dir, email)


def _post(client, action: str, password: str = PASSWORD, code: str = ""):
    csrf = client.get("/auth/csrf").json()["csrf_token"]
    return client.post(f"/auth/account/{action}", json={"password": password, "code": code},
                       headers={"X-CSRF-Token": csrf})


def _fill(client, user) -> None:
    """Give the account one record of each kind: a task from chat, a chat thread, a private
    note, a journal entry, a connected calendar and a stored reply."""
    csrf = client.get("/auth/csrf").json()["csrf_token"]
    assert client.post("/personal-manager/chat", headers={"X-CSRF-Token": csrf},
                       json={"message": "Add task to water the ferns", "session_id": "plants"}).status_code == 200
    # Web chat records its threads; a thread id belongs to one account.
    ensure_thread(client.data_dir, user.id, f"plants-{user.id}", "Plants")
    save_private(PrivateMetadata(notes_private=["my knee surgery is in March"]), user.id, client.data_dir)
    journal_append("Felt good about the week.", pm_db_path(user.id, client.data_dir))
    upsert_calendar_account(
        user.id, client.data_dir, provider="google", calendar_id="primary",
        account_email="person@gmail.example", access_token="ya29.access-secret",
        refresh_token="1//refresh-secret", token_expiry=None,
        scopes=[google.GOOGLE_CALENDAR_READONLY_SCOPE],
    )
    idempotency.claim(client.data_dir, user.id, "key-1", idempotency.fingerprint("hello"))
    idempotency.finish(client.data_dir, user.id, "key-1", "a stored reply")


def _archive(response) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


# ── Export ────────────────────────────────────────────────────────────────────

def test_export_holds_the_accounts_records_and_no_credentials(client):
    user = _signed_in(client)
    _fill(client, user)

    response = _post(client, "export")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith('attachment; filename="kairo-export-')
    assert response.headers["cache-control"] == "no-store"
    files = _archive(response)

    account = json.loads(files["account.json"])
    assert account["email"] == EMAIL and account["created_at"]
    assert [thread["title"] for thread in account["chat_threads"]] == ["Plants"]
    assert "water the ferns" in files["files/todos.json"]
    # The one place a private note's text leaves the server: its owner's own export.
    assert "knee surgery" in files["files/private.json"]
    assert "Felt good about the week." in files["tables/journal_entries.json"]
    assert "water the ferns" in files["tables/conversation_log.json"]
    assert "person@gmail.example" in files["tables/calendar_accounts.json"]

    everything = "\n".join(files.values())
    stored = sqlite3.connect(pm_db_path(user.id, client.data_dir)).execute(
        "SELECT access_token, refresh_token FROM calendar_accounts").fetchone()
    for secret in ("ya29.access-secret", "1//refresh-secret", *stored, PASSWORD, "password_hash"):
        assert secret not in everything
    # The search index repeats the journal and SQLite's own tables are not records.
    assert not [name for name in files if "journal_fts" in name or "sqlite_" in name]


def test_export_of_an_account_with_nothing_in_it(client):
    _signed_in(client)
    files = _archive(_post(client, "export"))
    assert set(files) == {"README.txt", "account.json"}


@pytest.mark.parametrize("action", ["export", "delete"])
def test_the_password_is_asked_again(client, action):
    user = _signed_in(client)
    _fill(client, user)

    wrong = _post(client, action, password="not-the-password")
    assert wrong.status_code == 400
    assert wrong.headers["content-type"].startswith("application/json")
    assert client.post(f"/auth/account/{action}", json={"password": PASSWORD}).status_code == 403  # no CSRF token
    assert get_user_by_email(client.data_dir, EMAIL) is not None
    assert os.path.exists(pm_db_path(user.id, client.data_dir))


@pytest.mark.parametrize("action", ["export", "delete"])
def test_an_enabled_authenticator_is_asked_for_too(client, action):
    _signed_in(client)
    csrf = {"X-CSRF-Token": client.get("/auth/csrf").json()["csrf_token"]}
    secret = client.post("/auth/mfa/setup", json={"password": PASSWORD}, headers=csrf).json()["secret"]
    now = int(time.time()) // 30
    assert client.post("/auth/mfa/enable", headers=csrf,
                       json={"password": PASSWORD, "code": identity.totp(secret, now)}).status_code == 200

    assert _post(client, action).status_code == 400
    assert _post(client, action, code="000000").status_code == 400
    assert get_user_by_email(client.data_dir, EMAIL) is not None
    # The code that enabled the authenticator is spent; the next step's code is still in the window.
    assert _post(client, action, code=identity.totp(secret, now + 1)).status_code == 200


@pytest.mark.parametrize("action", ["export", "delete"])
def test_a_demo_has_no_account_to_export_or_delete(client, monkeypatch, action):
    monkeypatch.setenv("AUTH_DEMO_RATE_LIMIT_RPM", "100")
    assert client.post("/auth/demo", json={}).status_code == 201
    assert _post(client, action, password="anything").status_code == 403
    assert client.get("/auth/me").status_code == 200


# ── Deletion ──────────────────────────────────────────────────────────────────

def test_deleting_removes_the_account_and_everything_stored_for_it(client, monkeypatch):
    revoked: list[str] = []
    monkeypatch.setattr(service, "revoke_google_token", lambda token: revoked.append(token) or True)
    user = _signed_in(client)
    _fill(client, user)
    other = TestClient(app)   # a second device signed in to the same account
    assert other.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 200

    response = _post(client, "delete")
    assert response.status_code == 200, response.text

    assert get_user_by_email(client.data_dir, EMAIL) is None
    assert not os.path.exists(pm_dir(user.id, client.data_dir))
    users_db = sqlite3.connect(os.path.join(client.data_dir, "users.db"))
    for table in ("auth_sessions", "chat_threads", "account_security", "account_tokens",
                  "mfa_recovery_codes", "idempotency_keys"):
        assert users_db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    # Google was told to end the grant, with the token as it was before encryption.
    assert revoked == ["1//refresh-secret"]

    assert client.get("/auth/me").status_code == 401
    assert other.get("/auth/me").status_code == 401
    assert client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 401
    assert [body for to, subject, body in client.mail if to == EMAIL and "was deleted" in body]


def test_the_address_can_sign_up_again_and_starts_empty(client):
    first = _signed_in(client)
    _fill(client, first)
    assert _post(client, "delete").status_code == 200

    second = _signed_in(client)
    assert second.id != first.id
    assert set(_archive(_post(client, "export"))) == {"README.txt", "account.json"}


def test_deleting_one_account_leaves_another_alone(client):
    keeper = _signed_in(client, "keeper@example.com")
    _fill(client, keeper)
    client.cookies.clear()
    leaver = _signed_in(client)
    _fill(client, leaver)

    assert _post(client, "delete").status_code == 200

    assert get_user_by_email(client.data_dir, "keeper@example.com") is not None
    assert os.path.exists(os.path.join(pm_dir(keeper.id, client.data_dir), "todos.json"))
    users_db = sqlite3.connect(os.path.join(client.data_dir, "users.db"))
    assert users_db.execute("SELECT user_id FROM idempotency_keys").fetchall() == [(keeper.id,)]


def test_if_erasing_fails_the_account_is_still_there_to_try_again(client, monkeypatch):
    user = _signed_in(client)
    _fill(client, user)

    def fail(_path):
        raise OSError("disk says no")

    monkeypatch.setattr(account_data.shutil, "rmtree", fail)
    with pytest.raises(OSError):
        _post(client, "delete")
    assert client.get("/auth/me").status_code == 200

    monkeypatch.undo()
    monkeypatch.setenv("DATA_DIR", client.data_dir)
    assert _post(client, "delete").status_code == 200
    assert get_user_by_email(client.data_dir, EMAIL) is None
    assert not os.path.exists(pm_dir(user.id, client.data_dir))
