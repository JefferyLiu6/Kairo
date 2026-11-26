"""A thread's recent turns survive a restart and stay with their thread and user."""
from __future__ import annotations

import pytest

from kairo.agents import conversation_memory as memory
from kairo.storage.control_store import delete_thread_data, list_orchestrator_turns


@pytest.fixture(autouse=True)
def fresh_process(monkeypatch):
    monkeypatch.setattr(memory, "_sessions", {})


def _restart(monkeypatch):
    """What a redeploy does to the in-process registry."""
    monkeypatch.setattr(memory, "_sessions", {})


def test_turns_are_restored_after_a_restart(tmp_path, monkeypatch):
    wm = memory.get_working_memory("user-1", "pm-thread", str(tmp_path))
    wm.add_turn("user", "I'm planning a trip to Lisbon")
    wm.add_turn("assistant", "Nice, when are you going?")

    _restart(monkeypatch)
    again = memory.get_working_memory("user-1", "pm-thread", str(tmp_path))

    assert again.turns == [
        {"role": "user", "content": "I'm planning a trip to Lisbon"},
        {"role": "assistant", "content": "Nice, when are you going?"},
    ]


def test_only_the_newest_turns_are_kept(tmp_path, monkeypatch):
    wm = memory.get_working_memory("user-1", "pm-thread", str(tmp_path))
    for i in range(memory.MAX_TURNS * 2 + 5):
        wm.add_turn("user", f"message {i}")

    stored = list_orchestrator_turns("user-1", "pm-thread", str(tmp_path), limit=1000)
    assert len(stored) == memory.MAX_TURNS * 2 == len(wm.turns)
    assert stored[-1]["content"] == f"message {memory.MAX_TURNS * 2 + 4}"
    assert stored == wm.turns


def test_threads_and_users_do_not_share_context(tmp_path):
    memory.get_working_memory("user-1", "pm-a", str(tmp_path)).add_turn("user", "thread A secret")
    assert memory.get_working_memory("user-1", "pm-b", str(tmp_path)).turns == []
    assert memory.get_working_memory("user-2", "pm-a", str(tmp_path)).turns == []


def test_deleting_a_thread_deletes_its_turns(tmp_path, monkeypatch):
    memory.get_working_memory("user-1", "pm-a", str(tmp_path)).add_turn("user", "forget me")
    memory.get_working_memory("user-1", "pm-b", str(tmp_path)).add_turn("user", "keep me")

    delete_thread_data("user-1", "pm-a", str(tmp_path))

    _restart(monkeypatch)
    assert memory.get_working_memory("user-1", "pm-a", str(tmp_path)).turns == []
    assert memory.get_working_memory("user-1", "pm-b", str(tmp_path)).turns[0]["content"] == "keep me"
