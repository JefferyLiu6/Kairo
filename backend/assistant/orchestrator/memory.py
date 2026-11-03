"""Working memory and long-term profile loading for the orchestrator."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from assistant.personal_manager.persistence.control_store import append_orchestrator_turn, list_orchestrator_turns


MAX_TURNS = 12  # user+assistant exchanges kept as prompt context


@dataclass
class WorkingMemory:
    """Per-user, per-thread orchestrator context.

    Turns are written through to SQLite, so the conversation context survives a
    restart or redeploy. The PM snapshot cache is a 60-second cache and stays in
    process. Without a data_dir (some tests), turns are kept in memory only.
    """
    owner: str = ""
    thread_id: str = ""
    data_dir: Optional[str] = None
    turns: list[dict[str, str]] = field(default_factory=list)
    pm_cache: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ── turn history ──────────────────────────────────────────────────────────

    def add_turn(self, role: str, content: str, *, max_turns: int = MAX_TURNS) -> None:
        self.turns.append({"role": role, "content": content})
        if len(self.turns) > max_turns * 2:
            self.turns = self.turns[-(max_turns * 2):]
        if self.data_dir:
            append_orchestrator_turn(self.owner, self.thread_id, self.data_dir, role, content, keep=max_turns * 2)

    def format_history(self) -> str:
        if not self.turns:
            return ""
        lines = []
        for t in self.turns:
            prefix = "User" if t["role"] == "user" else "Assistant"
            lines.append(f"{prefix}: {t['content']}")
        return "\n".join(lines)

    # ── PM snapshot cache ─────────────────────────────────────────────────────

    def cache_pm(self, key: str, data: str, ttl: int = 60) -> None:
        self.pm_cache[key] = {"data": data, "expires_at": time.time() + ttl}

    def get_cached_pm(self, key: str) -> str | None:
        entry = self.pm_cache.get(key)
        if entry and time.time() < entry["expires_at"]:
            return entry["data"]
        self.pm_cache.pop(key, None)
        return None

    def invalidate_pm_cache(self) -> None:
        self.pm_cache.clear()


# In-process registry: (user_id, thread_id, data_dir) → WorkingMemory.
# Holds the cache; a fresh process rebuilds the turns from SQLite on first use.
_sessions: dict[tuple[str, str, Optional[str]], WorkingMemory] = {}


def get_working_memory(user_id: str, thread_id: str, data_dir: Optional[str] = None) -> WorkingMemory:
    key = (user_id, thread_id, data_dir)
    if key not in _sessions:
        owner = user_id or thread_id
        turns = list_orchestrator_turns(owner, thread_id, data_dir, limit=MAX_TURNS * 2) if data_dir else []
        _sessions[key] = WorkingMemory(owner=owner, thread_id=thread_id, data_dir=data_dir, turns=turns)
    return _sessions[key]


# ── Long-term profile loading ─────────────────────────────────────────────────

def load_profile(user_data_dir: str) -> str:
    """Return PROFILE.md from the user's data directory, or empty string."""
    path = os.path.join(user_data_dir, "PROFILE.md")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def build_memory_context(user_id: str, thread_id: str, user_data_dir: str, data_dir: Optional[str] = None) -> str:
    """Build the memory block injected into every orchestrator prompt."""
    wm = get_working_memory(user_id, thread_id, data_dir)
    profile = load_profile(user_data_dir)

    sections: list[str] = []

    if profile:
        sections.append(f"## User profile\n{profile}")

    history = wm.format_history()
    if history:
        sections.append(f"## Recent conversation\n{history}")

    return "\n\n".join(sections)
