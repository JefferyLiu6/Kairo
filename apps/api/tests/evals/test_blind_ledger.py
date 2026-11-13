"""The scores ledger: every recorded eval run, with the prompts and model that produced it."""
from __future__ import annotations

import json

import pytest

from evals import prompt_versions
from evals.blind.adapters import OrchestratorPath, PMWorkflowPath
from evals.blind.ledger import LEDGER, append, entry_from_report, load, prompt_set, render_history, scored

REPORT = {
    "path": {"name": "orchestrator", "provider": "openai", "model": "gpt-4o-mini", "pm_model": "gpt-4o-mini",
             "prompt_set": "abc123def456", "prompts": {"translate": "1a478512", "judge": "c8742e9d"}},
    "fixture": {"sha256": "e515cf5b8d8b", "now": "2026-11-12T10:00:00-05:00", "turns": 85},
    "code": {"commit": "34bf142", "dirty": False},
    "started_at": "2026-11-12T23:56:39.123456+00:00",
    "metrics": {
        "overall": {"turns": 85, "scoreable": 64, "right": 25, "right_rate": 0.3906, "wrong_save": 12,
                    "false_claim": 2, "wrong_but_reported_done": 14, "wrong_but_reported_done_rate": 0.1647,
                    "trapped": 3, "miss": 22, "blocked": 13, "unscored": 8, "latency_median_s": 3.196,
                    "latency_p95_s": 6.333, "model_calls_per_turn": 2.04, "input_tokens": 259842,
                    "output_tokens": 13604, "cost_usd": 0.047139},
        "dev": {"right_rate": 0.42}, "holdout": {"right_rate": 0.3333},
    },
    "gate": {"passed": False, "failed": ["right_rate 0.3906 is below 0.7"]},
}


def test_the_prompt_set_is_one_short_name_for_every_prompt_version_together():
    versions = {"translate": "1a478512", "judge": "c8742e9d"}
    assert prompt_set(versions) == prompt_set(dict(reversed(versions.items())))  # order does not matter
    assert prompt_set(versions) != prompt_set({**versions, "judge": "00000000"})
    assert len(prompt_set(versions)) == 12 and prompt_set({}) is None


def test_an_entry_carries_what_was_run_and_how_it_scored():
    entry = entry_from_report(REPORT, "with receipts", report_file="orchestrator-20261112T235639Z.json")

    assert entry["recorded_at"] == "2026-11-12T23:56:39+00:00" and entry["note"] == "with receipts"
    assert (entry["path"], entry["model"], entry["pm_model"]) == ("orchestrator", "gpt-4o-mini", "gpt-4o-mini")
    assert entry["prompt_set"] == "abc123def456" and entry["prompts"]["translate"] == "1a478512"
    assert entry["bundle"] is None
    assert entry_from_report({**REPORT, "path": {**REPORT["path"], "bundle": "0f1e2d3c"}}, "n")["bundle"] == "0f1e2d3c"
    assert entry["eval_set"] == "e515cf5b8d8b" and entry["turns"] == 85 and entry["code"] == "34bf142"
    assert (entry["right_rate"], entry["wrong_but_reported_done_rate"], entry["trapped"]) == (0.3906, 0.1647, 3)
    assert (entry["dev_right_rate"], entry["holdout_right_rate"]) == (0.42, 0.3333)
    assert entry["model_calls_per_turn"] == 2.04 and entry["latency_median_s"] == 3.196
    assert entry["gate_passed"] is False and entry["report"] == "orchestrator-20261112T235639Z.json"


def test_uncommitted_code_is_marked_so_a_score_is_not_pinned_to_the_wrong_commit():
    dirty = {**REPORT, "code": {"commit": "34bf142", "dirty": True}}
    assert entry_from_report(dirty, "n")["code"] == "34bf142+uncommitted"


def test_an_entry_needs_a_note_saying_why_the_run_was_made():
    with pytest.raises(ValueError, match="note"):
        entry_from_report(REPORT, "  ")


def test_the_ledger_only_ever_grows(tmp_path):
    ledger = tmp_path / "scores.jsonl"
    first, second = entry_from_report(REPORT, "baseline"), entry_from_report(REPORT, "with receipts")

    append(first, ledger)
    before = ledger.read_text()
    append(second, ledger)

    assert ledger.read_text().startswith(before) and [e["note"] for e in load(ledger)] == ["baseline", "with receipts"]
    assert all(json.loads(line) for line in ledger.read_text().splitlines())
    assert load(tmp_path / "missing.jsonl") == []


def test_scored_says_whether_a_prompt_set_has_a_recorded_run_on_a_path(tmp_path):
    ledger = tmp_path / "scores.jsonl"
    append(entry_from_report(REPORT, "baseline"), ledger)
    assert scored("abc123def456", ledger=ledger)
    assert not scored("abc123def456", path="pm", ledger=ledger)
    assert not scored("000000000000", ledger=ledger)


def test_history_puts_runs_side_by_side_with_their_notes():
    worse = {**entry_from_report(REPORT, "baseline"), "right_rate": 0.3385, "trapped": 7}
    text = render_history([worse, entry_from_report(REPORT, "with receipts")])
    lines = text.splitlines()
    assert "baseline" in lines[-2] and "34%" in lines[-2] and "with receipts" in lines[-1] and "39%" in lines[-1]
    assert "abc123def456" in text and "gpt-4o-mini" in text
    assert render_history([]) == "No runs recorded yet."


def test_a_path_says_which_prompts_it_runs():
    described = OrchestratorPath().describe()
    assert described["prompts"] == prompt_versions.current()
    assert described["prompt_set"] == prompt_set(prompt_versions.current())
    assert PMWorkflowPath().describe()["prompt_set"] is None  # the typed workflow sends no prompt


def test_the_current_prompts_have_a_recorded_web_chat_score():
    """A prompt edit changes the prompt set. It is not done until the blind eval has been run on it."""
    current = prompt_set(prompt_versions.current())
    assert scored(current, ledger=LEDGER), (
        f"No blind-eval score is recorded for the current prompts (set {current}).\n"
        'Run: make eval-blind path=orchestrator record="what changed and why"\n'
        "then commit apps/api/evals/scores.jsonl with the prompt change."
    )


# ── Command line ──────────────────────────────────────────────────────────────

@pytest.fixture
def cli(monkeypatch):
    import evals.blind.__main__ as main_module

    monkeypatch.setattr(main_module, "load_dotenv", lambda *_a, **_k: None)
    return main_module.main


def test_record_appends_a_full_run_to_the_ledger(cli, tmp_path, capsys):
    ledger = tmp_path / "scores.jsonl"
    assert cli(["--path", "pm", "--no-save", "--record", "workflow baseline", "--ledger", str(ledger)]) == 0
    (entry,) = load(ledger)
    assert entry["note"] == "workflow baseline" and entry["path"] == "pm" and entry["turns"] == 117
    assert "Recorded in" in capsys.readouterr().out


def test_a_partial_run_cannot_be_recorded(cli, tmp_path):
    with pytest.raises(SystemExit):
        cli(["--path", "pm", "--scenario", "01_freelancer", "--record", "x", "--ledger", str(tmp_path / "s.jsonl")])


def test_history_prints_the_ledger_without_running_anything(cli, tmp_path, capsys):
    ledger = tmp_path / "scores.jsonl"
    append(entry_from_report(REPORT, "with receipts"), ledger)
    assert cli(["--history", "--ledger", str(ledger)]) == 0
    assert "with receipts" in capsys.readouterr().out
