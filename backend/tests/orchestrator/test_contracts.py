"""Orchestrator stage contracts: strict model output, safe fallbacks, prompts in step."""
from __future__ import annotations

import json
import re

import pytest

import assistant.orchestrator.agent as orch
from assistant.orchestrator import prompts
from assistant.orchestrator.contracts import (
    TRANSLATOR_INTENTS,
    JudgeOutput,
    RouterOutput,
    TranslatorOutput,
)
from assistant.orchestrator.harness import UNPARSED_VERDICT_REASON, parse_harness_verdict
from assistant.orchestrator.translator import parse_translator_response

_VALID_JUDGE = dict(verdict="pass", confidence=0.9, reason="ok", suggested_fix="", failure_type="null")


# ── Judge keeps its fail-closed rules ─────────────────────────────────────────

@pytest.mark.parametrize("change", [
    {"confidence": True},            # bool is not a number
    {"confidence": "0.9"},           # string is not a number
    {"confidence": 1.5},             # outside 0..1
    {"reason": 3},                   # strings must be strings
    {"verdict": "maybe"},
    {"failure_type": "timeout"},
    {"verdict": "retry", "suggested_fix": "  "},
    {"extra": "field"},
])
def test_judge_rejects_malformed_verdicts(change):
    raw = json.dumps({**_VALID_JUDGE, **change})
    verdict = parse_harness_verdict(raw)
    assert verdict.verdict == "fallback" and verdict.reason == UNPARSED_VERDICT_REASON


@pytest.mark.parametrize("missing", sorted(_VALID_JUDGE))
def test_judge_requires_every_field(missing):
    raw = json.dumps({k: v for k, v in _VALID_JUDGE.items() if k != missing})
    assert parse_harness_verdict(raw).reason == UNPARSED_VERDICT_REASON


def test_judge_accepts_a_valid_verdict_inside_prose():
    verdict = parse_harness_verdict(f"Verdict: {json.dumps(_VALID_JUDGE)}")
    assert verdict.verdict == "pass" and verdict.confidence == 0.9


# ── Translator ────────────────────────────────────────────────────────────────

def test_translator_null_timeframe_and_defaults():
    action = parse_translator_response('{"intent": "show_todos", "timeframe": "null", "confidence": 0.9}')
    assert (action.intent, action.timeframe, action.pm_prompt, action.is_write) == ("show_todos", None, "", False)


def test_translator_rejects_invented_intents():
    action = parse_translator_response('{"intent": "book_flight", "confidence": 0.99}')
    assert action.intent == "direct" and action.confidence == 0.0


# ── Stage fallbacks after the repair attempt ──────────────────────────────────

def _config(tmp_path):
    return orch.OrchestratorConfig(session_id="pm-contracts", data_dir=str(tmp_path), provider="test",
                                   model="test", pm_provider="test", pm_model="test")


def test_invalid_translator_reply_is_repaired_once(tmp_path, monkeypatch):
    replies = iter(['{"intent": "add_todo"}', json.dumps(dict(
        intent="add_todo", timeframe=None, entities=[], confidence=0.95,
        pm_prompt="add task call mom", is_write=True))])
    sent = []
    monkeypatch.setattr(orch, "_llm", lambda *_a, **_k: object())
    monkeypatch.setattr(orch, "_invoke", lambda _llm, system, user: sent.append(user) or next(replies))

    action = orch._translate("add task call mom", "", _config(tmp_path))

    assert action.intent == "add_todo" and action.pm_prompt == "add task call mom"
    assert json.loads(sent[0]) == {"message": "add task call mom", "context": ""}
    assert "confidence" in sent[1]


def test_unusable_translator_reply_falls_back_and_is_recorded(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(orch, "_llm", lambda *_a, **_k: object())
    monkeypatch.setattr(orch, "_invoke", lambda *_a: "I think they want a task?")
    monkeypatch.setattr(orch, "emit", lambda event, **fields: events.append((event, fields)))

    action = orch._translate("add task call mom", "", _config(tmp_path))

    assert action.intent == "direct" and action.confidence == 0.0
    assert ("contract_error", {"source": "translate"}) in events


def test_unusable_router_reply_answers_directly(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "_llm", lambda *_a, **_k: object())
    monkeypatch.setattr(orch, "_invoke", lambda *_a: "yes probably")
    assert orch._route("hmm what do you think", "", _config(tmp_path)) is False


# ── Prompts and schemas cannot drift apart ────────────────────────────────────

def _example_keys(prompt: str) -> set[str]:
    block = re.search(r"\{\{?\s*\n(.*?)\n\}\}?", prompt, re.S).group(1)
    return set(re.findall(r'^\s*"([a-z_]+)":', block, re.M))


@pytest.mark.parametrize("prompt, contract", [
    (prompts.ROUTER_SYSTEM, RouterOutput),
    (prompts.TRANSLATOR_SYSTEM, TranslatorOutput),
    (prompts.HARNESS_SYSTEM, JudgeOutput),
])
def test_prompt_json_example_matches_its_contract(prompt, contract):
    assert _example_keys(prompt) == set(contract.model_fields)


def test_translator_prompt_lists_exactly_the_allowed_intents():
    listed = re.search(r'"intent": "([^"]+)"', prompts.TRANSLATOR_SYSTEM).group(1)
    assert [i.strip() for i in listed.split("|")] == list(TRANSLATOR_INTENTS)
