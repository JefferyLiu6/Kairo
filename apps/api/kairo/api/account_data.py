"""What an account holds: hand it to its owner as a zip, or erase it."""
from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from kairo.storage import idempotency, identity
from kairo.storage.calendar_store import list_calendar_accounts
from kairo.storage.control_store import pm_db_path
from kairo.storage.store import pm_dir
from kairo.storage.user_store import User, list_threads, user_created_at
from kairo.tools.calendar.service import CalendarService

# Files in the account's directory that are the user's own records, readable as they are.
_PLAIN_SUFFIXES = {".json", ".md", ".txt"}

_README = """\
Kairo data export

account.json   Your account: email, name, when it was created, your chat threads.
files/         Records Kairo keeps as files: your schedule, tasks, private notes
               and the profile of what it remembers about you.
tables/        Records Kairo keeps in its database, one file per table: chat
               messages, habits, journal entries, remembered facts, preferences,
               approvals, the audit trail, and events copied from a connected calendar.

Left out:
- Sign-in credentials and the tokens Kairo holds for a connected calendar. They
  are access to an account, not records about you.
- The journal's search index, which repeats the journal entries.
- The agent's saved working state for unfinished turns. The chat messages
  themselves are in tables/.
"""


def export_account(data_dir: str, user: User) -> bytes:
    """Everything Kairo holds about the account, as a zip of JSON and text files."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", _README)
        archive.writestr("account.json", _json(_account_record(data_dir, user)))
        directory = Path(pm_dir(user.id, data_dir))
        if directory.is_dir():
            for path in sorted(directory.rglob("*")):
                if path.is_file() and path.suffix.lower() in _PLAIN_SUFFIXES:
                    archive.write(path, f"files/{path.relative_to(directory).as_posix()}")
        for table, rows in _tables(pm_db_path(user.id, data_dir)).items():
            archive.writestr(f"tables/{table}.json", _json(rows))
    return buffer.getvalue()


def erase_account_data(data_dir: str, user_id: str) -> None:
    """Erase what the account stored outside its row in users.db.

    Run before the row is deleted: if this fails the account still exists and the
    deletion can be tried again.
    """
    if os.path.exists(pm_db_path(user_id, data_dir)):
        service = CalendarService(user_id, data_dir)
        for account in list_calendar_accounts(user_id, data_dir, provider="google"):
            service.disconnect_google_account(account.id)
    idempotency.forget_user(data_dir, user_id)
    directory = pm_dir(user_id, data_dir)
    if os.path.isdir(directory):
        shutil.rmtree(directory)


def _account_record(data_dir: str, user: User) -> dict[str, Any]:
    security = identity.status(data_dir, user.id)
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "created_at": user_created_at(data_dir, user.id),
        "email_verified": security["email_verified"],
        "authenticator_enabled": security["mfa_enabled"],
        "credits_remaining": user.credits_remaining,
        "chat_threads": [
            {"id": thread.id, "title": thread.title, "created_at": thread.created_at,
             "last_active_at": thread.last_active_at}
            for thread in list_threads(data_dir, user.id)
        ],
    }


def _tables(db_path: str) -> dict[str, list[dict[str, Any]]]:
    """Every table of records in the account's database, as rows of column to value."""
    if not os.path.exists(db_path):
        return {}
    with closing(sqlite3.connect(f"file:{quote(db_path)}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        listed = db.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
        # A search index is a virtual table plus the tables SQLite keeps behind it.
        indexes = [row["name"] for row in listed if (row["sql"] or "").upper().startswith("CREATE VIRTUAL")]
        tables: dict[str, list[dict[str, Any]]] = {}
        for row in listed:
            name = row["name"]
            if name.startswith("sqlite_") or any(name == i or name.startswith(f"{i}_") for i in indexes):
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            tables[name] = [
                {column: _value(record[column]) for column in record.keys() if not _is_credential(column)}
                for record in db.execute(f"SELECT * FROM {quoted}")
            ]
    return tables


def _is_credential(column: str) -> bool:
    name = column.lower()
    return name.endswith("token") or "secret" in name or "password" in name


def _value(value: Any) -> Any:
    # Bytes are derived data with no readable form; say how much there was.
    return {"bytes": len(value)} if isinstance(value, bytes) else value


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)
