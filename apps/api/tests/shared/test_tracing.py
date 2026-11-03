from __future__ import annotations

import asyncio
from types import SimpleNamespace

from kairo.core.observability.tracing import _final_reply, _safe_inputs, tag_turn, traced


def test_safe_inputs_never_records_config_or_clients():
    inputs = {
        "message": "add golf tomorrow",
        "config": SimpleNamespace(api_key="sk-secret"),
        "llm": object(),
        "api_key": "sk-secret",
        "pm_api_key": "sk-secret",
    }
    assert _safe_inputs(inputs) == {"message": "add golf tomorrow"}


def test_final_reply_keeps_only_done_event():
    events = [("progress", "Thinking…"), ("token", "Hi"), ("done", "Hi there")]
    assert _final_reply(events) == {"reply": "Hi there"}
    assert _final_reply([("progress", "Thinking…")]) == {"reply": None}


def test_traced_functions_behave_the_same_with_tracing_off():
    @traced("sync_stage")
    def add(a, b, config=None):
        return a + b

    @traced("stream_stage")
    async def stream(config=None):
        yield ("token", "a")
        yield ("done", "ab")

    async def collect():
        return [event async for event in stream()]

    assert add(1, 2, config=object()) == 3
    assert asyncio.run(collect()) == [("token", "a"), ("done", "ab")]


def test_traced_accepts_config_by_keyword_when_tracing_is_on(monkeypatch):
    # langsmith reads a `config` keyword as a LangChain RunnableConfig; ours is not one.
    from langsmith import Client, get_current_run_tree, tracing_context

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    client = Client(api_key="test-key-not-real", api_url="http://127.0.0.1:9", auto_batch_tracing=False)
    monkeypatch.setattr(Client, "create_run", lambda *a, **kw: None)
    monkeypatch.setattr(Client, "update_run", lambda *a, **kw: None)
    seen = {}

    @traced("stage")
    def stage(message, config):
        tag_turn(config)
        run = get_current_run_tree()
        seen.update(inputs=run.inputs, metadata=dict(run.metadata))
        return message.upper()

    config = SimpleNamespace(api_key="sk-secret", user_id="user-1", session_id="pm-1", model="m")
    with tracing_context(enabled=True, client=client):
        assert stage("hi", config=config) == "HI"

    assert seen["inputs"] == {"message": "hi"}
    assert "sk-secret" not in repr(seen)
    assert "user-1" not in repr(seen) and len(seen["metadata"]["user"]) == 16


def test_tag_turn_is_a_no_op_outside_a_trace():
    tag_turn(SimpleNamespace(user_id="u1", session_id="s1", provider="openai", model="m"))
