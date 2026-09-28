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
    assert "Check your calendar directly" in reply
    assert "deleted" not in reply.lower()
    assert "write failed" not in reply.lower()
    assert wm.get_cached_pm("schedule") is None
    assert fallback_logs[0]["fallback_reply"] == reply


async def _async_value(value: str) -> str:
    return value


def test_pending_approval_bypasses_judge_and_humanizer(tmp_path, monkeypatch):
    config = _config(tmp_path)
    prompt = (
        "Approval required [6f4e04fe]: Remove schedule event: Morning run\n"
        "Risk: medium.\n"
        "Reply `approve 6f4e04fe` to go ahead, or `reject 6f4e04fe` to cancel."
    )
    action = StructuredAction("delete_event", None, ["Morning run"], 0.95,
                              "Delete my morning run", True)
    calls = []

    async def pending(*args):
        calls.append(args)
        return prompt

    def unexpected(*args, **kwargs):
        pytest.fail("Pending approval must not be judged, rewritten, or replaced by fallback")

    monkeypatch.setattr(orch, "_route", lambda *args: True)
    monkeypatch.setattr(orch, "_translate", lambda *args: action)
    monkeypatch.setattr(orch, "_call_pm", pending)
    monkeypatch.setattr(orch, "_judge", unexpected)
    monkeypatch.setattr(orch, "_humanize", unexpected)
    monkeypatch.setattr(orch, "log_fallback", unexpected)

    assert _done(_run_events("Delete my morning run", config)) == prompt
    assert len(calls) == 1
    from assistant.personal_manager.persistence.decision_log import list_turn_decisions
    decisions = list_turn_decisions(config.session_id, str(tmp_path))
    assert decisions[0]["routing"]["mode"] == "AWAITING_APPROVAL"


@pytest.mark.parametrize("verb", ["approve", "reject"])
@pytest.mark.parametrize("explicit_id", [True, False])
def test_approval_result_bypasses_models(tmp_path, monkeypatch, verb, explicit_id):
    from assistant.personal_manager.persistence.control_store import create_approval_request, find_approval_request
    from assistant.personal_manager.persistence.store import ScheduleData, ScheduleEntry, save_schedule, load_schedule
    config = _config(tmp_path)
    save_schedule(ScheduleData(entries=[ScheduleEntry(id="run1", title="Morning run")]),
                  config.session_id, str(tmp_path))
    approval = create_approval_request(
        config.session_id, str(tmp_path), action_type="schedule_remove",
        payload={"ids": ["run1"], "_thread_id": config.session_id},
        summary="Remove Morning run", risk_level="medium",
    )
    message = f"{verb} {approval.id}" if explicit_id else verb

    def unexpected(*args, **kwargs):
        pytest.fail("Approval service results must not pass through a model")

    monkeypatch.setattr(orch, "_route", unexpected)
    monkeypatch.setattr(orch, "_translate", unexpected)
    monkeypatch.setattr(orch, "_call_pm", unexpected)
    monkeypatch.setattr(orch, "_judge", unexpected)
    monkeypatch.setattr(orch, "_humanize", unexpected)
    reply = _done(_run_events(message, config))
    record = find_approval_request(str(tmp_path), approval.id, session_id=config.session_id)
    entries = load_schedule(config.session_id, str(tmp_path)).entries
    if verb == "approve":
        assert reply == "Removed 'Morning run' from your calendar."
        assert record.status == "executed"
        assert entries == []
        assert _done(_run_events(f"approve {approval.id}", config)) == reply
    else:
        assert reply == "Got it — cancelled that action."
        assert record.status == "rejected"
        assert len(entries) == 1


def test_approval_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    from assistant.personal_manager.application import approval_flow
    from assistant.personal_manager.persistence.control_store import create_approval_request, find_approval_request
    config = _config(tmp_path)
    approval = create_approval_request(
        config.session_id, str(tmp_path), action_type="schedule_remove",
        payload={"ids": ["run1"], "_thread_id": config.session_id},
        summary="Remove Morning run", risk_level="medium",
    )
    monkeypatch.setattr(approval_flow, "execute_pm_action", lambda *args: {"ok": False, "message": "Event could not be deleted."})
    assert _done(_run_events(f"approve {approval.id}", config)) == "Event could not be deleted."
    assert find_approval_request(str(tmp_path), approval.id, session_id=config.session_id).status == "failed"


def test_approval_command_cannot_execute_another_threads_action(tmp_path):
    from assistant.personal_manager.persistence.control_store import create_approval_request, find_approval_request
    config = _config(tmp_path)
    approval = create_approval_request(
        config.session_id, str(tmp_path), action_type="schedule_remove",
        payload={"ids": ["run1"], "_thread_id": "pm-other-thread"},
        summary="Remove Morning run", risk_level="medium",
    )
    assert "No approval request found" in _done(_run_events(f"approve {approval.id}", config))
    assert find_approval_request(str(tmp_path), approval.id, session_id=config.session_id).status == "pending"


def test_judge_cannot_trigger_a_second_write(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(orch, "_route", lambda *_: True)
    monkeypatch.setattr(orch, "_translate", lambda *_: StructuredAction(
        "add_event", None, [], .99, "add prep session", True))

    async def write(*_):
        calls.append(1)
        return "Uncertain write result"

    monkeypatch.setattr(orch, "_call_pm", write)
    monkeypatch.setattr(orch, "_judge", lambda *_: HarnessVerdict(
        "retry", .99, "try again", "add prep session again", "write_failed"))
    reply = _done(_run_events("Add prep session", _config(tmp_path)))
    assert len(calls) == 1
    assert "before trying again" in reply


def test_unavailable_judge_falls_back_without_leaking_exception(tmp_path, monkeypatch):
    def fail(*_):
        raise RuntimeError("private provider details")
    monkeypatch.setattr(orch, "build_llm", fail)
    action = StructuredAction("show_schedule", None, [], .9, "show schedule", False)
    result = orch._judge("Show schedule", action, "Interview at 2pm", "", _config(tmp_path))
    assert result.verdict == "fallback"
    assert "private" not in result.reason


def test_known_write_cannot_retry_even_if_translator_mislabels_it(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(orch, "_route", lambda *_: True)
    monkeypatch.setattr(orch, "_translate", lambda *_: StructuredAction(
        "delete_event", None, [], .99, "delete interview", False))

    async def write(*_):
        calls.append(1)
        return "Unknown outcome"

    monkeypatch.setattr(orch, "_call_pm", write)
    monkeypatch.setattr(orch, "_judge", lambda *_: HarnessVerdict(
        "retry", .99, "repeat", "delete interview", "write_failed"))
    _run_events("Delete interview", _config(tmp_path))
    assert len(calls) == 1
