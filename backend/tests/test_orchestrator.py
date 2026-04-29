from __future__ import annotations

import asyncio

import pytest

from assistant.orchestrator import agent as orch
from assistant.orchestrator import memory as orch_memory
from assistant.orchestrator.harness import HarnessVerdict
from assistant.orchestrator.translator import StructuredAction


@pytest.fixture(autouse=True)
def clear_orchestrator_memory():
    orch_memory._sessions.clear()
    yield
    orch_memory._sessions.clear()


def _config(tmp_path, session_id: str = "pm-test-orchestrator") -> orch.OrchestratorConfig:
    return orch.OrchestratorConfig(
        session_id=session_id,
        data_dir=str(tmp_path),
        provider="test",
        model="test",
        pm_provider="test",
        pm_model="test",
    )


def _run_events(message: str, config: orch.OrchestratorConfig) -> list[tuple[str, str]]:
    async def collect() -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        async for event in orch.astream_orchestrator(message, config):
            events.append(event)
        return events

    return asyncio.run(collect())


def _done(events: list[tuple[str, str]]) -> str:
    done_values = [value for kind, value in events if kind == "done"]
    assert done_values
    return done_values[-1]


def test_router_heuristic_delegates_pm_messages_without_llm(tmp_path, monkeypatch):
    config = _config(tmp_path)

    def fail_llm(*_args, **_kwargs):
        raise AssertionError("heuristic PM messages should not call router LLM")

    monkeypatch.setattr(orch, "_llm", fail_llm)

    assert orch._route("Add a dentist appointment tomorrow at 3pm", "", config) is True


def test_low_confidence_translation_routes_direct_without_pm(tmp_path, monkeypatch):
    config = _config(tmp_path)
    logs: list[dict[str, object]] = []

    monkeypatch.setattr(orch, "_route", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        orch,
        "_translate",
        lambda *_args, **_kwargs: StructuredAction(
            intent="delete_event",
            timeframe=None,
            entities=[],
            confidence=0.42,
            pm_prompt="delete something",
            is_write=True,
        ),
    )
    monkeypatch.setattr(
        orch,
        "_direct_reply",
        lambda *_args, **_kwargs: "Which calendar event should I change?",
    )

    async def fail_call_pm(*_args, **_kwargs):
        raise AssertionError("low-confidence translation must not call PM agent")

    def fake_log_turn(*args, **kwargs):
        logs.append({
            "route": args[2],
            "reason": args[3],
            "retry_count": kwargs.get("retry_count", 0),
        })

    monkeypatch.setattr(orch, "_call_pm", fail_call_pm)
    monkeypatch.setattr(orch, "_log_turn", fake_log_turn)

    events = _run_events("Delete the thing tomorrow", config)

    assert _done(events) == "Which calendar event should I change?"
    assert logs[0]["route"] == "DIRECT"
    assert "low confidence" in str(logs[0]["reason"])
    assert logs[0]["retry_count"] == 0


def test_retry_uses_harness_suggested_prompt_then_humanizes_success(tmp_path, monkeypatch):
    config = _config(tmp_path)
    pm_prompts: list[str] = []
    fallback_logs: list[object] = []
    turn_logs: list[dict[str, object]] = []

    action = StructuredAction(
        intent="show_schedule",
        timeframe="today",
        entities=[],
        confidence=0.96,
        pm_prompt="show schedule",
        is_write=False,
    )

    monkeypatch.setattr(orch, "_route", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(orch, "_translate", lambda *_args, **_kwargs: action)

    async def fake_call_pm(prompt: str, _config: orch.OrchestratorConfig) -> str:
        pm_prompts.append(prompt)
        if len(pm_prompts) == 1:
            return "Todo list: (empty)"
        return "Schedule: Product review at 1 PM."

    def fake_judge(_message, _action, pm_output, _profile, _config):
        if "Todo list" in pm_output:
            return HarnessVerdict(
                verdict="retry",
                confidence=0.93,
                reason="wrong data type",
                suggested_fix="show today's schedule",
                failure_type="irrelevant",
            )
        return HarnessVerdict(
            verdict="pass",
            confidence=0.98,
            reason="schedule answer matches request",
            suggested_fix="",
            failure_type="null",
        )

    def fake_log_turn(*args, **kwargs):
        turn_logs.append({
            "route": args[2],
            "verdict": args[5].verdict if args[5] else None,
            "retry_count": args[8] if len(args) > 8 else kwargs.get("retry_count", 0),
        })

    monkeypatch.setattr(orch, "_call_pm", fake_call_pm)
    monkeypatch.setattr(orch, "_judge", fake_judge)
    monkeypatch.setattr(
        orch,
        "_humanize",
        lambda _message, pm_output, _memory_ctx, _config: f"Humanized: {pm_output}",
    )
    monkeypatch.setattr(orch, "log_fallback", lambda *args, **kwargs: fallback_logs.append(args))
    monkeypatch.setattr(orch, "_log_turn", fake_log_turn)

    events = _run_events("What's on my schedule today?", config)

    assert pm_prompts == ["show schedule", "show today's schedule"]
    assert _done(events) == "Humanized: Schedule: Product review at 1 PM."
    assert fallback_logs == []
    assert turn_logs[-1] == {"route": "DELEGATE", "verdict": "pass", "retry_count": 1}


def test_read_fallback_uses_cached_snapshot_without_humanizer(tmp_path, monkeypatch):
    config = _config(tmp_path)
    orch_memory.get_working_memory(config.user_id, config.session_id).cache_pm(
        "schedule",
        "Schedule: cached deep work block at 10 AM.",
    )
    fallback_logs: list[dict[str, object]] = []

    action = StructuredAction(
        intent="show_schedule",
        timeframe="today",
        entities=[],
        confidence=0.95,
        pm_prompt="show schedule",
        is_write=False,
    )

    monkeypatch.setattr(orch, "_route", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(orch, "_translate", lambda *_args, **_kwargs: action)
    monkeypatch.setattr(orch, "_call_pm", lambda *_args, **_kwargs: _async_value("Error: DB down"))
    monkeypatch.setattr(
        orch,
        "_judge",
        lambda *_args, **_kwargs: HarnessVerdict(
            verdict="fallback",
            confidence=0.91,
            reason="read failed",
            suggested_fix="",
            failure_type="read_failed",
        ),
    )

    def fail_humanize(*_args, **_kwargs):
        raise AssertionError("fallback replies should not be humanized")

    def fake_log_fallback(*_args, **kwargs):
        fallback_logs.append(kwargs)

    monkeypatch.setattr(orch, "_humanize", fail_humanize)
    monkeypatch.setattr(orch, "log_fallback", fake_log_fallback)
    monkeypatch.setattr(orch, "_log_turn", lambda *_args, **_kwargs: None)

    events = _run_events("What's on my schedule today?", config)
    reply = _done(events)

    assert "wasn't able to pull that up fresh" in reply
    assert "cached deep work block at 10 AM" in reply
    assert fallback_logs[0]["retry_count"] == 0


def test_write_failure_never_claims_success_and_invalidates_cache(tmp_path, monkeypatch):
    config = _config(tmp_path)
    wm = orch_memory.get_working_memory(config.user_id, config.session_id)
    wm.cache_pm("schedule", "Schedule: stale snapshot")
    fallback_logs: list[dict[str, object]] = []

    action = StructuredAction(
        intent="delete_event",
        timeframe="tomorrow",
        entities=["deep work block"],
        confidence=0.97,
        pm_prompt="delete deep work tomorrow",
        is_write=True,
    )

    monkeypatch.setattr(orch, "_route", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(orch, "_translate", lambda *_args, **_kwargs: action)
    monkeypatch.setattr(orch, "_call_pm", lambda *_args, **_kwargs: _async_value("Error: write failed"))
    monkeypatch.setattr(
        orch,
        "_judge",
        lambda *_args, **_kwargs: HarnessVerdict(
            verdict="fallback",
            confidence=0.96,
            reason="write failed",
            suggested_fix="",
            failure_type="write_failed",
        ),
    )
    monkeypatch.setattr(orch, "log_fallback", lambda *_args, **kwargs: fallback_logs.append(kwargs))
    monkeypatch.setattr(orch, "_log_turn", lambda *_args, **_kwargs: None)

    events = _run_events("Delete my deep work block tomorrow", config)
    reply = _done(events)

    assert "couldn't confirm that was saved" in reply
    assert "check your calendar directly" in reply
    assert "deleted" not in reply.lower()
    assert "write failed" not in reply.lower()
    assert wm.get_cached_pm("schedule") is None
    assert fallback_logs[0]["fallback_reply"] == reply


async def _async_value(value: str) -> str:
    return value
