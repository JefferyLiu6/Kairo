"""Paired comparison of judge reports. Never compare replay with live model results."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .judge_eval import summarize

METRICS = (
    "coverage", "agreement_all_cases", "agreement_scored", "false_pass_rate",
    "pass_precision", "invalid", "provider_errors", "latency_p50_ms", "latency_p95_ms",
)


def _index(rows):
    result = {}
    for row in rows:
        key = (row["id"], row["repeat"])
        if key in result:
            raise ValueError("Duplicate case/repeat in report")
        if row["status"] not in {"ok", "invalid", "provider_error"}:
            raise ValueError("Unknown result status")
        if row["label"] not in {"pass", "fail", "abstain"}:
            raise ValueError("Unknown result label")
        if row["status"] != "ok" and row["label"] != "abstain":
            raise ValueError("Failed grading must abstain")
        result[key] = row
    if not result:
        raise ValueError("Cannot compare empty reports")
    return result


def compare(baseline: dict, candidate: dict) -> dict:
    a_meta, b_meta = baseline["metadata"], candidate["metadata"]
    for key in ("mode", "dataset_sha256", "repeats"):
        if a_meta.get(key) is None or a_meta.get(key) != b_meta.get(key):
            raise ValueError(f"Reports must share {key}")
    if a_meta["mode"] not in {"live", "replay_contract_only"}:
        raise ValueError("Unsupported report mode")
    a, b = _index(baseline["rows"]), _index(candidate["rows"])
    if a.keys() != b.keys():
        raise ValueError("Reports must contain identical case/repeat pairs")
    changes = []
    for key in sorted(a):
        old, new = a[key], b[key]
        if old["expected"] != new["expected"] or old["category"] != new["category"]:
            raise ValueError("Paired rows must have identical labels/categories")
        if (old["status"], old["label"]) != (new["status"], new["label"]):
            changes.append({
                "id": key[0], "repeat": key[1], "expected": old["expected"],
                "baseline": {"status": old["status"], "label": old["label"]},
                "candidate": {"status": new["status"], "label": new["label"]},
            })
    # Recompute from rows instead of trusting cached summary fields.
    a_summary, b_summary = summarize(list(a.values())), summarize(list(b.values()))
    return {
        "mode": a_meta["mode"], "paired_observations": len(a),
        "unique_cases": len({key[0] for key in a}),
        "baseline": a_meta, "candidate": b_meta,
        "metric_deltas_candidate_minus_baseline": {
            key: b_summary[key] - a_summary[key]
            if b_summary[key] is not None and a_summary[key] is not None else None
            for key in METRICS
        },
        "baseline_summary": a_summary, "candidate_summary": b_summary,
        "changed_grades": changes,
        "interpretation": "Descriptive paired comparison; repeats are not independent cases. No statistical significance or automatic winner is claimed.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = compare(json.loads(args.baseline.read_text()), json.loads(args.candidate.read_text()))
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["metric_deltas_candidate_minus_baseline"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
