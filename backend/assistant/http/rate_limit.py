"""SQLite-backed rate limiting helpers for public HTTP routes."""
from __future__ import annotations

import hashlib
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Iterable

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class RateLimitRule:
    """Maximum events allowed inside a sliding window."""

    limit: int
    window_seconds: int
    label: str


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("rate limit exceeded")
        self.retry_after = max(1, retry_after)


def parse_limit(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_limit(name: str, default: int) -> int:
    return parse_limit(os.environ.get(name), default)


def client_ip(request: Request) -> str:
    """Return the caller IP.

    Proxy headers are trusted only when TRUST_PROXY_HEADERS is explicitly enabled.
    This prevents arbitrary clients from spoofing rate-limit buckets by sending
    their own X-Forwarded-For header in local/private deployments.
    """

    trust_proxy = os.environ.get("TRUST_PROXY_HEADERS", "").strip().lower()
    if trust_proxy in {"1", "true", "yes"}:
        cf_ip = request.headers.get("CF-Connecting-IP", "").strip()
        if cf_ip:
            return cf_ip
        forwarded = request.headers.get("X-Forwarded-For", "").strip()
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def bucket(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()
    return f"{kind}:{digest}"


def request_ip_bucket(request: Request, kind: str = "ip") -> str:
    return bucket(kind, client_ip(request))


def email_bucket(email: str, kind: str = "email") -> str:
    return bucket(kind, email)


def user_bucket(user_id: str, kind: str = "user") -> str:
    return bucket(kind, user_id)


def _db_path(data_dir: str) -> str:
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "rate_limits.db")


def _conn(data_dir: str) -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(data_dir), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rate_limit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bucket TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rate_limit_events_bucket_time "
        "ON rate_limit_events(bucket, created_at)"
    )
    return conn


def check_rate_limit(
    data_dir: str,
    buckets: Iterable[str],
    rules: Iterable[RateLimitRule],
    *,
    now: float | None = None,
) -> None:
    active_buckets = [b for b in buckets if b]
    active_rules = [r for r in rules if r.limit > 0 and r.window_seconds > 0]
    if not active_buckets or not active_rules:
        return

    current = time.time() if now is None else now
    max_window = max(rule.window_seconds for rule in active_rules)
    oldest_needed = current - max_window

    with _conn(data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM rate_limit_events WHERE created_at < ?", (oldest_needed,))

        retry_after = 0
        for bucket_key in active_buckets:
            for rule in active_rules:
                cutoff = current - rule.window_seconds
                count = db.execute(
                    "SELECT COUNT(*) FROM rate_limit_events WHERE bucket = ? AND created_at >= ?",
                    (bucket_key, cutoff),
                ).fetchone()[0]
                if count >= rule.limit:
                    oldest = db.execute(
                        "SELECT MIN(created_at) FROM rate_limit_events "
                        "WHERE bucket = ? AND created_at >= ?",
                        (bucket_key, cutoff),
                    ).fetchone()[0]
                    retry_after = max(
                        retry_after,
                        int(math.ceil((oldest + rule.window_seconds) - current)),
                    )

        if retry_after > 0:
            raise RateLimitExceeded(retry_after)

        db.executemany(
            "INSERT INTO rate_limit_events (bucket, created_at) VALUES (?, ?)",
            [(bucket_key, current) for bucket_key in active_buckets],
        )


def enforce_rate_limit(
    data_dir: str,
    buckets: Iterable[str],
    rules: Iterable[RateLimitRule],
    *,
    detail: str = "Too many requests. Please try again later.",
) -> None:
    try:
        check_rate_limit(data_dir, buckets, rules)
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=detail,
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
