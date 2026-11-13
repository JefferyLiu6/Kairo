"""Prompt versioning: every prompt is declared, snapshotted, and recorded on traces."""
from __future__ import annotations

import ast
from pathlib import Path

from langsmith import Client, get_current_run_tree, tracing_context

from evals import prompt_versions as pv
from kairo.core.observability.tracing import traced
import kairo.core.prompt as prompt_module
from kairo.core.prompt import Prompt, PromptTemplate, prompt, record_prompt

KAIRO = Path(__file__).resolve().parents[1] / "kairo"


def test_prompt_snapshot_is_up_to_date():
    diff = pv.changes(pv.saved(), pv.current())
    assert not diff, "Prompts changed since the snapshot:\n" + "\n".join(diff) + "\nRun `make prompt-versions` to accept."


def test_every_module_that_declares_a_prompt_is_in_the_snapshot_list():
    declaring = set()
    for path in KAIRO.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in {"prompt", "prompt_template"} and path.name != "prompt.py"):
                declaring.add(".".join(path.relative_to(KAIRO.parent).with_suffix("").parts))
    assert declaring == set(pv.PROMPT_MODULES)


def test_version_follows_the_text():
    a, b = Prompt("t", "Say hi."), Prompt("t", "Say hello.")
    assert a.version != b.version and a.version == Prompt("t", "Say hi.").version
    assert a.id == f"t@{a.version}" and isinstance(a, str) and a == "Say hi."


def test_a_template_version_tracks_its_wording_not_its_values():
    template = PromptTemplate("dated", 'Today is $today. Reply as {"ok": true}.')
    first, second = template.render(today="2026-11-03"), template.render(today="2026-11-04")
    assert first.version == second.version == template.version
    assert first == 'Today is 2026-11-03. Reply as {"ok": true}.'


def test_two_different_prompts_cannot_share_a_name(monkeypatch):
    monkeypatch.setattr(prompt_module, "_REGISTRY", dict(prompt_module._REGISTRY))  # keep the real snapshot clean
    prompt("unique_test_prompt", "one")
    prompt("unique_test_prompt", "one")  # re-declaring the same text is fine (module reloads)
    try:
        prompt("unique_test_prompt", "two")
    except ValueError as exc:
        assert "unique_test_prompt" in str(exc)
    else:
        raise AssertionError("expected a name clash")


def test_record_prompt_tags_the_current_trace(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setattr(Client, "create_run", lambda *a, **k: None)
    monkeypatch.setattr(Client, "update_run", lambda *a, **k: None)
    client = Client(api_key="test-key-not-real", api_url="http://127.0.0.1:9", auto_batch_tracing=False)
    seen = {}
    system = Prompt("route", "You are a routing classifier.")

    @traced("route")
    def stage():
        record_prompt(system)
        record_prompt("plain text is ignored")
        seen.update(get_current_run_tree().metadata)

    with tracing_context(enabled=True, client=client):
        stage()

    assert seen["prompt.route"] == system.version
    assert not any(key.startswith("prompt.") and key != "prompt.route" for key in seen)


def test_a_real_stage_records_its_prompt_version(monkeypatch, tmp_path):
    """route/translate/judge/humanize all pass through _invoke, which records the prompt."""
    import json

    import kairo.agents.orchestrator.agent as orch
    from kairo.agents.orchestrator.prompts import TRANSLATOR_SYSTEM

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setattr(Client, "create_run", lambda *a, **k: None)
    monkeypatch.setattr(Client, "update_run", lambda *a, **k: None)
    client = Client(api_key="test-key-not-real", api_url="http://127.0.0.1:9", auto_batch_tracing=False)
    seen = {}

    class FakeModel:
        def invoke(self, messages):
            seen.update(get_current_run_tree().metadata)
            reply = dict(intent="add_todo", timeframe=None, entities=[], confidence=0.9,
                         pm_prompt="add task call mom", is_write=True)
            return type("Reply", (), {"content": json.dumps(reply), "usage_metadata": {}})()

    monkeypatch.setattr(orch, "_llm", lambda *_a, **_k: FakeModel())
    config = orch.OrchestratorConfig(session_id="pm-x", data_dir=str(tmp_path), provider="test",
                                     model="test", pm_provider="test", pm_model="test")
    with tracing_context(enabled=True, client=client):
        assert orch._translate("add task call mom", "", config).intent == "add_todo"

    assert seen["prompt.translate"] == TRANSLATOR_SYSTEM.version
