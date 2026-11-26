"""The recent turns of a chat thread, kept so the next turn can read them."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from kairo.storage.control_store import append_orchestrator_turn, list_orchestrator_turns


MAX_TURNS = 12  # user+assistant exchanges kept as prompt context


@dataclass
class WorkingMemory:
    """A thread's recent turns, per user.

    Turns are written through to SQLite, so the conversation context survives a
    restart or redeploy. Without a data_dir (some tests), turns are kept in memory only.
    """
    owner: str = ""
    thread_id: str = ""
    data_dir: Optional[str] = None
    turns: list[dict[str, str]] = field(default_factory=list)

    # ── turn history ──────────────────────────────────────────────────────────

    def add_turn(self, role: str, content: str, *, max_turns: int = MAX_TURNS) -> None:
        self.turns.append({"role": role, "content": content})
        if len(self.turns) > max_turns * 2:
            self.turns = self.turns[-(max_turns * 2):]
        if self.data_dir:
            append_orchestrator_turn(self.owner, self.thread_id, self.data_dir, role, content, keep=max_turns * 2)


# In-process registry: (user_id, thread_id, data_dir) → WorkingMemory.
# A fresh process rebuilds the turns from SQLite on first use.
_sessions: dict[tuple[str, str, Optional[str]], WorkingMemory] = {}


def get_working_memory(user_id: str, thread_id: str, data_dir: Optional[str] = None) -> WorkingMemory:
    key = (user_id, thread_id, data_dir)
    if key not in _sessions:
        owner = user_id or thread_id
        turns = list_orchestrator_turns(owner, thread_id, data_dir, limit=MAX_TURNS * 2) if data_dir else []
        _sessions[key] = WorkingMemory(owner=owner, thread_id=thread_id, data_dir=data_dir, turns=turns)
    return _sessions[key]
