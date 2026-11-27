"""Auth route tests: signup, login, logout, /me, /csrf, rate limiting."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kairo.api.app import app
from kairo.storage.user_store import init_users_db


# ── Client fixture ────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch, account_mail):
    """TestClient with an isolated data dir and background sync disabled."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_CALENDAR_BACKGROUND_SYNC_SECONDS", "0")
    monkeypatch.setenv("COOKIE_SECURE", "false")   # TestClient uses http://
    monkeypatch.setenv("COOKIE_SAMESITE", "lax")
    init_users_db(str(tmp_path))
    with TestClient(app, raise_server_exceptions=True) as c:
        c.account_mail = account_mail
        yield c


def _signup(client, email="user@example.com", password="hunter2hunter2",
            display_name="Test User"):
    result = client.post("/auth/signup", json={
        "email": email, "password": password, "display_name": display_name,
    })
    # Exercise the real verification route before tests requiring a signed-in user.
    import re
    links = [body for recipient, subject, body in client.account_mail
             if recipient == email.lower() and subject == "Verify your Kairo email"]
    if result.status_code == 200 and links:
        token = re.search(r"#token=([A-Za-z0-9_-]+)", links[-1])[1]
        client.post("/auth/verification/confirm", json={"token": token, "password": password})
    return result



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
    assert data["credits_remaining"] == 10
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


def test_login_email_rate_limit_is_email_scoped(client, monkeypatch):
    monkeypatch.setenv("AUTH_LOGIN_RATE_LIMIT_RPM", "100")
    monkeypatch.setenv("AUTH_LOGIN_RATE_LIMIT_HOURLY", "100")
    monkeypatch.setenv("AUTH_LOGIN_EMAIL_LIMIT_15M", "2")

    assert _login(client, email="target@example.com").status_code == 401
    assert _login(client, email="target@example.com").status_code == 401

    limited = _login(client, email="target@example.com")
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0

    # Same IP can still try a different email because the stricter bucket is per email hash.
    assert _login(client, email="other@example.com").status_code == 401


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
    assert res.json()["credits_remaining"] == 10


def test_auth_demo_starts_with_initial_credits(client, monkeypatch):
    from kairo.storage.user_store import DEMO_INITIAL_CREDITS

    monkeypatch.setenv("AUTH_DEMO_RATE_LIMIT_RPM", "100")
    res = client.post("/auth/demo", json={})
    assert res.status_code == 201
    data = res.json()
    assert data["is_demo"] is True
    assert data["credits_remaining"] == DEMO_INITIAL_CREDITS


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
    import kairo.api.app as _pm_mod
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


def test_auth_demo_rate_limit_has_retry_after(client, monkeypatch):
    monkeypatch.setenv("AUTH_DEMO_RATE_LIMIT_RPM", "1")
    monkeypatch.setenv("AUTH_DEMO_RATE_LIMIT_HOURLY", "100")
    monkeypatch.setenv("AUTH_DEMO_RATE_LIMIT_DAILY", "100")

    first = client.post("/auth/demo", json={})
    assert first.status_code == 201

    second = client.post("/auth/demo", json={})
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) > 0


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
    import kairo.api.app as _pm_mod
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


def test_demo_user_pm_rate_limit_uses_demo_policy(client, monkeypatch):
    monkeypatch.setenv("AUTH_DEMO_RATE_LIMIT_RPM", "100")
    monkeypatch.setenv("PM_DEMO_RATE_LIMIT_RPM", "100")
    monkeypatch.setenv("PM_DEMO_RATE_LIMIT_DAILY", "1")

    demo = client.post("/auth/demo", json={})
    assert demo.status_code == 201
    csrf = client.get("/auth/csrf").json()["csrf_token"]

    payload = {"message": "Add task to smoke test", "session_id": "demo-limit"}
    first = client.post("/personal-manager/chat", json=payload, headers={"X-CSRF-Token": csrf})
    assert first.status_code == 200

    second = client.post("/personal-manager/chat", json=payload, headers={"X-CSRF-Token": csrf})
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) > 0


def test_pm_chat_consumes_one_credit(client):
    _signup(client)
    _login(client)
    csrf = client.get("/auth/csrf").json()["csrf_token"]

    res = client.post(
        "/personal-manager/chat",
        json={"message": "Add task to smoke test", "session_id": "credit-test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["credits_remaining"] == 9


def test_pm_chat_returns_402_when_credits_exhausted(client):
    _signup(client)
    _login(client)
    from kairo.storage.user_store import get_user_by_email, _conn
    import os

    data_dir = os.environ["DATA_DIR"]
    user = get_user_by_email(data_dir, "user@example.com")
    with _conn(data_dir) as db:
        db.execute("UPDATE users SET credits_remaining = 0 WHERE id = ?", (user.id,))

    csrf = client.get("/auth/csrf").json()["csrf_token"]
    res = client.post(
        "/personal-manager/chat",
        json={"message": "Add task to smoke test", "session_id": "credit-empty"},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 402
    assert "credits" in res.json()["detail"].lower()
    assert "refill" in res.json()["detail"].lower()
    # Spent today's credits: they stay spent for the rest of the day.
    assert client.get("/auth/me").json()["credits_remaining"] == 0


def _spend_all_credits(email: str, refilled_on: str | None, data_dir: str = "") -> None:
    """Leave the account with no credits, last refilled on the given UTC date."""
    import os
    import sqlite3

    db = sqlite3.connect(os.path.join(data_dir or os.environ["DATA_DIR"], "users.db"))
    with db:
        db.execute(
            "UPDATE users SET credits_remaining = 0, credits_reset_on = ? WHERE email = ?",
            (refilled_on, email),
        )
    db.close()


def _yesterday() -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()


# None is an account from before the refill date was recorded.
@pytest.mark.parametrize("refilled_on", [_yesterday(), None])
def test_registered_credits_refill_on_a_new_day(client, refilled_on):
    _signup(client)
    _login(client)
    _spend_all_credits("user@example.com", refilled_on)

    # The web app stops sending at zero, so the refill has to show before a message is sent.
    assert client.get("/auth/me").json()["credits_remaining"] == 10

    csrf = client.get("/auth/csrf").json()["csrf_token"]
    res = client.post(
        "/personal-manager/chat",
        json={"message": "Add task to smoke test", "session_id": "credit-refill"},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200
    assert client.get("/auth/me").json()["credits_remaining"] == 9


def test_refill_straight_from_a_message_spends_one_of_the_new_credits(tmp_path):
    from kairo.storage.user_store import consume_user_credit, create_user

    data_dir = str(tmp_path)
    init_users_db(data_dir)
    user = create_user(data_dir, "direct@example.com", "hunter2hunter2", "Direct")
    assert [consume_user_credit(data_dir, user.id) for _ in range(11)] == [9, 8, 7, 6, 5, 4, 3, 2, 1, 0, None]

    _spend_all_credits("direct@example.com", _yesterday(), data_dir)
    assert consume_user_credit(data_dir, user.id) == 9


def test_daily_credits_follow_the_setting(client, monkeypatch):
    monkeypatch.setenv("USER_DAILY_CREDITS", "3")
    _signup(client)
    assert _login(client).json()["credits_remaining"] == 3

    _spend_all_credits("user@example.com", _yesterday())
    assert client.get("/auth/me").json()["credits_remaining"] == 3


def test_demo_credits_are_not_refilled(client, monkeypatch):
    monkeypatch.setenv("AUTH_DEMO_RATE_LIMIT_RPM", "100")
    demo = client.post("/auth/demo", json={})
    assert demo.status_code == 201
    _spend_all_credits(demo.json()["email"], _yesterday())

    assert client.get("/auth/me").json()["credits_remaining"] == 0
    csrf = client.get("/auth/csrf").json()["csrf_token"]
    res = client.post(
        "/personal-manager/chat",
        json={"message": "Add task to smoke test", "session_id": "demo-empty"},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 402
    assert "refill" not in res.json()["detail"].lower()


# ── Thread deletion data isolation ───────────────────────────────────────────

def test_delete_session_purges_transcript(tmp_path, monkeypatch):
    """Deleting a PM thread must remove its conversation_log rows."""
    from kairo.storage.user_store import init_users_db, create_user, ensure_thread
    from kairo.storage.control_store import append_conversation_turn, list_conversation_turns, delete_thread_data
    from kairo.storage.user_store import delete_thread

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    data_dir = str(tmp_path)
    init_users_db(data_dir)
    create_user(data_dir, "owner@example.com", "hunter2hunter2", "Owner")

    from kairo.storage.user_store import get_user_by_email
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
    import kairo.api.app as _pm_mod
    monkeypatch.setattr(_pm_mod, "is_valid_csrf_token", lambda token, request=None: True)

    _signup(client)
    _login(client)

    # Seed a thread directly via the persistence layer.
    from kairo.storage.user_store import get_user_by_email, ensure_thread
    from kairo.storage.control_store import append_conversation_turn

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


# ── Production auth boundaries (real cookies and CSRF; no auth bypasses) ──────

def test_csrf_tokens_survive_multiple_tabs(client):
    _signup(client)
    _login(client)
    tokens = [client.get('/auth/csrf').json()['csrf_token'] for _ in range(2)]
    for token in tokens:
        assert client.put('/personal-manager/pet', json={'xp': 7},
                          headers={'X-CSRF-Token': token}).status_code == 204


def test_csrf_validates_in_an_independent_process(client):
    import json
    import subprocess
    import sys

    _signup(client)
    _login(client)
    token = client.get('/auth/csrf').json()['csrf_token']
    worker = subprocess.run(
        [sys.executable, '-c', '''
import json, sys
from starlette.requests import Request
from kairo.api.auth import is_valid_csrf_token
value = json.load(sys.stdin)
request = Request({'type': 'http', 'headers': [
    (b'cookie', ('kairo_session=' + value['cookie']).encode())
]})
assert is_valid_csrf_token(value['csrf'], request)
'''], input=json.dumps({'cookie': client.cookies['kairo_session'], 'csrf': token}),
        text=True, capture_output=True, timeout=30,
    )
    assert worker.returncode == 0, worker.stderr


def test_login_rotates_and_revokes_the_previous_session(client):
    _signup(client)
    _login(client)
    old_cookie = client.cookies['kairo_session']
    old_csrf = client.get('/auth/csrf').json()['csrf_token']
    _login(client)
    assert client.cookies['kairo_session'] != old_cookie
    assert client.put('/personal-manager/pet', json={},
                      headers={'X-CSRF-Token': old_csrf}).status_code == 403
    assert client.get('/auth/me', headers={'Cookie': f'kairo_session={old_cookie}'}).status_code == 401


def test_logout_revokes_a_replayed_cookie_and_csrf(client):
    _signup(client)
    _login(client)
    cookie = client.cookies['kairo_session']
    csrf = client.get('/auth/csrf').json()['csrf_token']
    response = client.post('/auth/logout', headers={'X-CSRF-Token': csrf})
    assert response.status_code == 204
    assert 'Max-Age=0' in response.headers['set-cookie']
    assert client.get('/auth/me', headers={'Cookie': f'kairo_session={cookie}'}).status_code == 401
    assert client.put('/personal-manager/pet', json={}, headers={
        'Cookie': f'kairo_session={cookie}', 'X-CSRF-Token': csrf,
    }).status_code == 403


@pytest.mark.parametrize('invalidation', ['session_expiry', 'account_deletion'])
def test_invalid_sessions_cannot_read_or_write(client, invalidation):
    import os
    from kairo.storage.user_store import _conn

    _signup(client)
    user = _login(client).json()
    token = client.get('/auth/csrf').json()['csrf_token']
    with _conn(os.environ['DATA_DIR']) as db:
        if invalidation == 'session_expiry':
            db.execute("UPDATE auth_sessions SET expires_at = '2000-01-01' WHERE user_id = ?", (user['id'],))
        else:
            db.execute('DELETE FROM users WHERE id = ?', (user['id'],))
    assert client.get('/auth/me').status_code == 401
    assert client.put('/personal-manager/pet', json={}, headers={'X-CSRF-Token': token}).status_code == 403


def test_csrf_rejects_tampering_and_missing_request(client):
    from kairo.api.auth import is_valid_csrf_token

    _signup(client)
    _login(client)
    token = client.get('/auth/csrf').json()['csrf_token']
    assert not is_valid_csrf_token(token)
    modified = ('0' if token[0] != '0' else '1') + token[1:]
    assert client.put('/personal-manager/pet', json={},
                      headers={'X-CSRF-Token': modified}).status_code == 403


@pytest.mark.parametrize('path', ['/auth/login', '/auth/signup', '/auth/demo'])
def test_preauth_rejects_untrusted_origin_and_simple_forms(client, path):
    assert client.post(path, json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post(path, json={}, headers={'Origin': 'null'}).status_code == 403
    assert client.post(path, content='{}', headers={'Content-Type': 'text/plain'}).status_code == 415
    assert client.post(path, data={'email': 'user@example.com'}).status_code == 415


def test_preauth_allows_same_origin_and_explicit_frontend(client, monkeypatch):
    import kairo.api.app as module

    _signup(client)
    payload = {'email': 'user@example.com', 'password': 'hunter2hunter2'}
    assert client.post('/auth/login', json=payload, headers={'Origin': 'http://testserver'}).status_code == 200
    monkeypatch.setattr(module, '_cors_origins', ['https://frontend.example'])
    assert client.post('/auth/login', json=payload, headers={'Origin': 'https://frontend.example'}).status_code == 200


def test_auth_and_private_responses_are_not_cacheable(client):
    _signup(client)
    response = _login(client)
    assert response.headers['cache-control'] == 'no-store'
    for path in ['/auth/me', '/auth/csrf', '/personal-manager/tasks']:
        assert client.get(path).headers['cache-control'] == 'no-store'


def test_secure_cookie_flags(client, monkeypatch):
    _signup(client)
    monkeypatch.setenv('COOKIE_SECURE', 'true')
    monkeypatch.setenv('COOKIE_SAMESITE', 'lax')
    cookie = _login(client).headers['set-cookie'].lower()
    assert 'secure' in cookie and 'httponly' in cookie and 'samesite=lax' in cookie


def test_users_cannot_read_delete_or_take_over_another_users_thread(client):
    import os
    from kairo.storage.user_store import ensure_thread, list_threads
    from kairo.storage.control_store import append_conversation_turn, list_conversation_turns

    data_dir = os.environ['DATA_DIR']
    _signup(client, email='alice@example.com')
    alice = _login(client, email='alice@example.com').json()
    alice_csrf = client.get('/auth/csrf').json()['csrf_token']
    ensure_thread(data_dir, alice['id'], 'pm-alice-private-thread', 'Private')
    append_conversation_turn(alice['id'], 'pm-alice-private-thread', data_dir, 'private text', 'private reply')

    _signup(client, email='bob@example.com')
    bob = _login(client, email='bob@example.com').json()
    bob_csrf = client.get('/auth/csrf').json()['csrf_token']
    headers = {'X-CSRF-Token': bob_csrf}
    assert client.get('/personal-manager/sessions').json()['sessions'] == []
    for suffix in ['', '/messages']:
        assert client.get('/personal-manager/sessions/pm-alice-private-thread' + suffix).status_code == 404
    assert client.put('/personal-manager/pet', json={}, headers={'X-CSRF-Token': alice_csrf}).status_code == 403
    client.delete('/personal-manager/sessions/pm-alice-private-thread', headers=headers)
    response = client.post('/orchestrator/stream', json={
        'session_id': 'pm-alice-private-thread', 'message': 'read this conversation',
    }, headers=headers)
    assert response.status_code == 404
    assert client.get('/auth/me').json()['credits_remaining'] == bob['credits_remaining']
    assert len(list_threads(data_dir, alice['id'])) == 1
    assert len(list_conversation_turns(alice['id'], 'pm-alice-private-thread', data_dir)) == 2


def test_supplied_user_id_cannot_read_or_overwrite_another_schedule(client):
    import os
    from kairo.storage.store import load_schedule

    _signup(client, email='alice@example.com')
    alice = _login(client, email='alice@example.com').json()
    alice_csrf = client.get('/auth/csrf').json()['csrf_token']
    schedule = {'entries': [{'id': 'private', 'title': 'Alice appointment', 'date': '2027-01-01'}]}
    assert client.put('/personal-manager/schedule/default', json=schedule,
                      headers={'X-CSRF-Token': alice_csrf}).status_code == 200
    _signup(client, email='bob@example.com')
    _login(client, email='bob@example.com')
    bob_csrf = client.get('/auth/csrf').json()['csrf_token']
    path = f"/personal-manager/schedule/{alice['id']}?user_id={alice['id']}"
    assert client.get(path).json()['entries'] == []
    assert client.put(path, json={'entries': []}, headers={'X-CSRF-Token': bob_csrf}).status_code == 200
    assert load_schedule(alice['id'], os.environ['DATA_DIR']).entries[0].title == 'Alice appointment'


def test_demo_cannot_connect_google_calendar(client):
    assert client.post('/auth/demo', json={}).status_code == 201
    assert client.get('/personal-manager/google-calendar/connect').status_code == 403
