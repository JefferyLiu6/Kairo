"""
SQLite-backed private journal with FTS5 full-text search.

Stored at: data/personal-manager/<encoded-session>/pm.db
Tables: journal_entries (primary), journal_fts (FTS5 content table)

Entries are sensitive — stored in the same per-session db as habits,
isolated from the shared vault.
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone


# ── Connection helper ──────────────────────────────────────────────────────────

def _conn(db_path: str) -> sqlite3.Connection:
    directory = os.path.dirname(db_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init(db_path: str) -> None:
    with _conn(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS journal_entries (
                id   TEXT PRIMARY KEY,
                ts   TEXT NOT NULL,
                body TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_journal_ts ON journal_entries(ts DESC);

            -- FTS5 content table — index tracks journal_entries via rowid
            CREATE VIRTUAL TABLE IF NOT EXISTS journal_fts
                USING fts5(body, content='journal_entries', content_rowid='rowid');

            -- Keep FTS in sync automatically
            CREATE TRIGGER IF NOT EXISTS journal_ai AFTER INSERT ON journal_entries BEGIN
                INSERT INTO journal_fts(rowid, body) VALUES (new.rowid, new.body);
            END;
            CREATE TRIGGER IF NOT EXISTS journal_ad AFTER DELETE ON journal_entries BEGIN
                INSERT INTO journal_fts(journal_fts, rowid, body)
                    VALUES ('delete', old.rowid, old.body);
            END;
            CREATE TRIGGER IF NOT EXISTS journal_au AFTER UPDATE ON journal_entries BEGIN
                INSERT INTO journal_fts(journal_fts, rowid, body)
                    VALUES ('delete', old.rowid, old.body);
                INSERT INTO journal_fts(rowid, body) VALUES (new.rowid, new.body);
            END;
        """)


# ── Public operations ──────────────────────────────────────────────────────────

def journal_append(body: str, db_path: str) -> str:
    _init(db_path)
    eid = uuid.uuid4().hex[:8]
    ts = datetime.now(timezone.utc).isoformat()
    with _conn(db_path) as conn:
        conn.execute(
            "INSERT INTO journal_entries (id, ts, body) VALUES (?, ?, ?)",
            (eid, ts, body.strip()),
        )
    return f"OK: journal entry [{eid}] saved at {ts[:10]}"


def journal_read(db_path: str, limit: int = 5) -> str:
    _init(db_path)
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, ts, body FROM journal_entries ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    if not rows:
        return "Journal: (no entries yet)"
    lines = ["## Recent journal entries"]
    for r in rows:
        lines.append(f"\n### [{r['id']}] {r['ts'][:10]}\n{r['body']}")
    return "\n".join(lines)


def journal_search(query: str, db_path: str, limit: int = 10) -> str:
    """Full-text search across journal entries using SQLite FTS5."""
    _init(db_path)
    if not query.strip():
        return "Error: search query cannot be empty"
    with _conn(db_path) as conn:
        try:
            rows = conn.execute(
                """
                SELECT je.id, je.ts, je.body
                FROM journal_fts
                JOIN journal_entries je ON je.rowid = journal_fts.rowid
                WHERE journal_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query.strip(), max(1, min(limit, 50))),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            return f"Search error: {exc}"
    if not rows:
        return f"Journal search: no entries matching '{query}'"
    lines = [f"## Journal search results for '{query}'"]
    for r in rows:
        lines.append(f"\n### [{r['id']}] {r['ts'][:10]}\n{r['body']}")
    return "\n".join(lines)
