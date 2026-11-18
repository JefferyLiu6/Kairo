"""Account verification, recovery and TOTP. Security transitions are atomic in users.db."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import struct
import time
from urllib.parse import quote

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from cryptography.fernet import Fernet, InvalidToken

from kairo.storage.user_store import _conn, _hash_token, _now, create_session

SCHEMA = """
CREATE TABLE IF NOT EXISTS account_security (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    email_verified INTEGER NOT NULL DEFAULT 0,
    mfa_secret TEXT,
    pending_secret TEXT,
    pending_until REAL,
    last_counter INTEGER NOT NULL DEFAULT -1
);
CREATE TABLE IF NOT EXISTS account_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL CHECK(purpose IN ('verify', 'reset')),
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_account_tokens_user ON account_tokens(user_id, purpose);
CREATE TABLE IF NOT EXISTS mfa_recovery_codes (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    PRIMARY KEY(user_id, code_hash)
);
"""


class IdentityError(ValueError):
    pass


class IdentityUnavailable(RuntimeError):
    pass


def cipher() -> Fernet:
    try:
        return Fernet(os.environ.get("AUTH_ENCRYPTION_KEY", "").encode())
    except (ValueError, TypeError) as exc:
        raise IdentityUnavailable("Account security encryption is not configured") from exc


def _encrypt(user_id: str, secret: str) -> str:
    # Bind ciphertext to its account so database row swaps cannot swap factors.
    return cipher().encrypt(f"{user_id}:{secret}".encode()).decode()


def _decrypt(user_id: str, encrypted: str) -> str:
    try:
        value = cipher().decrypt(encrypted.encode()).decode()
        prefix = f"{user_id}:"
        if not value.startswith(prefix):
            raise InvalidToken
        return value[len(prefix):]
    except InvalidToken as exc:
        raise IdentityUnavailable("Account security key cannot decrypt stored factors") from exc


def _state(db: sqlite3.Connection, user_id: str):
    db.execute("INSERT OR IGNORE INTO account_security(user_id) VALUES (?)", (user_id,))
    return db.execute("SELECT * FROM account_security WHERE user_id = ?", (user_id,)).fetchone()


def status(data_dir: str, user_id: str) -> dict:
    with _conn(data_dir) as db:
        row = _state(db, user_id)
        count = db.execute("SELECT COUNT(*) FROM mfa_recovery_codes WHERE user_id = ?", (user_id,)).fetchone()[0]
    return {"email_verified": bool(row["email_verified"]), "mfa_enabled": bool(row["mfa_secret"]),
            "recovery_codes_remaining": count}


def issue_token(data_dir: str, user_id: str, purpose: str) -> str:
    if purpose not in ("verify", "reset"):
        raise ValueError("Invalid token purpose")
    raw = secrets.token_urlsafe(32)
    now = time.time()
    with _conn(data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM account_tokens WHERE expires_at <= ?", (now,))
        db.execute("DELETE FROM account_tokens WHERE user_id = ? AND purpose = ?", (user_id, purpose))
        db.execute("INSERT INTO account_tokens VALUES (?, ?, ?, ?)",
                   (_hash_token(raw), user_id, purpose, now + (3600 if purpose == "verify" else 1800)))
    return raw


def change_password_with_token(data_dir: str, raw: str, purpose: str, password: str) -> None:
    # Bound by route rate limits. Hash before taking the database write lock.
    hashed = PasswordHasher().hash(password)
    with _conn(data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        token = db.execute("SELECT * FROM account_tokens WHERE token_hash = ? AND purpose = ? AND expires_at > ?",
                           (_hash_token(raw), purpose, time.time())).fetchone()
        if token is None:
            raise IdentityError("This link is invalid or expired. Request a new link.")
        uid = token["user_id"]
        state = _state(db, uid)
        if purpose == "verify" and state["email_verified"]:
            raise IdentityError("This link is invalid or expired. Request a new link.")
        db.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?", (hashed, _now(), uid))
        db.execute("UPDATE account_security SET email_verified = 1, pending_secret = NULL, pending_until = NULL WHERE user_id = ?", (uid,))
        # Email recovery never disables an active second factor.
        db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM account_tokens WHERE user_id = ?", (uid,))


def totp(secret: str, counter: int, digits: int = 6) -> str:
    """RFC 6238 / RFC 4226 HMAC-SHA1, 30-second steps at the caller."""
    digest = hmac.new(base64.b32decode(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff
    return str(number % (10 ** digits)).zfill(digits)


def _counter(secret: str, code: str, last: int) -> int | None:
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        return None
    current = int(time.time()) // 30
    for counter in (current, current - 1, current + 1):
        if counter > last and hmac.compare_digest(totp(secret, counter), code):
            return counter
    return None


def _factor(db, user_id: str, state, code: str) -> bool:
    if not state["mfa_secret"]:
        return False
    counter = _counter(_decrypt(user_id, state["mfa_secret"]), code.strip(), state["last_counter"])
    if counter is not None:
        db.execute("UPDATE account_security SET last_counter = ? WHERE user_id = ?", (counter, user_id))
        return True
    normalized = code.strip().lower().replace("-", "")
    if len(normalized) != 32:
        return False
    return db.execute("DELETE FROM mfa_recovery_codes WHERE user_id = ? AND code_hash = ?",
                      (user_id, _hash_token(normalized))).rowcount == 1


# One fixed dummy hash gives unknown accounts the same Argon2 work as known accounts.
_DUMMY_HASH = PasswordHasher().hash(secrets.token_urlsafe(32))


def _password(row, password: str) -> bool:
    try:
        PasswordHasher().verify(row["password_hash"] if row else _DUMMY_HASH, password)
        return row is not None and not row["is_demo"]
    except (VerificationError, InvalidHashError):
        return False


def login_session(data_dir: str, email: str, password: str, code: str, previous: str | None) -> str:
    with _conn(data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        user = db.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        if not _password(user, password):
            raise IdentityError("Invalid email or password")
        state = _state(db, user["id"])
        if not state["email_verified"]:
            raise IdentityError("Verify your email before signing in. You can request a new verification link.")
        if state["mfa_secret"] and not _factor(db, user["id"], state, code):
            raise IdentityError("Enter a valid authenticator or recovery code. Each code can be used only once.")
        if previous:
            db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (_hash_token(previous),))
        return create_session(data_dir, user["id"], db=db)


def _reauth(db, user_id: str, session: str, password: str):
    user = db.execute("SELECT u.* FROM users u JOIN auth_sessions s ON u.id = s.user_id "
                      "WHERE u.id = ? AND s.token_hash = ? AND s.expires_at > ?",
                      (user_id, _hash_token(session), _now())).fetchone()
    if not _password(user, password):
        raise IdentityError("Your session or password is invalid. Sign in again and retry.")
    return _state(db, user_id)


def setup_mfa(data_dir: str, user_id: str, session: str, password: str) -> dict:
    secret = base64.b32encode(secrets.token_bytes(20)).decode()
    encrypted = _encrypt(user_id, secret)
    with _conn(data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        state = _reauth(db, user_id, session, password)
        if state["mfa_secret"]:
            raise IdentityError("MFA is already enabled. Disable it before replacing your authenticator.")
        db.execute("UPDATE account_security SET pending_secret = ?, pending_until = ? WHERE user_id = ?",
                   (encrypted, time.time() + 600, user_id))
        email = db.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()[0]
    uri = f"otpauth://totp/{quote('Kairo:' + email, safe='')}?secret={secret}&issuer=Kairo&algorithm=SHA1&digits=6&period=30"
    return {"secret": secret, "otpauth_uri": uri}


def _new_recovery_codes(db, user_id: str) -> list[str]:
    codes = [secrets.token_hex(16) for _ in range(10)]
    db.execute("DELETE FROM mfa_recovery_codes WHERE user_id = ?", (user_id,))
    db.executemany("INSERT INTO mfa_recovery_codes VALUES (?, ?)", [(user_id, _hash_token(c)) for c in codes])
    return ["-".join(c[i:i + 8] for i in range(0, 32, 8)) for c in codes]


def update_mfa(data_dir: str, user_id: str, session: str, password: str, code: str, action: str) -> dict:
    if action not in ("enable", "disable", "recovery"):
        raise ValueError("Invalid MFA action")
    with _conn(data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        state = _reauth(db, user_id, session, password)
        codes = []
        if action == "enable":
            if state["mfa_secret"] or not state["pending_secret"] or state["pending_until"] <= time.time():
                raise IdentityError("Authenticator setup expired or is already enabled. Start setup again.")
            counter = _counter(_decrypt(user_id, state["pending_secret"]), code.strip(), -1)
            if counter is None:
                raise IdentityError("Invalid authenticator code")
            db.execute("UPDATE account_security SET mfa_secret = pending_secret, pending_secret = NULL, "
                       "pending_until = NULL, last_counter = ? WHERE user_id = ?", (counter, user_id))
            codes = _new_recovery_codes(db, user_id)
        else:
            if not _factor(db, user_id, state, code):
                raise IdentityError("Invalid or previously used authenticator or recovery code")
            if action == "disable":
                db.execute("UPDATE account_security SET mfa_secret = NULL, pending_secret = NULL, "
                           "pending_until = NULL, last_counter = -1 WHERE user_id = ?", (user_id,))
                db.execute("DELETE FROM mfa_recovery_codes WHERE user_id = ?", (user_id,))
            else:
                codes = _new_recovery_codes(db, user_id)
        # All other devices must prove the new factor policy on their next login.
        db.execute("DELETE FROM auth_sessions WHERE user_id = ? AND token_hash != ?", (user_id, _hash_token(session)))
        db.execute("DELETE FROM account_tokens WHERE user_id = ?", (user_id,))
    return {"mfa_enabled": action != "disable", "recovery_codes": codes}
