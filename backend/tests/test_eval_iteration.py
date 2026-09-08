from __future__ import annotations

import copy
import json

import pytest

from assistant.orchestrator.capture_eval import capture
from assistant.orchestrator.judge_compare import compare
from assistant.orchestrator.judge_eval import run, summarize, validate_cases
from assistant.orchestrator.monitor_report import summarize_logs
from assistant.personal_manager.extractors.entities import extract_pm_entities
from assistant.personal_manager.domain.types import PMIntent


def report():
    return {
        "metadata": {"mode": "live", "dataset_sha256": "abc", "repeats": 1},
        "rows": [{"id": "a", "repeat": 0, "category": "groundedness", "expected": "fail",
                  "status": "ok", "label": "fail", "duration_ms": 10}],
    }


@pytest.mark.parametrize("field,value", [("mode", "replay_contract_only"),
                                         ("dataset_sha256", "different"), ("repeats", 2)])
def test_compare_rejects_incompatible_runs(field, value):
    candidate = report()
    candidate["metadata"][field] = value
    with pytest.raises(ValueError):
        compare(report(), candidate)


def test_compare_recomputes_metrics_and_lists_regression():
    candidate = report()
    candidate["summary"] = {"false_pass_rate": 0}
    candidate["rows"][0]["label"] = "pass"
    result = compare(report(), candidate)
    assert result["metric_deltas_candidate_minus_baseline"]["false_pass_rate"] == 1
    assert result["changed_grades"][0]["id"] == "a"


@pytest.mark.parametrize("change", ["duplicate", "missing", "label"])
def test_compare_rejects_unpaired_or_relabelled_rows(change):
    candidate = report()
    if change == "duplicate":
        candidate["rows"].append(copy.deepcopy(candidate["rows"][0]))
    elif change == "missing":
        candidate["rows"] = []
    else:
        candidate["rows"][0]["expected"] = "pass"
    with pytest.raises(ValueError):
        compare(report(), candidate)


@pytest.mark.parametrize("cases", [[], {}, [None], [{"id": "x"}]])
def test_dataset_validation_rejects_invalid_inputs(cases):
    with pytest.raises(ValueError):
        validate_cases(cases, replay=False)


def test_unreviewed_rows_do_not_generate_accuracy_claims():
    row = report()["rows"][0] | {"expected": None}
    result = summarize([row])
    assert result["labeled_cases"] == 0
    assert result["agreement_all_cases"] is None
    assert result["agreement_scored"] is None
    assert result["false_pass_rate"] is None
    assert result["pass_precision"] is None


def test_capture_reads_actual_state_and_preserves_unreviewed_labels():
    cases = capture()
    assert len(cases) == 3
    assert all(case["expected"] is None for case in cases)
    created = json.loads(cases[0]["evidence"])
    listed = json.loads(cases[1]["evidence"])
    completed = json.loads(cases[2]["evidence"])
    assert created["before"] == []
    assert created["after"][0]["done"] is False
    assert listed["before"] == listed["after"]
    assert completed["before"][0]["done"] is False
    assert completed["after"][0]["done"] is True
    with pytest.raises(ValueError, match="Replay requires"):
        run(cases)


@pytest.mark.parametrize("title", ["prepare interview examples", "write as done documentation", "Sarah's review"])
def test_mark_quoted_task_preserves_exact_title(title):
    entities = extract_pm_entities(f'Mark the "{title}" todo as done', PMIntent.COMPLETE_TODO)
    assert entities["query"] == title


def log(event, **fields):
    return json.dumps(dict(service="kairo", schema_version=1, event=event,
                           trace_id="t", span_id=event) | fields)


def test_monitor_missing_usage_is_not_zero_cost():
    result = summarize_logs([log("span", stage="llm_invoke", duration_ms=10)])
    assert result["reported_input_tokens"] is None
    assert result["input_usage_coverage"] == 0


def test_monitor_percentiles_duplicates_and_malformed_lines():
    lines = [log("span", stage="turn", duration_ms=100),
             log("span", stage="turn", duration_ms=100),
             log("turn_result", outcome="fallback"), "not json", "[]"]
    result = summarize_logs(lines)
    assert result["observed_turn_spans"] == 1
    assert result["fallback_rate"] == 1
    assert result["stages"]["turn"]["p95_ms"] == 100
    assert result["duplicate_records"] == 1
    assert result["malformed_records"] == 1
    assert result["ignored_records"] == 1


def test_monitor_rejects_nonfinite_and_negative_duration():
    result = summarize_logs([log("span", stage="turn", duration_ms=float("nan")),
                             log("span", stage="turn", duration_ms=-1)])
    assert result["observed_turn_spans"] == 0
    assert result["fallback_rate"] is None
    assert result["malformed_records"] == 2


def test_monitor_excludes_unmatched_events_from_rates():
    result = summarize_logs([
        log("span", stage="turn", duration_ms=10),
        log("turn_result", trace_id="missing-turn", outcome="fallback"),
        log("llm_usage", input_tokens=100, output_tokens=20),
    ])
    assert result["fallback_rate"] == 0
    assert result["unmatched_fallback_traces"] == 1
    assert result["unmatched_usage_events"] == 1
    assert result["reported_input_tokens"] is None


def test_monitor_matches_usage_to_its_llm_span():
    result = summarize_logs([
        log("span", span_id="call", stage="llm_invoke", duration_ms=10),
        log("llm_usage", span_id="call", input_tokens=20, output_tokens=5),
    ])
    assert result["reported_input_tokens"] == 20
    assert result["input_usage_coverage"] == 1
