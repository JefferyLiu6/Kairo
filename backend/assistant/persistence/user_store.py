"""User identity, session, and thread persistence (users.db)."""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass
class User:
    id: str
    email: str
    display_name: str
    is_demo: bool = False


@dataclass
class AuthSession:
    id: str
    user_id: str
    expires_at: str


@dataclass
class ChatThread:
    id: str
    user_id: str
    title: Optional[str]
    created_at: str
    last_active_at: str


# ── DB path ───────────────────────────────────────────────────────────────────

def _users_db_path(data_dir: str) -> str:
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "users.db")


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    is_demo         INTEGER NOT NULL DEFAULT 0,
    demo_expires_at TEXT
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash   TEXT UNIQUE NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_threads (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title          TEXT,
    created_at     TEXT NOT NULL,
    last_active_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_token_hash ON auth_sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_id    ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_chat_threads_user_id     ON chat_threads(user_id);
"""


def init_users_db(data_dir: str) -> None:
    conn = sqlite3.connect(_users_db_path(data_dir))
    conn.executescript(_SCHEMA)
    # Additive migrations for existing databases.
    for stmt in (
        "ALTER TABLE users ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN demo_expires_at TEXT",
    ):
        try:
            conn.execute(stmt)
            conn.commit()
        except sqlite3.OperationalError:
            pass
    conn.close()


def _conn(data_dir: str) -> sqlite3.Connection:
    db = sqlite3.connect(_users_db_path(data_dir))
    db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = sqlite3.Row
    return db


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return secrets.token_hex(16)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ── User CRUD ─────────────────────────────────────────────────────────────────

def create_user(data_dir: str, email: str, password: str, display_name: str) -> User:
    from argon2 import PasswordHasher
    ph = PasswordHasher()
    password_hash = ph.hash(password)
    uid = _new_id()
    now = _now()
    with _conn(data_dir) as db:
        db.execute(
            "INSERT INTO users (id, email, password_hash, display_name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uid, email.lower().strip(), password_hash, display_name, now, now),
        )
    return User(id=uid, email=email.lower().strip(), display_name=display_name)


def get_user_by_email(data_dir: str, email: str) -> Optional[User]:
    with _conn(data_dir) as db:
        row = db.execute(
            "SELECT id, email, display_name FROM users WHERE email = ?",
            (email.lower().strip(),),
        ).fetchone()
    if row is None:
        return None
    return User(id=row["id"], email=row["email"], display_name=row["display_name"])


def get_user_by_id(data_dir: str, user_id: str) -> Optional[User]:
    with _conn(data_dir) as db:
        row = db.execute(
            "SELECT id, email, display_name FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return User(id=row["id"], email=row["email"], display_name=row["display_name"])


def verify_password(data_dir: str, email: str, password: str) -> Optional[User]:
    """Return User if credentials are valid, else None."""
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, VerificationError
    try:
        from argon2.exceptions import InvalidHashError
    except ImportError:
        from argon2.exceptions import InvalidHash as InvalidHashError  # type: ignore[attr-defined]
    with _conn(data_dir) as db:
        row = db.execute(
            "SELECT id, email, password_hash, display_name FROM users WHERE email = ?",
            (email.lower().strip(),),
        ).fetchone()
    if row is None:
        return None
    ph = PasswordHasher()
    try:
        ph.verify(row["password_hash"], password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return None
    return User(id=row["id"], email=row["email"], display_name=row["display_name"])


# ── Session CRUD ──────────────────────────────────────────────────────────────

SESSION_TTL_DAYS = 30


def create_session(data_dir: str, user_id: str) -> str:
    """Create an auth session. Returns the raw (unhashed) token."""
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    sid = _new_id()
    now = _now()
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat()
    with _conn(data_dir) as db:
        db.execute(
            "INSERT INTO auth_sessions (id, user_id, token_hash, created_at, expires_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, user_id, token_hash, now, expires, now),
        )
    return raw_token


def get_session_user(data_dir: str, raw_token: str) -> Optional[User]:
    """Look up a session by raw token. Returns User if valid and not expired."""
    token_hash = _hash_token(raw_token)
    now = _now()
    with _conn(data_dir) as db:
        row = db.execute(
            "SELECT s.id as sid, s.expires_at, u.id, u.email, u.display_name, "
            "       COALESCE(u.is_demo, 0) as is_demo, u.demo_expires_at "
            "FROM auth_sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ?",
            (token_hash,),
        ).fetchone()
        if row is None or row["expires_at"] < now:
            return None
        # Expire demo accounts at the user level too (belt-and-suspenders).
        if row["is_demo"] and row["demo_expires_at"] and row["demo_expires_at"] < now:
            return None
        db.execute(
            "UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?",
            (now, token_hash),
        )
    return User(
        id=row["id"], email=row["email"], display_name=row["display_name"],
        is_demo=bool(row["is_demo"]),
    )


def delete_session(data_dir: str, raw_token: str) -> None:
    token_hash = _hash_token(raw_token)
    with _conn(data_dir) as db:
        db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))


# ── Chat thread CRUD ──────────────────────────────────────────────────────────

def ensure_thread(data_dir: str, user_id: str, thread_id: str, title: Optional[str] = None) -> ChatThread:
    """Create thread if it doesn't exist, or touch last_active_at."""
    now = _now()
    with _conn(data_dir) as db:
        existing = db.execute(
            "SELECT id, user_id, title, created_at, last_active_at FROM chat_threads WHERE id = ? AND user_id = ?",
            (thread_id, user_id),
        ).fetchone()
        if existing is None:
            db.execute(
                "INSERT INTO chat_threads (id, user_id, title, created_at, last_active_at) VALUES (?, ?, ?, ?, ?)",
                (thread_id, user_id, title, now, now),
            )
            return ChatThread(id=thread_id, user_id=user_id, title=title, created_at=now, last_active_at=now)
        db.execute(
            "UPDATE chat_threads SET last_active_at = ? WHERE id = ?", (now, thread_id)
        )
        return ChatThread(
            id=existing["id"], user_id=existing["user_id"], title=existing["title"],
            created_at=existing["created_at"], last_active_at=now,
        )


def list_threads(data_dir: str, user_id: str) -> list[ChatThread]:
    with _conn(data_dir) as db:
        rows = db.execute(
            "SELECT id, user_id, title, created_at, last_active_at FROM chat_threads "
            "WHERE user_id = ? ORDER BY last_active_at DESC",
            (user_id,),
        ).fetchall()
    return [
        ChatThread(id=r["id"], user_id=r["user_id"], title=r["title"],
                   created_at=r["created_at"], last_active_at=r["last_active_at"])
        for r in rows
    ]


def delete_thread(data_dir: str, user_id: str, thread_id: str) -> bool:
    with _conn(data_dir) as db:
        cursor = db.execute(
            "DELETE FROM chat_threads WHERE id = ? AND user_id = ?", (thread_id, user_id)
        )
    return cursor.rowcount > 0


# ── Demo user helpers ─────────────────────────────────────────────────────────

DEMO_TTL_HOURS = 24


def create_demo_user(data_dir: str) -> tuple[User, str]:
    """Create an ephemeral demo user. Returns (User, raw_session_token)."""
    uid = _new_id()
    email = f"demo_{uid}@local"
    now = _now()
    expires = (datetime.now(timezone.utc) + timedelta(hours=DEMO_TTL_HOURS)).isoformat()
    with _conn(data_dir) as db:
        db.execute(
            "INSERT INTO users "
            "(id, email, password_hash, display_name, created_at, updated_at, is_demo, demo_expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
            (uid, email, "", "Demo User", now, now, expires),
        )
    user = User(id=uid, email=email, display_name="Demo User", is_demo=True)
    token = create_session(data_dir, uid)
    return user, token


def delete_expired_demo_users(data_dir: str) -> list[str]:
    """Delete expired demo users from the DB; return their user_ids for filesystem cleanup."""
    now = _now()
    with _conn(data_dir) as db:
        rows = db.execute(
            "SELECT id FROM users WHERE is_demo = 1 AND demo_expires_at IS NOT NULL AND demo_expires_at < ?",
            (now,),
        ).fetchall()
        expired_ids = [r["id"] for r in rows]
        if expired_ids:
            db.executemany("DELETE FROM users WHERE id = ?", [(uid,) for uid in expired_ids])
    return expired_ids
