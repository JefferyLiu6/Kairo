"""Web chat (`/orchestrator/stream`) runs on the agent graph and streams what it did."""
from __future__ import annotations

import pytest

import kairo.agents.graph as graph_module
import kairo.api.app as app_module
import kairo.storage.user_store as user_store


@pytest.fixture
def chat(authed_client, monkeypatch):
    async def graph(message, _config):
        yield ("token", f"graph: {message}")
        yield ("outcome", "acted")

    monkeypatch.setattr(graph_module, "astream_graph", graph)
    monkeypatch.setattr(app_module, "_rate_limit", lambda *_a, **_k: None)
    monkeypatch.setattr(app_module, "_consume_credit_or_402", lambda *_a, **_k: None)
    monkeypatch.setattr(user_store, "ensure_thread", lambda *_a, **_k: None)

    def send(message: str) -> str:
        response = authed_client.post("/orchestrator/stream", json={"message": message, "session_id": "setting"})
        assert response.status_code == 200
        return response.text

    return send


def test_web_chat_runs_on_the_agent_graph(chat, monkeypatch):
    monkeypatch.setenv("KAIRO_AGENT", "legacy")   # the old switch is gone and changes nothing
    assert "graph: add milk" in chat("add milk")


def test_the_stream_says_whether_the_turn_carried_anything_out(chat):
    assert 'data: {"outcome": "acted"}' in chat("add milk")
