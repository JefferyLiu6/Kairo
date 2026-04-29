"""Working memory and long-term profile loading for the orchestrator."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkingMemory:
    """Per-user, per-thread in-process state. Discarded when process restarts."""
    turns: list[dict[str, str]] = field(default_factory=list)
    open_threads: list[str] = field(default_factory=list)
    pending_context: str = ""
    pm_cache: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ── turn history ──────────────────────────────────────────────────────────

    def add_turn(self, role: str, content: str, *, max_turns: int = 12) -> None:
        self.turns.append({"role": role, "content": content})
        if len(self.turns) > max_turns * 2:
            self.turns = self.turns[-(max_turns * 2):]

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


# Module-level registry: (user_id, thread_id) → WorkingMemory
_sessions: dict[tuple[str, str], WorkingMemory] = {}


def get_working_memory(user_id: str, thread_id: str) -> WorkingMemory:
    key = (user_id, thread_id)
    if key not in _sessions:
        _sessions[key] = WorkingMemory()
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


def build_memory_context(user_id: str, thread_id: str, user_data_dir: str) -> str:
    """Build the memory block injected into every orchestrator prompt."""
    wm = get_working_memory(user_id, thread_id)
    profile = load_profile(user_data_dir)

    sections: list[str] = []

    if profile:
        sections.append(f"## User profile\n{profile}")

    history = wm.format_history()
    if history:
        sections.append(f"## Recent conversation\n{history}")

    if wm.open_threads:
        threads = "\n".join(f"- {t}" for t in wm.open_threads)
        sections.append(f"## Open threads\n{threads}")

    if wm.pending_context:
        sections.append(f"## Pending context\n{wm.pending_context}")

    return "\n\n".join(sections)
