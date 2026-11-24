"""Idempotency keys: a request sent twice with the same key is carried out once.

The client makes up a key for each message it sends and sends the same key on every retry. The
first request with a key claims it and runs; one that comes while that is still running watches it
(see kairo.api.turns); one that comes after it finished gets the stored reply back without running
anything. A key reused for a different request is refused, so a key can never return someone else's
answer. Keys are kept per user, in users.db, for KEY_TTL.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator, Literal, Optional

KEY_TTL = timedelta(hours=24)
MAX_KEY_LENGTH = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS idempotency_keys (
    user_id      TEXT NOT NULL,
    key          TEXT NOT NULL,
    fingerprint  TEXT NOT NULL,
    status       TEXT NOT NULL,          -- running | done
    response     TEXT,
    created_at   TEXT NOT NULL,
    finished_at  TEXT,
    PRIMARY KEY (user_id, key)
);
CREATE INDEX IF NOT EXISTS idx_idempotency_created ON idempotency_keys(created_at);
"""


@dataclass(frozen=True)
class Claim:
    """What to do with a request that carries a key.

    new:      nobody has run it; this request now owns the key and must call finish().
    running:  a request with this key is still being carried out.
    done:     it already finished; `response` is what it returned.
    mismatch: the key was used for a different request.
    """
    outcome: Literal["new", "running", "done", "mismatch"]
    response: Optional[str] = None


def fingerprint(*parts: str) -> str:
    """A digest of what a request asks for, to tell a retry from a different request under the same key."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def valid_key(key: str) -> bool:
    return 0 < len(key) <= MAX_KEY_LENGTH and key.isprintable() and not key.isspace()


def claim(data_dir: str, user_id: str, key: str, request_fingerprint: str) -> Claim:
    """Claim `key` for this user's request, or report what already happened under it. Atomic."""
    now = datetime.now(timezone.utc)
    with _conn(data_dir) as db:
        db.execute("DELETE FROM idempotency_keys WHERE created_at < ?", ((now - KEY_TTL).isoformat(),))
        inserted = db.execute(
            "INSERT OR IGNORE INTO idempotency_keys (user_id, key, fingerprint, status, created_at) "
            "VALUES (?, ?, ?, 'running', ?)",
            (user_id, key, request_fingerprint, now.isoformat()),
        ).rowcount
        if inserted:
            return Claim("new")
        row = db.execute(
            "SELECT fingerprint, status, response FROM idempotency_keys WHERE user_id = ? AND key = ?",
            (user_id, key),
        ).fetchone()
    if row is None:                       # expired and removed between the two statements
        return claim(data_dir, user_id, key, request_fingerprint)
    if row["fingerprint"] != request_fingerprint:
        return Claim("mismatch")
    if row["status"] == "done":
        return Claim("done", row["response"])
    return Claim("running")


def finish(data_dir: str, user_id: str, key: str, response: str) -> None:
    """Record what the request under `key` returned, for any retry that comes later."""
    with _conn(data_dir) as db:
        db.execute(
            "UPDATE idempotency_keys SET status = 'done', response = ?, finished_at = ? "
            "WHERE user_id = ? AND key = ?",
            (response, datetime.now(timezone.utc).isoformat(), user_id, key),
        )


def release(data_dir: str, user_id: str, key: str) -> None:
    """Give up a claim before anything was done under it (the request was refused), so a retry can run."""
    with _conn(data_dir) as db:
        db.execute("DELETE FROM idempotency_keys WHERE user_id = ? AND key = ? AND status = 'running'",
                   (user_id, key))


@contextmanager
def _conn(data_dir: str) -> Iterator[sqlite3.Connection]:
    os.makedirs(data_dir, exist_ok=True)
    db = sqlite3.connect(os.path.join(data_dir, "users.db"), timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.executescript(_SCHEMA)
        yield db
        db.commit()
    finally:
        db.close()
