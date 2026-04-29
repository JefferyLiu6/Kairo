"""Auth route tests: signup, login, logout, /me, /csrf, rate limiting."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from assistant.http.pm_app import app
from assistant.persistence.user_store import init_users_db


# ── Shared state reset ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_process_local_state():
    """Clear process-local rate-limit buckets and CSRF tokens between tests."""
    import assistant.http.auth as _auth
    import assistant.http.pm_app as _pm
    _auth._auth_ip_buckets.clear()
    _auth._csrf_tokens.clear()
    _pm._ip_buckets.clear()
    _pm._user_buckets.clear()
    yield
    _auth._auth_ip_buckets.clear()
    _auth._csrf_tokens.clear()
    _pm._ip_buckets.clear()
    _pm._user_buckets.clear()


# ── Client fixture ────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with an isolated data dir and background sync disabled."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_CALENDAR_BACKGROUND_SYNC_SECONDS", "0")
    monkeypatch.setenv("COOKIE_SECURE", "false")   # TestClient uses http://
    monkeypatch.setenv("COOKIE_SAMESITE", "lax")
    init_users_db(str(tmp_path))
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _signup(client, email="user@example.com", password="hunter2hunter2",
            display_name="Test User"):
    return client.post("/auth/signup", json={
        "email": email, "password": password, "display_name": display_name,
    })


def _login(client, email="user@example.com", password="hunter2hunter2"):
    return client.post("/auth/login", json={"email": email, "password": password})


# ── Signup ────────────────────────────────────────────────────────────────────

def test_signup_new_account_returns_200(client):
    res = _signup(client)
    assert res.status_code == 200
    assert "detail" in res.json()


def test_signup_duplicate_email_still_returns_200(client):
    """Anti-enumeration: existing and new emails must look identical."""
    _signup(client)
    res = _signup(client)
    assert res.status_code == 200


def test_signup_duplicate_returns_same_body(client):
    _signup(client)
    first = _signup(client)
    second = _signup(client)
    assert first.json() == second.json()


def test_signup_short_password_rejected(client):
    res = client.post("/auth/signup", json={
        "email": "x@example.com", "password": "short", "display_name": "X",
    })
    assert res.status_code == 422


def test_signup_invalid_email_rejected(client):
    res = client.post("/auth/signup", json={
        "email": "notanemail", "password": "longenough!", "display_name": "X",
    })
    assert res.status_code == 422


# ── Login ─────────────────────────────────────────────────────────────────────

def test_login_success_sets_cookie(client):
    _signup(client)
    res = _login(client)
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == "user@example.com"
    assert data["is_demo"] is False
    assert "kairo_session" in res.cookies


def test_login_wrong_password_401(client):
    _signup(client)
    res = _login(client, password="wrongpassword!")
    assert res.status_code == 401
    assert "kairo_session" not in res.cookies


def test_login_unknown_email_401(client):
    res = _login(client, email="nobody@example.com")
    assert res.status_code == 401


def test_login_error_does_not_reveal_internals(client):
    res = _login(client, email="nobody@example.com")
    assert res.status_code == 401
    detail = res.json().get("detail", "")
    assert "traceback" not in detail.lower()
    assert "sqlite" not in detail.lower()


# ── /auth/me ──────────────────────────────────────────────────────────────────

def test_me_unauthenticated_401(client):
    res = client.get("/auth/me")
    assert res.status_code == 401


def test_me_authenticated_returns_user(client):
    _signup(client)
    _login(client)
    res = client.get("/auth/me")
    assert res.status_code == 200
    assert res.json()["email"] == "user@example.com"


# ── Logout ────────────────────────────────────────────────────────────────────

def test_logout_clears_cookie(client):
    _signup(client)
    _login(client)
    assert client.get("/auth/me").status_code == 200

    csrf = client.get("/auth/csrf").json()["csrf_token"]
    res = client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 204

    # Cookie cleared — subsequent /me must fail.
    # TestClient persists cookies across requests; manually clear it.
    client.cookies.clear()
    assert client.get("/auth/me").status_code == 401


def test_logout_without_session_is_204(client):
    """Logout with a valid CSRF token and no session still succeeds."""
    import assistant.http.pm_app as _pm_mod
    _pm_mod_orig = _pm_mod.is_valid_csrf_token
    _pm_mod.is_valid_csrf_token = lambda token, request=None: True
    try:
        res = client.post("/auth/logout", headers={"X-CSRF-Token": "any"})
        assert res.status_code == 204
    finally:
        _pm_mod.is_valid_csrf_token = _pm_mod_orig


def test_logout_without_csrf_token_is_403(client):
    """Logout must be CSRF-protected — missing token must be rejected."""
    _signup(client)
    _login(client)
    res = client.post("/auth/logout")
    assert res.status_code == 403
    # Session must still be active after the rejected request.
    assert client.get("/auth/me").status_code == 200


def test_logout_with_valid_csrf_token_succeeds(client):
    """Logout with a valid CSRF token clears the session."""
    _signup(client)
    _login(client)
    csrf = client.get("/auth/csrf").json()["csrf_token"]
    res = client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 204
    client.cookies.clear()
    assert client.get("/auth/me").status_code == 401


# ── CSRF ──────────────────────────────────────────────────────────────────────

def test_csrf_requires_auth(client):
    res = client.get("/auth/csrf")
    assert res.status_code == 401


def test_csrf_returns_token_when_authenticated(client):
    _signup(client)
    _login(client)
    res = client.get("/auth/csrf")
    assert res.status_code == 200
    token = res.json().get("csrf_token", "")
    assert len(token) > 20


def test_csrf_token_different_each_fetch(client):
    _signup(client)
    _login(client)
    t1 = client.get("/auth/csrf").json()["csrf_token"]
    t2 = client.get("/auth/csrf").json()["csrf_token"]
    assert t1 != t2


def test_pm_post_without_csrf_token_is_403(client):
    """PM state-changing routes must reject requests without a CSRF token."""
    _signup(client)
    _login(client)
    res = client.post("/personal-manager/chat", json={
        "message": "hello", "session_id": "s1",
    })
    assert res.status_code == 403


def test_pm_post_with_valid_csrf_token_passes_csrf_check(client, monkeypatch):
    """With a valid CSRF token the middleware passes (business logic may still fail)."""
    import assistant.http.pm_app as _pm_mod
    monkeypatch.setattr(_pm_mod, "is_valid_csrf_token", lambda token, request=None: True)

    _signup(client)
    _login(client)
    res = client.post(
        "/personal-manager/chat",
        json={"message": "hello", "session_id": "s1"},
        headers={"X-CSRF-Token": "fake-but-patched"},
    )
    # CSRF passed; may fail for other reasons (no LLM key) — just not 403.
    assert res.status_code != 403


# ── Thread deletion data isolation ───────────────────────────────────────────

def _authed_client_with_csrf(client):
    """Return a csrf-bypassing client and user id after signup+login."""
    import assistant.http.pm_app as _pm_mod
    _pm_mod.is_valid_csrf_token = lambda token, request=None: True
    _signup(client)
    _login(client)
    return client


def test_delete_session_purges_transcript(tmp_path, monkeypatch):
    """Deleting a PM thread must remove its conversation_log rows."""
    from assistant.persistence.user_store import init_users_db, create_user, create_session, ensure_thread
    from assistant.personal_manager.persistence.control_store import (
        append_conversation_turn, list_conversation_turns, delete_thread_data,
    )
    from assistant.persistence.user_store import delete_thread

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    data_dir = str(tmp_path)
    init_users_db(data_dir)
    create_user(data_dir, "owner@example.com", "hunter2hunter2", "Owner")

    from assistant.persistence.user_store import get_user_by_email
    user = get_user_by_email(data_dir, "owner@example.com")
    thread_id = "thread-del-test"
    ensure_thread(data_dir, user.id, thread_id)

    append_conversation_turn(user.id, thread_id, data_dir, "hello", "world")
    assert len(list_conversation_turns(user.id, thread_id, data_dir)) == 2

    delete_thread(data_dir, user.id, thread_id)
    delete_thread_data(user.id, thread_id, data_dir)

    assert list_conversation_turns(user.id, thread_id, data_dir) == []


def test_get_messages_returns_404_after_delete(client, monkeypatch):
    """GET /personal-manager/sessions/{id}/messages must 404 after the thread is deleted."""
    import assistant.http.pm_app as _pm_mod
    monkeypatch.setattr(_pm_mod, "is_valid_csrf_token", lambda token, request=None: True)

    _signup(client)
    _login(client)

    # Seed a thread directly via the persistence layer.
    import assistant.http.pm_app as pm
    from assistant.persistence.user_store import get_user_by_email, ensure_thread
    from assistant.personal_manager.persistence.control_store import append_conversation_turn

    data_dir = client.app.dependency_overrides.get("DATA_DIR", None)
    # DATA_DIR is set via monkeypatch.setenv in the client fixture; read it back.
    import os
    data_dir = os.environ["DATA_DIR"]
    user = get_user_by_email(data_dir, "user@example.com")
    thread_id = "pm-del-http-test"
    ensure_thread(data_dir, user.id, thread_id)
    append_conversation_turn(user.id, thread_id, data_dir, "hi", "hello")

    # Messages accessible before deletion.
    res = client.get(f"/personal-manager/sessions/{thread_id}/messages")
    assert res.status_code == 200
    assert len(res.json()["messages"]) == 2

    # Delete the thread via HTTP.
    csrf = client.get("/auth/csrf").json()["csrf_token"]
    client.delete(f"/personal-manager/sessions/{thread_id}",
                  headers={"X-CSRF-Token": csrf})

    # Messages must be gone — 404, not stale data.
    res = client.get(f"/personal-manager/sessions/{thread_id}/messages")
    assert res.status_code == 404


# ── Session cookie properties ─────────────────────────────────────────────────

def test_session_cookie_is_httponly(client):
    _signup(client)
    res = _login(client)
    set_cookie = res.headers.get("set-cookie", "")
    assert "httponly" in set_cookie.lower()


def test_session_cookie_has_max_age(client):
    _signup(client)
    res = _login(client)
    set_cookie = res.headers.get("set-cookie", "")
    assert "max-age" in set_cookie.lower()
