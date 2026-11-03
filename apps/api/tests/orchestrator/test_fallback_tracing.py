from __future__ import annotations

import asyncio

from langsmith import Client, get_current_run_tree, tracing_context

import kairo.agents.orchestrator.agent as orch
from kairo.agents.orchestrator.harness import HarnessVerdict
from kairo.agents.orchestrator.translator import StructuredAction
from kairo.core.observability import tracing


async def _async_value(value: str) -> str:
    return value


def _run(message: str, config: orch.OrchestratorConfig) -> list[tuple[str, str]]:
    async def collect():
        return [event async for event in orch.astream_orchestrator(message, config)]
    return asyncio.run(collect())


def test_fallback_turn_is_tagged_on_the_chat_turn_trace(tmp_path, monkeypatch):
    config = orch.OrchestratorConfig(
        session_id="pm-fallback-trace", data_dir=str(tmp_path),
        provider="test", model="test", pm_provider="test", pm_model="test",
    )
    action = StructuredAction("add_todo", None, [], 0.95, "add task call the bank", True)
    monkeypatch.setattr(orch, "_route", lambda *_a, **_k: True)
    monkeypatch.setattr(orch, "_translate", lambda *_a, **_k: action)
    monkeypatch.setattr(orch, "_call_pm", lambda *_a, **_k: _async_value("Error: secret account 1234 locked"))
    monkeypatch.setattr(orch, "_judge", lambda *_a, **_k: HarnessVerdict(
        "fallback", 0.9, "PM agent returned an error: secret account 1234 locked", "", "write_failed",
    ))
    monkeypatch.setattr(orch, "log_fallback", lambda *_a, **_k: None)

    seen = {}
    real_mark = tracing.mark_fallback

    def spy(**fields):
        real_mark(**fields)
        run = get_current_run_tree()
        seen.update(name=run.name, tags=list(run.tags or []), metadata=dict(run.metadata))

    monkeypatch.setattr(orch, "mark_fallback", spy)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setattr(Client, "create_run", lambda *a, **kw: None)
    monkeypatch.setattr(Client, "update_run", lambda *a, **kw: None)
    client = Client(api_key="test-key-not-real", api_url="http://127.0.0.1:9", auto_batch_tracing=False)

    with tracing_context(enabled=True, client=client):
        _run("add task call the bank", config)

    assert seen["name"] == "chat_turn"
    assert "fallback" in seen["tags"]
    assert seen["metadata"]["fallback_failure_type"] == "write_failed"
    assert seen["metadata"]["fallback_intent"] == "add_todo"
    assert seen["metadata"]["fallback_pm_error"] is True
    assert "1234" not in repr(seen["metadata"])


def test_mark_fallback_is_a_no_op_outside_a_trace():
    tracing.mark_fallback(failure_type="read_failed")
