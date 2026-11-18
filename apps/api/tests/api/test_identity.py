"""Account lifecycle regressions, including concurrent token and factor redemption."""
import base64
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from kairo.api.app import app
from kairo.storage import identity
from kairo.api import account_mail as mail
from kairo.storage.user_store import _conn, get_user_by_email, init_users_db, get_session_user

PASSWORD = "long-test-passphrase"
NEW_PASSWORD = "new-long-test-passphrase"
EMAIL = "person@example.com"


@pytest.fixture
def client(tmp_path, monkeypatch, account_mail):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_CALENDAR_BACKGROUND_SYNC_SECONDS", "0")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_LOGIN_RATE_LIMIT_RPM", "100")
    monkeypatch.setenv("AUTH_LOGIN_EMAIL_LIMIT_15M", "100")
    init_users_db(str(tmp_path))
    with TestClient(app) as c:
        c.data_dir = str(tmp_path)
        c.mail = account_mail
        yield c


def signup(client, email=EMAIL):
    return client.post("/auth/signup", json={"email": email, "password": PASSWORD, "display_name": "Person"})


def latest_token(client, purpose="Verify", email=EMAIL):
    body = [body for recipient, subject, body in client.mail if recipient == email and subject.startswith(purpose)][-1]
    return re.search(r"#token=([A-Za-z0-9_-]+)", body)[1]


def verified(client):
    assert signup(client).status_code == 200
    assert client.post("/auth/verification/confirm", json={"token": latest_token(client), "password": PASSWORD}).status_code == 200
    return get_user_by_email(client.data_dir, EMAIL)


def login(client, code="", password=PASSWORD):
    return client.post("/auth/login", json={"email": EMAIL, "password": password, "code": code})


def factor(client, path, code="", password=PASSWORD):
    csrf = client.get("/auth/csrf").json()["csrf_token"]
    return client.post("/auth/mfa/" + path, json={"password": password, "code": code}, headers={"X-CSRF-Token": csrf})


def enabled(client):
    user = verified(client)
    assert login(client).status_code == 200
    setup = factor(client, "setup").json()
    code = identity.totp(setup["secret"], int(time.time()) // 30)
    response = factor(client, "enable", code)
    assert response.status_code == 200, response.text
    return user, setup["secret"], response.json()["recovery_codes"]


def test_verification_required_and_token_single_use(client):
    response = signup(client)
    token = latest_token(client)
    assert token not in response.text
    assert login(client).status_code == 401
    assert "kairo_session" not in client.cookies
    with _conn(client.data_dir) as db:
        assert db.execute("SELECT token_hash FROM account_tokens").fetchone()[0] != token
    confirm = client.post("/auth/verification/confirm", json={"token": token, "password": NEW_PASSWORD})
    assert confirm.status_code == 200
    assert client.post("/auth/verification/confirm", json={"token": token, "password": PASSWORD}).status_code == 400
    # Verification replaces a password supplied by a possible pre-registration attacker.
    assert login(client).status_code == 401
    assert login(client, password=NEW_PASSWORD).status_code == 200


def test_resend_invalidates_old_link_and_purpose_is_bound(client):
    signup(client)
    first = latest_token(client)
    assert client.post("/auth/verification/request", json={"email": EMAIL}).status_code == 200
    second = latest_token(client)
    assert first != second
    assert client.post("/auth/verification/confirm", json={"token": first, "password": PASSWORD}).status_code == 400
    assert client.post("/auth/password/reset", json={"token": second, "password": PASSWORD}).status_code == 400
    assert client.post("/auth/verification/confirm", json={"token": second, "password": PASSWORD}).status_code == 200


@pytest.mark.parametrize("purpose,route", [("verify", "/auth/verification/confirm"), ("reset", "/auth/password/reset")])
def test_expired_tokens_rejected(client, purpose, route):
    signup(client)
    uid = get_user_by_email(client.data_dir, EMAIL).id
    token = identity.issue_token(client.data_dir, uid, purpose)
    with _conn(client.data_dir) as db:
        db.execute("UPDATE account_tokens SET expires_at = 0")
    assert client.post(route, json={"token": token, "password": PASSWORD}).status_code == 400


def test_reset_revokes_all_sessions_and_preserves_mfa(client):
    user, secret, codes = enabled(client)
    old_cookie = client.cookies.get("kairo_session")
    assert login(client, codes[0]).status_code == 200
    other_cookie = client.cookies.get("kairo_session")
    response = client.post("/auth/password/forgot", json={"email": EMAIL})
    token = latest_token(client, "Reset")
    assert token not in response.text
    assert client.post("/auth/password/reset", json={"token": token, "password": NEW_PASSWORD}).status_code == 200
    assert get_session_user(client.data_dir, old_cookie) is None
    assert get_session_user(client.data_dir, other_cookie) is None
    assert identity.status(client.data_dir, user.id)["mfa_enabled"]
    assert login(client, codes[1]).status_code == 401
    assert login(client, password=NEW_PASSWORD).status_code == 401
    assert login(client, codes[1], NEW_PASSWORD).status_code == 200
    assert client.post("/auth/password/reset", json={"token": token, "password": PASSWORD}).status_code == 400


def test_request_responses_do_not_enumerate_accounts_or_mail_failures(client, monkeypatch, caplog):
    verified(client)
    unknown = client.post("/auth/password/forgot", json={"email": "unknown@example.com"})
    def fail(*args):
        raise RuntimeError("sensitive-address-and-token")
    monkeypatch.setattr(mail, "send_message", fail)
    known = client.post("/auth/password/forgot", json={"email": EMAIL})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert "sensitive-address-and-token" not in caplog.text
    assert "Account email delivery failed" in caplog.text


def test_missing_mail_configuration_fails_closed(client, monkeypatch):
    monkeypatch.delenv("SMTP_HOST")
    assert signup(client).status_code == 503
    assert get_user_by_email(client.data_dir, EMAIL) is None
    assert client.post("/auth/password/forgot", json={"email": EMAIL}).status_code == 503


def test_token_consumption_is_atomic(client):
    user = verified(client)
    token = identity.issue_token(client.data_dir, user.id, "reset")
    def attempt(_):
        try:
            identity.change_password_with_token(client.data_dir, token, "reset", NEW_PASSWORD)
            return True
        except identity.IdentityError:
            return False
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [False, True]


@pytest.mark.parametrize("seconds,expected", [(59, "94287082"), (1111111109, "07081804"),
    (1111111111, "14050471"), (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130")])
def test_rfc6238_sha1_vectors(seconds, expected):
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert identity.totp(secret, seconds // 30, digits=8) == expected


def test_mfa_requires_password_csrf_and_valid_setup_code(client, monkeypatch):
    user = verified(client)
    login(client)
    assert client.post("/auth/mfa/setup", json={"password": PASSWORD}).status_code == 403
    assert factor(client, "setup", password="wrong").status_code == 400
    setup = factor(client, "setup").json()
    assert factor(client, "enable", "invalid").status_code == 400
    with _conn(client.data_dir) as db:
        row = db.execute("SELECT * FROM account_security WHERE user_id = ?", (user.id,)).fetchone()
        assert setup["secret"] not in row["pending_secret"]
        assert row["mfa_secret"] is None
        db.execute("UPDATE account_security SET pending_until = 0 WHERE user_id = ?", (user.id,))
    assert factor(client, "enable", identity.totp(setup["secret"], int(time.time()) // 30)).status_code == 400


def test_mfa_codes_and_recovery_cannot_be_replayed(client):
    user, secret, codes = enabled(client)
    assert len(codes) == len(set(codes)) == 10
    assert login(client).status_code == 401
    assert login(client, identity.totp(secret, int(time.time()) // 30)).status_code == 401
    assert login(client, codes[0]).status_code == 200
    assert login(client, codes[0]).status_code == 401
    with _conn(client.data_dir) as db:
        row = db.execute("SELECT mfa_secret FROM account_security WHERE user_id = ?", (user.id,)).fetchone()
        assert secret not in row[0]
        hashes = [r[0] for r in db.execute("SELECT code_hash FROM mfa_recovery_codes")]
        assert len(hashes) == 9
        assert all(c.replace("-", "") not in hashes for c in codes)


def test_totp_window_and_atomic_replay(client, monkeypatch):
    user, secret, _ = enabled(client)
    # An independent clock source for just identity, so rate-limit and session clocks stay real.
    class Clock:
        @staticmethod
        def time():
            return now
    now = time.time() + 60
    monkeypatch.setattr(identity, "time", Clock)
    assert login(client, identity.totp(secret, int(now) // 30 - 2)).status_code == 401
    valid = identity.totp(secret, int(now) // 30)
    def attempt(_):
        try:
            return bool(identity.login_session(client.data_dir, EMAIL, PASSWORD, valid, None))
        except identity.IdentityError:
            return False
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [False, True]


def test_recovery_code_concurrency(client):
    _, _, codes = enabled(client)
    def attempt(_):
        try:
            return bool(identity.login_session(client.data_dir, EMAIL, PASSWORD, codes[0], None))
        except identity.IdentityError:
            return False
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [False, True]


def test_mfa_management_revokes_other_devices_and_old_recovery_codes(client):
    user, _, codes = enabled(client)
    # Create another fully authenticated device session without replacing the current one.
    other = identity.login_session(client.data_dir, EMAIL, PASSWORD, codes[0], None)
    result = factor(client, "recovery", codes[1])
    assert result.status_code == 200
    replacement = result.json()["recovery_codes"]
    assert get_session_user(client.data_dir, other) is None
    assert login(client, codes[2]).status_code == 401
    assert factor(client, "disable", replacement[0]).status_code == 200
    assert not identity.status(client.data_dir, user.id)["mfa_enabled"]
    assert login(client).status_code == 200


def test_missing_encryption_key_never_bypasses_mfa(client, monkeypatch):
    _, _, codes = enabled(client)
    monkeypatch.delenv("AUTH_ENCRYPTION_KEY")
    assert login(client, codes[0]).status_code == 503


def test_demo_cannot_enroll_mfa(client):
    assert client.post("/auth/demo", json={}).status_code == 201
    assert factor(client, "setup").status_code == 403
    assert client.get("/auth/security").status_code == 403


@pytest.mark.parametrize("path", ["/auth/verification/request", "/auth/verification/confirm", "/auth/password/forgot", "/auth/password/reset"])
def test_new_public_routes_require_json_and_trusted_origin(client, path):
    assert client.post(path, json={}, headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post(path, content="{}", headers={"Content-Type": "text/plain"}).status_code == 415


def test_mfa_and_email_rate_limits(client):
    verified(client)
    login(client)
    for _ in range(5):
        assert factor(client, "setup", password="wrong").status_code == 400
    assert factor(client, "setup", password="wrong").status_code == 429
    for _ in range(4):  # signup already counts as one email
        assert client.post("/auth/password/forgot", json={"email": EMAIL}).status_code == 200
    assert client.post("/auth/password/forgot", json={"email": EMAIL}).status_code == 429


def test_short_window_rate_check_preserves_long_window_history(tmp_path):
    from kairo.api.rate_limit import check_rate_limit, RateLimitRule, RateLimitExceeded
    directory = str(tmp_path)
    hourly = [RateLimitRule(1, 3600, "hour")]
    check_rate_limit(directory, ["email"], hourly, now=1000)
    check_rate_limit(directory, ["csrf"], [RateLimitRule(30, 60, "minute")], now=1300)
    with pytest.raises(RateLimitExceeded):
        check_rate_limit(directory, ["email"], hourly, now=1301)


def test_existing_database_migration_requires_verification(tmp_path):
    from kairo.storage.user_store import _SCHEMA, create_user, create_session
    directory = str(tmp_path)
    with sqlite3.connect(tmp_path / "users.db") as db:
        db.executescript(_SCHEMA)
    user = create_user(directory, EMAIL, PASSWORD, "Existing User")
    session = create_session(directory, user.id)
    init_users_db(directory)
    init_users_db(directory)
    assert get_session_user(directory, session) is None
    assert not identity.status(directory, user.id)["email_verified"]
    token = identity.issue_token(directory, user.id, "verify")
    identity.change_password_with_token(directory, token, "verify", NEW_PASSWORD)
    assert get_session_user(directory, session) is None
    assert identity.login_session(directory, EMAIL, NEW_PASSWORD, "", None)


def test_smtp_uses_verified_tls_authentication_and_timeout(monkeypatch):
    calls = []
    monkeypatch.setenv("AUTH_PUBLIC_URL", "https://kairo.example")
    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    monkeypatch.setenv("SMTP_FROM", "accounts@kairo.example")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_SECURITY", "starttls")
    monkeypatch.setenv("SMTP_USERNAME", "smtp-user")
    monkeypatch.setenv("SMTP_PASSWORD", "smtp-password")
    class SMTP:
        def __init__(self, host, port, timeout):
            calls.append((host, port, timeout))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def ehlo(self):
            calls.append("ehlo")
        def starttls(self, context):
            import ssl
            assert context.check_hostname
            assert context.verify_mode == ssl.CERT_REQUIRED
            calls.append("tls")
        def login(self, username, password):
            assert (username, password) == ("smtp-user", "smtp-password")
            calls.append("login")
        def send_message(self, message):
            assert message["To"] == EMAIL
            calls.append("sent")
    monkeypatch.setattr(mail.smtplib, "SMTP", SMTP)
    mail.send_message(EMAIL, "Verify", "Test body")
    assert calls == [("smtp.example", 587, 10), "ehlo", "tls", "ehlo", "login", "sent"]


@pytest.mark.parametrize("origin", ["http://public.example", "https://evil.example/path", "https://user:password@example.com", "https://example.com?next=evil"])
def test_email_link_origin_is_fixed_and_validated(account_mail, monkeypatch, origin):
    monkeypatch.setenv("AUTH_PUBLIC_URL", origin)
    with pytest.raises(identity.IdentityUnavailable):
        mail.configuration()
