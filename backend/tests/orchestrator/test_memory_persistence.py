"""Orchestrator conversation context survives restarts (Phase 4d)."""
from __future__ import annotations

import pytest

import assistant.orchestrator.agent as orch
from assistant.orchestrator import memory
from assistant.personal_manager.persistence.control_store import delete_thread_data, list_orchestrator_turns


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
    wm.cache_pm("schedule", "cached snapshot")

    _restart(monkeypatch)
    again = memory.get_working_memory("user-1", "pm-thread", str(tmp_path))

    assert again.turns == [
        {"role": "user", "content": "I'm planning a trip to Lisbon"},
        {"role": "assistant", "content": "Nice, when are you going?"},
    ]
    assert again.get_cached_pm("schedule") is None  # the cache is in-process by design


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


def test_deleting_a_thread_deletes_its_orchestrator_turns(tmp_path, monkeypatch):
    memory.get_working_memory("user-1", "pm-a", str(tmp_path)).add_turn("user", "forget me")
    memory.get_working_memory("user-1", "pm-b", str(tmp_path)).add_turn("user", "keep me")

    delete_thread_data("user-1", "pm-a", str(tmp_path))

    _restart(monkeypatch)
    assert memory.get_working_memory("user-1", "pm-a", str(tmp_path)).turns == []
    assert memory.get_working_memory("user-1", "pm-b", str(tmp_path)).turns[0]["content"] == "keep me"


def test_a_real_turn_is_in_the_prompt_context_after_a_restart(tmp_path, monkeypatch):
    config = orch.OrchestratorConfig(user_id="user-1", session_id="pm-thread", data_dir=str(tmp_path),
                                     provider="test", model="test", pm_provider="test", pm_model="test")
    monkeypatch.setattr(orch, "_route", lambda *_a, **_k: False)
    monkeypatch.setattr(orch, "_direct_reply", lambda *_a, **_k: "Lisbon in May sounds lovely.")
    orch.run_orchestrator("I'm going to Lisbon in May", config)

    _restart(monkeypatch)
    context = memory.build_memory_context(config.user_id, config.session_id, str(tmp_path), config.data_dir)

    assert "User: I'm going to Lisbon in May" in context
    assert "Assistant: Lisbon in May sounds lovely." in context
