import json
from pathlib import Path

import pytest

from assistant.orchestrator import agent
from assistant.orchestrator.final_eval import TITLE, capture_final, snapshot
from assistant.orchestrator.memory import _sessions
from assistant.personal_manager.persistence.store import _todos_path, todo_add


def test_real_orchestrator_control_flow_with_stubbed_models(monkeypatch):
    monkeypatch.setattr(agent, "build_llm", lambda *_: object())

    def invoke(_llm, system, payload):
        request = payload.split("User message: ", 1)[-1].split("\n", 1)[0]
        if system == agent.TRANSLATOR_SYSTEM:
            intent = "show_todos" if request.startswith("List") else "complete_todo" if request.startswith("Mark") else "add_todo"
            return json.dumps(dict(intent=intent, timeframe=None, entities=[], confidence=.99,
                                   pm_prompt=request, is_write=intent != "show_todos"))
        if system == agent.HARNESS_SYSTEM:
            return json.dumps(dict(verdict="pass", confidence=.9, reason="fixture", suggested_fix="", failure_type="null"))
        if system == agent.HUMANIZER_SYSTEM:
            return "Final reply: " + payload.split("PM agent result:\n")[1].split("\n\nContext:")[0]
        raise AssertionError("Unexpected model call")

    monkeypatch.setattr(agent, "_invoke", invoke)
    result = capture_final(agent.OrchestratorConfig(session_id="test", provider="offline", pm_provider="offline"))
    assert result["summary"] == dict(attempted_turns=3, captured_replies=3, state_checks_passed=3, execution_errors=0)
    assert all(case["response"].startswith("Final reply:") for case in result["cases"])
    assert all(case["expected"] is None for case in result["cases"])
    assert result["observations"][-1]["after"][0]["done"] is True


def test_success_words_do_not_pass_state_checks(tmp_path):
    config = agent.OrchestratorConfig(session_id="real-thread", data_dir=str(tmp_path))
    result = capture_final(config, execute=lambda *_: "Done! Saved successfully.")
    assert result["summary"]["captured_replies"] == 3
    assert result["summary"]["state_checks_passed"] == 0
    assert list(tmp_path.iterdir()) == []


def test_mutation_before_exception_is_observed_and_not_invented_as_reply():
    def execute(request, config):
        if request.startswith("Add"):
            todo_add(TITLE, None, config.user_id, config.data_dir)
        raise ValueError("secret provider payload")
    result = capture_final(agent.OrchestratorConfig(session_id="x"), execute)
    assert result["cases"] == []
    assert result["summary"]["execution_errors"] == 3
    assert result["observations"][0]["state_check_passed"] is True
    assert "secret provider payload" not in json.dumps(result)


def test_capture_cleans_its_memory_without_touching_other_sessions():
    sentinel = object()
    _sessions[("existing", "thread")] = sentinel
    used = []
    def execute(_request, config):
        key = (config.user_id, config.session_id)
        used.append(key)
        _sessions[key] = object()
        return "reply"
    try:
        capture_final(agent.OrchestratorConfig(session_id="x"), execute)
        assert all(key not in _sessions for key in used)
        assert _sessions[("existing", "thread")] is sentinel
    finally:
        _sessions.pop(("existing", "thread"))


def test_corrupt_snapshot_is_not_treated_as_empty(tmp_path):
    config = agent.OrchestratorConfig(session_id="x", user_id="eval", data_dir=str(tmp_path))
    path = Path(_todos_path(config.user_id, config.data_dir))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("broken JSON")
    with pytest.raises(ValueError):
        snapshot(config)


def test_cli_requires_live_opt_in_before_execution(tmp_path, monkeypatch):
    from assistant.orchestrator import final_eval
    monkeypatch.setattr("sys.argv", ["final_eval", "--model", "unused", "--output-dir", str(tmp_path / "run")])
    monkeypatch.setattr(final_eval, "capture_final", lambda *_: pytest.fail("Must not execute"))
    with pytest.raises(SystemExit) as exc:
        final_eval.main()
    assert exc.value.code == 2
    assert not (tmp_path / "run").exists()


def test_cli_packages_failure_evidence_and_monitoring(tmp_path, monkeypatch):
    from assistant.orchestrator import final_eval
    from assistant.orchestrator.telemetry import emit, span
    destination = tmp_path / "run"
    monkeypatch.setattr("sys.argv", ["final_eval", "--live", "--model", "stub", "--output-dir", str(destination)])
    def fake_capture(_config):
        with span("turn"):
            emit("turn_result", outcome="fallback")
        return {"cases": [], "observations": [],
                "summary": {"execution_errors": 1, "state_checks_passed": 0}}
    monkeypatch.setattr(final_eval, "capture_final", fake_capture)
    assert final_eval.main() == 1
    assert set(p.name for p in destination.iterdir()) == {"cases.json", "run.json", "monitor.json", "telemetry.jsonl"}
    assert json.loads((destination / "monitor.json").read_text())["fallback_rate"] == 1
    assert json.loads((destination / "run.json").read_text())["metadata"]["model"] == "stub"
