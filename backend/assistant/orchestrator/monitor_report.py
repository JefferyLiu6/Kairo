"""Summarize Kairo JSON telemetry locally without Azure access or conversation content."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .judge_eval import percentile


def summarize_logs(lines) -> dict:
    stages: dict[str, list[float]] = {}
    grading_errors = 0
    turn_ids, fallback_ids, llm_ids = set(), set(), set()
    usage = {}
    input_known = output_known = input_total = output_total = 0
    ignored = malformed = duplicates = 0
    seen = set()
    for line in lines:
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            malformed += 1
            continue
        if not isinstance(row, dict) or row.get("service") != "kairo":
            ignored += 1
            continue
        if row.get("schema_version") != 1 or not all(
            isinstance(row.get(k), str) and row[k] for k in ("event", "trace_id", "span_id")
        ):
            malformed += 1
            continue
        # Duplicate ingestion of identical records must not double-count metrics.
        key = json.dumps(row, sort_keys=True)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        event = row["event"]
        if event == "span":
            value = row.get("duration_ms")
            stage = row.get("stage")
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or not isinstance(stage, str):
                malformed += 1
                continue
            stages.setdefault(stage, []).append(value)
            if stage == "turn":
                turn_ids.add(row["trace_id"])
            if stage == "llm_invoke":
                llm_ids.add((row["trace_id"], row["span_id"]))
        elif event == "turn_result" and row.get("outcome") == "fallback":
            fallback_ids.add(row["trace_id"])
        elif event == "judge_result" and row.get("source") in {"invalid", "provider_error"}:
            grading_errors += 1
        elif event == "llm_usage":
            usage[(row["trace_id"], row["span_id"])] = row
    for key in llm_ids & usage.keys():
        inp, out = usage[key].get("input_tokens"), usage[key].get("output_tokens")
        if type(inp) is int and inp >= 0:
            input_known += 1
            input_total += inp
        if type(out) is int and out >= 0:
            output_known += 1
            output_total += out
    turns, fallbacks, llm_calls = len(turn_ids), len(turn_ids & fallback_ids), len(llm_ids)
    return {
        "observed_turn_spans": turns,
        "fallback_events": len(fallback_ids),
        "unmatched_fallback_traces": len(fallback_ids - turn_ids),
        "fallback_rate": fallbacks / turns if turns and fallbacks <= turns else None,
        "judge_invalid_or_provider_errors": grading_errors,
        "observed_llm_call_spans": llm_calls,
        "usage_events": len(usage),
        "unmatched_usage_events": len(usage.keys() - llm_ids),
        "input_usage_coverage": input_known / llm_calls if llm_calls and input_known <= llm_calls else None,
        "output_usage_coverage": output_known / llm_calls if llm_calls and output_known <= llm_calls else None,
        "reported_input_tokens": input_total if input_known else None,
        "reported_output_tokens": output_total if output_known else None,
        "stages": {name: {"samples": len(values), "p50_ms": percentile(values, .5),
                          "p95_ms": percentile(values, .95)} for name, values in sorted(stages.items())},
        "ignored_records": ignored, "malformed_records": malformed, "duplicate_records": duplicates,
        "scope": "Best-effort log observations, not a complete request census. Missing spans/usage are not zero cost or health evidence.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.log.open() as source:
        report = summarize_logs(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
