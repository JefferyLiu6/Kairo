from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from assistant.orchestrator import telemetry
from assistant.orchestrator.harness import fast_precheck, parse_harness_verdict
from assistant.orchestrator.judge_eval import DEFAULT_CASES, run, summarize
from assistant.orchestrator.quality_judge import evaluate_response, parse_quality_verdict
from assistant.orchestrator.translator import StructuredAction


def valid(**updates):
    data = dict(groundedness=2, relevance=2, completeness=2, abstain=False,
                reason="Supported by event data", evidence_quote="Thursday")
    return json.dumps(data | updates)


@pytest.mark.parametrize("updates", [
    {"groundedness": 3}, {"relevance": True}, {"completeness": "2"},
    {"abstain": "false"}, {"extra": 1}, {"reason": ""},
    {"evidence_quote": "invented citation"},
])
def test_invalid_scores_and_ungrounded_quotes_are_not_passes(updates):
    result = evaluate_response(lambda *_: valid(**updates), "When?", "Thursday", "Thursday")
    assert result["status"] == "invalid"
    assert result["label"] == "abstain"
    assert result["scores"] is None


def test_low_groundedness_cannot_be_hidden_in_average():
    assert parse_quality_verdict(valid(groundedness=0), "Thursday", "").label == "fail"


def test_provider_failure_is_separate_from_bad_answer():
    def fail(*_):
        raise TimeoutError("secret request text")
    result = evaluate_response(fail, "When?", "Thursday", "Thursday")
    assert result == {"status": "provider_error", "label": "abstain", "scores": None}


def test_replay_contract_suite():
    report = run(json.loads(Path(DEFAULT_CASES).read_text()))
    assert report["summary"]["cases"] == 15
    assert report["summary"]["agreement_all_cases"] == 1
    assert report["summary"]["abstentions"] == 1


def test_denominators_do_not_hide_missing_grades():
    rows = [dict(expected="fail", label="pass", status="ok", duration_ms=5),
            dict(expected="pass", label="abstain", status="provider_error", duration_ms=10)]
    result = summarize(rows)
    assert result["coverage"] == .5
    assert result["false_pass_rate"] == 1
    assert result["agreement_all_cases"] == 0
    assert result["latency_p95_ms"] == 10
    assert summarize([])["coverage"] is None


def test_repeated_grades_report_instability():
    case = json.loads(Path(DEFAULT_CASES).read_text())[0]
    replies = iter([valid(), valid(groundedness=0)])
    report = run([case], lambda *_: next(replies), repeats=2)
    assert report["repeat_disagreement_cases"] == 1


@pytest.mark.parametrize("raw", ["{}", "[]", "null", "broken", '{"verdict":"PASS"}',
    '{"verdict":"pass","confidence":NaN,"reason":"x","suggested_fix":"","failure_type":"null"}',
    '{"verdict":"pass","confidence":2,"reason":"x","suggested_fix":"","failure_type":"null"}'])
def test_online_judge_invalid_output_falls_back(raw):
    assert parse_harness_verdict(raw).verdict == "fallback"


def test_truthful_empty_result_is_not_automatically_retried():
    action = StructuredAction("show_schedule", None, [], .9, "show schedule", False)
    assert fast_precheck(action, "Nothing on the schedule") is None
    assert fast_precheck(action, "Todo list: (empty)").verdict == "retry"


def test_write_error_never_requests_retry():
    action = StructuredAction("add_event", None, [], .9, "add event", True)
    assert fast_precheck(action, "Error: timeout").verdict == "fallback"


def test_spans_correlate_and_never_include_exception_text(caplog):
    with caplog.at_level("INFO", logger="kairo.telemetry"):
        with pytest.raises(ValueError), telemetry.span("turn"):
            with telemetry.span("judge"):
                telemetry.emit("judge_result", verdict="fallback", secret="private calendar")
                raise ValueError("private calendar")
    rows = [json.loads(r.message) for r in caplog.records]
    assert len({r["trace_id"] for r in rows}) == 1
    assert rows[1]["parent_span_id"] == rows[2]["span_id"]
    assert rows[-1]["outcome"] == "error"
    assert "private calendar" not in caplog.text
    assert telemetry._context.get() is None


def test_concurrent_turns_have_distinct_trace_ids(caplog):
    async def turn():
        with telemetry.span("turn"):
            await asyncio.sleep(0)
            telemetry.emit("turn_result", outcome="completed")

    async def both():
        await asyncio.gather(turn(), turn())

    with caplog.at_level("INFO", logger="kairo.telemetry"):
        asyncio.run(both())
    rows = [json.loads(r.message) for r in caplog.records]
    assert len({r["trace_id"] for r in rows}) == 2


def test_telemetry_can_be_disabled(caplog, monkeypatch):
    monkeypatch.setenv("KAIRO_TELEMETRY", "0")
    with caplog.at_level("INFO", logger="kairo.telemetry"), telemetry.span("turn"):
        telemetry.emit("turn_result", outcome="completed")
    assert not caplog.records
