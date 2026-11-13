"""Running the blind eval: pinned clock, one fresh user per scenario, a report that can be gated."""
from __future__ import annotations

import datetime as dt
import json
import os

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from evals.blind.adapters import PATHS, PathUnavailable, PMWorkflowPath, Sent, count_model_calls
from evals.blind.runner import render_summary, run_blind_eval, save_report
from evals.blind.snapshot import Snapshot
from evals.blind.spec import BlindSet, load_blind_set

TINY = {
    "version": 1, "now": "2026-11-12T10:00:00-05:00", "timezone": "America/Toronto",
    "scenarios": [
        {"id": "a", "persona": "writes a todo", "split": "dev", "entities": ["milk"], "turns": [
            {"say": "add milk", "expect": {"kind": "write", "adds": [
                {"in": "todos", "match": {"title": {"has": ["milk"]}}}]}},
            {"say": "what should i do", "expect": {"kind": "open"}},
        ]},
        {"id": "b", "persona": "is never understood", "split": "holdout", "entities": ["gym"], "turns": [
            {"say": "gym mon wed fri 7am", "expect": {"kind": "write", "adds": [
                {"in": "schedule", "match": {"title": {"has": ["gym"]}}}]}},
        ]},
    ],
}


class ScriptedSession:
    """Stores a todo for any message containing 'add', and records the clock it saw."""

    def __init__(self, seen: list):
        self.todos: dict[str, dict] = {}
        self.seen = seen

    def send(self, message: str) -> Sent:
        self.seen.append((message, dt.date.today().isoformat(), os.environ.get("KAIRO_TIMEZONE")))
        if message.startswith("boom"):
            raise RuntimeError("model exploded")
        if message.startswith("add "):
            self.todos["t1"] = {"id": "t1", "title": message[4:], "done": False, "due": None}
            return Sent(reply="Added.", model_calls=2, input_tokens=300, output_tokens=40)
        return Sent(reply="I'm not sure how to act on that.")

    def snapshot(self) -> Snapshot:
        return Snapshot(stores={"todos": dict(self.todos)})


class ScriptedPath:
    name = "scripted"

    def __init__(self):
        self.seen: list = []
        self.opened: list[str] = []

    def describe(self) -> dict:
        return {"model": "none"}

    def open(self, scenario_id: str, data_dir: str) -> ScriptedSession:
        assert os.path.isdir(data_dir)
        self.opened.append(scenario_id)
        return ScriptedSession(self.seen)


def tiny() -> BlindSet:
    return BlindSet.model_validate(TINY)


def test_each_scenario_runs_in_its_own_session_on_the_pinned_date(monkeypatch):
    monkeypatch.setenv("KAIRO_TIMEZONE", "UTC")
    path = ScriptedPath()

    run_blind_eval(tiny(), path)

    assert path.opened == ["a", "b"]
    assert {seen_date for _, seen_date, _ in path.seen} == {"2026-11-12"}
    assert {zone for _, _, zone in path.seen} == {"America/Toronto"}
    assert os.environ["KAIRO_TIMEZONE"] == "UTC"  # the caller's settings come back afterwards


def test_the_report_scores_every_turn_and_splits_the_metrics():
    report = run_blind_eval(tiny(), ScriptedPath())

    turns = [(s["id"], t["outcome"]) for s in report["scenarios"] for t in s["turns"]]
    assert turns == [("a", "right"), ("a", "unscored"), ("b", "miss")]
    assert report["metrics"]["overall"]["right_rate"] == 0.5
    assert report["metrics"]["dev"]["right_rate"] == 1.0 and report["metrics"]["holdout"]["right_rate"] == 0.0
    assert report["path"] == {"name": "scripted", "model": "none"}
    assert report["fixture"]["turns"] == 3 and report["fixture"]["now"] == "2026-11-12T10:00:00-05:00"


def test_the_report_carries_latency_and_model_usage():
    report = run_blind_eval(tiny(), ScriptedPath(), usd_per_mtok=(0.15, 0.60))
    overall = report["metrics"]["overall"]

    first = report["scenarios"][0]["turns"][0]
    assert first["model_calls"] == 2 and first["input_tokens"] == 300 and first["seconds"] >= 0
    assert overall["model_calls_per_turn"] == round(2 / 3, 2)
    assert overall["input_tokens"] == 300 and overall["output_tokens"] == 40
    assert overall["cost_usd"] == round(300 * 0.15 / 1e6 + 40 * 0.60 / 1e6, 6)
    assert overall["latency_median_s"] is not None and overall["latency_p95_s"] is not None


def test_the_gate_fails_until_the_quality_bar_is_met():
    report = run_blind_eval(tiny(), ScriptedPath())
    assert report["gate"]["passed"] is False
    assert any("right_rate" in failure for failure in report["gate"]["failed"])


def test_a_crash_in_the_path_is_recorded_as_that_turns_reply_and_the_run_continues():
    crashing = BlindSet.model_validate({**TINY, "scenarios": [
        {"id": "c", "persona": "crashes", "split": "dev", "entities": ["x"], "turns": [
            {"say": "boom", "expect": {"kind": "open"}},
            {"say": "add milk", "expect": {"kind": "write", "adds": [{"in": "todos", "match": {}}]}},
        ]}]})

    report = run_blind_eval(crashing, ScriptedPath())

    first, second = report["scenarios"][0]["turns"]
    assert first["error"] == "RuntimeError: model exploded" and first["outcome"] == "miss"
    assert second["outcome"] == "right"


def test_scenarios_can_be_selected_and_an_unknown_one_is_an_error():
    path = ScriptedPath()
    report = run_blind_eval(tiny(), path, only=["b"])
    assert path.opened == ["b"] and report["fixture"]["turns"] == 1
    with pytest.raises(ValueError, match="nope"):
        run_blind_eval(tiny(), ScriptedPath(), only=["nope"])


def test_each_finished_turn_is_reported_as_it_happens():
    seen = []
    run_blind_eval(tiny(), ScriptedPath(), on_turn=lambda scenario, record: seen.append((scenario.id, record["say"])))
    assert seen == [("a", "add milk"), ("a", "what should i do"), ("b", "gym mon wed fri 7am")]


def test_a_saved_report_is_never_overwritten(tmp_path):
    report = run_blind_eval(tiny(), ScriptedPath())
    target = tmp_path / "out" / "report.json"
    save_report(report, target)
    assert json.loads(target.read_text())["metrics"] == report["metrics"]
    with pytest.raises(FileExistsError):
        save_report(report, target)


def test_the_summary_shows_the_bar_next_to_each_result():
    text = render_summary(run_blind_eval(tiny(), ScriptedPath()))
    assert "scripted" in text and "Right" in text and "70%" in text and "FAIL" in text
    assert "gym mon wed fri 7am" in text  # every turn that was not right is listed with its reason


# ── Adapters ──────────────────────────────────────────────────────────────────

def test_model_calls_and_tokens_are_counted_for_any_chat_model_in_the_turn():
    model = FakeListChatModel(responses=["one", "two"])
    with count_model_calls() as usage:
        model.invoke("hello")
        model.invoke("again")
    assert usage.calls == 2
    with count_model_calls() as untouched:
        pass
    assert untouched.calls == 0


def test_the_known_paths_are_registered_by_name():
    assert {"pm", "orchestrator", "graph"} <= set(PATHS)


def test_the_graph_path_reports_its_bundle_and_needs_credentials_like_web_chat(monkeypatch, tmp_path):
    described = PATHS["graph"]().describe()
    assert len(described["bundle"]) == 8 and "coordinator" in described["prompts"]
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OLLAMA_API_KEY", "OLLAMA", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(PathUnavailable):
        PATHS["graph"]().open("x", str(tmp_path))


def test_the_orchestrator_path_refuses_to_run_without_model_credentials(monkeypatch, tmp_path):
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OLLAMA_API_KEY", "OLLAMA", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(PathUnavailable, match="credentials"):
        PATHS["orchestrator"]().open("x", str(tmp_path))


def test_the_workflow_path_never_calls_a_model_even_when_a_key_is_present(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    session = PMWorkflowPath().open("x", str(tmp_path))

    sent = session.send("what should i focus on today")  # falls through to the chat fallback

    assert sent.model_calls == 0 and "not sure how to act" in sent.reply
    assert os.environ["OPENAI_API_KEY"] == "sk-test-not-real"


def test_the_workflow_path_reproduces_the_trial_findings_on_the_freelancer_scenario():
    """Regression against 2026-11-12 trial output; remove with the workflow path when it is retired."""
    report = run_blind_eval(load_blind_set(), PMWorkflowPath(), only=["01_freelancer"])
    outcomes = [turn["outcome"] for turn in report["scenarios"][0]["turns"]]

    assert outcomes[0] == "miss"          # "client call with Northwind thurs 2-3pm" — not understood
    assert outcomes[2] == "wrong_save"    # deadline "due monday 5pm" stored at 2 AM
    assert outcomes[4] == "right"         # discovery call next wednesday at 11am
    assert outcomes[6:8] == ["right", "right"]  # move to 4pm waits for approval, then applies
    assert report["metrics"]["overall"]["model_calls_per_turn"] == 0


# ── Command line ──────────────────────────────────────────────────────────────

def test_the_command_prints_the_summary_and_only_fails_when_gated(capsys, tmp_path, monkeypatch):
    import evals.blind.__main__ as cli
    from evals.blind.__main__ import main

    monkeypatch.setattr(cli, "load_dotenv", lambda *_a, **_k: None)  # keep real credentials out of the test run
    target = tmp_path / "report.json"
    assert main(["--path", "pm", "--scenario", "08_privacy_engineer", "--output", str(target)]) == 0
    printed = capsys.readouterr().out
    assert "path: pm" in printed and "Gate:" in printed and str(target) in printed
    assert json.loads(target.read_text())["fixture"]["turns"] == 9

    assert main(["--path", "pm", "--scenario", "08_privacy_engineer", "--no-save", "--gate"]) == 1


def test_the_command_needs_both_prices_or_neither():
    from evals.blind.__main__ import main

    with pytest.raises(SystemExit):
        main(["--path", "pm", "--usd-in", "0.15"])
