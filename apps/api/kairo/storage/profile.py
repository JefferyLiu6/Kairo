"""Profile.md append + dirty-state tracking for background reconciliation."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

_PROFILE_FILENAME = "PROFILE.md"
_META_FILENAME = "profile_meta.json"

_WRITES_THRESHOLD = 3       # reconcile immediately after this many writes
_IDLE_RECON_MINUTES = 30    # also reconcile if dirty and last recon was this long ago

_meta_lock = threading.Lock()


def profile_path(vault_dir: str) -> str:
    """Profile path — vault_dir may be a vault dir or a per-user data dir."""
    return os.path.join(vault_dir, _PROFILE_FILENAME)


def _meta_path(data_dir: str, session_id: str) -> str:
    sid_dir = os.path.join(data_dir, session_id)
    os.makedirs(sid_dir, exist_ok=True)
    return os.path.join(sid_dir, _META_FILENAME)


def _read_meta(data_dir: str, session_id: str) -> dict[str, Any]:
    path = _meta_path(data_dir, session_id)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"writes_since_reconciliation": 0, "last_reconciled_at": None, "last_write_at": None}


def _write_meta(data_dir: str, session_id: str, meta: dict[str, Any]) -> None:
    path = _meta_path(data_dir, session_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f)


def append_profile_fact(vault_dir: str, data_dir: str, session_id: str, fact: str) -> None:
    os.makedirs(vault_dir, exist_ok=True)
    with open(profile_path(vault_dir), "a", encoding="utf-8") as f:
        f.write(f"\n- {fact}")
    with _meta_lock:
        meta = _read_meta(data_dir, session_id)
        meta["writes_since_reconciliation"] = meta.get("writes_since_reconciliation", 0) + 1
        meta["last_write_at"] = datetime.now(timezone.utc).isoformat()
        _write_meta(data_dir, session_id, meta)


def should_reconcile(data_dir: str, session_id: str) -> bool:
    meta = _read_meta(data_dir, session_id)
    writes = meta.get("writes_since_reconciliation", 0)
    if writes == 0:
        return False
    if writes >= _WRITES_THRESHOLD:
        return True
    last = meta.get("last_reconciled_at")
    if last is None:
        return True
    try:
        age_minutes = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 60
        return age_minutes >= _IDLE_RECON_MINUTES
    except Exception:
        return True


def mark_reconciled(data_dir: str, session_id: str) -> None:
    with _meta_lock:
        meta = _read_meta(data_dir, session_id)
        meta["writes_since_reconciliation"] = 0
        meta["last_reconciled_at"] = datetime.now(timezone.utc).isoformat()
        _write_meta(data_dir, session_id, meta)


def read_profile(vault_dir: str) -> str:
    path = profile_path(vault_dir)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def write_profile_atomic(vault_dir: str, content: str) -> None:
    path = profile_path(vault_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)
