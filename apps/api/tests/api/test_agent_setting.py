"""KAIRO_AGENT chooses which agent answers web chat."""
from __future__ import annotations

import pytest

import kairo.agents.graph as graph_module
import kairo.agents.orchestrator.agent as orchestrator_module
import kairo.api.app as app_module
import kairo.storage.user_store as user_store


@pytest.fixture
def chat(authed_client, monkeypatch):
    async def graph(message, _config):
        yield ("token", f"graph: {message}")

    async def previous_assistant(message, _config):
        yield ("token", f"assistant: {message}")

    monkeypatch.setattr(graph_module, "astream_graph", graph)
    monkeypatch.setattr(orchestrator_module, "astream_orchestrator", previous_assistant)
    monkeypatch.setattr(app_module, "_rate_limit", lambda *_a, **_k: None)
    monkeypatch.setattr(app_module, "_consume_credit_or_402", lambda *_a, **_k: None)
    monkeypatch.setattr(user_store, "ensure_thread", lambda *_a, **_k: None)

    def send(message: str) -> str:
        response = authed_client.post("/orchestrator/stream", json={"message": message, "session_id": "setting"})
        assert response.status_code == 200
        return response.text

    return send


def test_web_chat_runs_on_the_agent_graph_by_default(chat, monkeypatch):
    monkeypatch.delenv("KAIRO_AGENT", raising=False)
    assert "graph: add milk" in chat("add milk")
    monkeypatch.setenv("KAIRO_AGENT", "graph")
    assert "graph: add milk" in chat("add milk")
    monkeypatch.setenv("KAIRO_AGENT", "something-else")      # an unknown value does not switch the agent off
    assert "graph: add milk" in chat("add milk")


def test_the_previous_assistant_is_one_setting_away(chat, monkeypatch):
    monkeypatch.setenv("KAIRO_AGENT", "Legacy ")   # case and stray spaces do not matter
    assert "assistant: add milk" in chat("add milk")
