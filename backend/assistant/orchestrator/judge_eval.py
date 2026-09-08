"""Run a versioned synthetic response-quality benchmark, with explicit live opt-in."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime, timezone

from .quality_judge import RUBRIC, RUBRIC_VERSION, evaluate_response

DEFAULT_CASES = Path(__file__).parents[2] / "tests/fixtures/judge_cases.json"


def percentile(values: list[float], fraction: float) -> float | None:
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def summarize(rows: list[dict]) -> dict:
    scored = [r for r in rows if r["status"] == "ok" and r["label"] != "abstain"]
    negatives = [r for r in rows if r["expected"] == "fail"]
    predicted_pass = [r for r in scored if r["label"] == "pass"]
    matrix: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = matrix.setdefault(row["expected"], {"pass": 0, "fail": 0, "abstain": 0})
        bucket[row["label"]] += 1
    return {
        "cases": len(rows), "scored": len(scored),
        "coverage": len(scored) / len(rows) if rows else None,
        "agreement_all_cases": sum(r["status"] == "ok" and r["label"] == r["expected"] for r in rows) / len(rows) if rows else None,
        "agreement_scored": sum(r["label"] == r["expected"] for r in scored) / len(scored) if scored else None,
        "false_pass_rate": sum(r["label"] == "pass" for r in negatives) / len(negatives) if negatives else None,
        "pass_precision": sum(r["expected"] == "pass" for r in predicted_pass) / len(predicted_pass) if predicted_pass else None,
        "invalid": sum(r["status"] == "invalid" for r in rows),
        "provider_errors": sum(r["status"] == "provider_error" for r in rows),
        "abstentions": sum(r["label"] == "abstain" for r in rows),
        "latency_p50_ms": percentile([r["duration_ms"] for r in rows], .5),
        "latency_p95_ms": percentile([r["duration_ms"] for r in rows], .95),
        "confusion_matrix": matrix,
    }


def run(cases: list[dict], invoke=None, repeats: int = 1) -> dict:
    rows = []
    for case in cases:
        for repeat in range(repeats):
            started = time.perf_counter()
            caller = invoke if invoke is not None else lambda _s, _u, c=case: json.dumps(c["replay_verdict"])
            result = evaluate_response(caller, case["request"], case["response"], case["evidence"])
            rows.append({
                "id": case["id"], "category": case["category"], "repeat": repeat,
                "expected": case["expected"], **result,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            })
    inconsistent = sum(
        len({r["label"] for r in rows if r["id"] == case["id"]}) > 1 for case in cases
    )
    return {
        "summary": summarize(rows),
        "by_category": {c: summarize([r for r in rows if r["category"] == c]) for c in sorted({r["category"] for r in rows})},
        "repeat_disagreement_cases": inconsistent if repeats > 1 else None,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="Makes billable model calls on synthetic fixtures")
    parser.add_argument("--provider", choices=["openai", "anthropic", "ollama"], default="openai")
    parser.add_argument("--model", help="Required for live; pin an available model version")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-calls", type=int, default=30)
    args = parser.parse_args()
    cases_bytes = args.cases.read_bytes()
    cases = json.loads(cases_bytes)
    if not cases or len({c["id"] for c in cases}) != len(cases):
        parser.error("Dataset must be nonempty with unique IDs")
    if args.repeats < 1 or args.max_calls < 1 or len(cases) * args.repeats > args.max_calls:
        parser.error("Requested evaluations exceed --max-calls, or limits are nonpositive")
    invoke = None
    usage: list[dict] = []
    if args.live:
        if not args.model:
            parser.error("--live requires --model")
        from assistant.shared.llm_env import build_llm
        from langchain_core.messages import HumanMessage, SystemMessage
        llm = build_llm(args.provider, args.model, os.getenv("JUDGE_API_KEY"), os.getenv("JUDGE_BASE_URL"))
        # This ceiling counts logical evaluations; disable SDK retries where supported.
        if hasattr(llm, "max_retries"):
            llm.max_retries = 0

        def invoke(system, payload):
            response = llm.invoke([SystemMessage(content=system), HumanMessage(content=payload)])
            usage.append(getattr(response, "usage_metadata", None) or {})
            return str(response.content)

    report = run(cases, invoke, args.repeats)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip())
    report["metadata"] = {
        "mode": "live" if args.live else "replay_contract_only",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rubric_version": RUBRIC_VERSION,
        "rubric_sha256": hashlib.sha256(RUBRIC.encode()).hexdigest(),
        "dataset_sha256": hashlib.sha256(cases_bytes).hexdigest(),
        "git_revision": revision, "working_tree_dirty": dirty,
        "provider": args.provider if args.live else None, "model": args.model if args.live else None,
        "label_source": "author-proposed synthetic labels; not independently human-calibrated",
        "repeats": args.repeats,
        "scope": "Response-quality grading of fixed fixtures, not end-to-end agent task success",
        "token_usage": usage if args.live else None,
        "cost": None,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"mode": report["metadata"]["mode"], **report["summary"]}, indent=2))
    # Missing grades always fail. Replay additionally checks expected fixture labels.
    if report["summary"]["invalid"] or report["summary"]["provider_errors"]:
        return 1
    if not args.live and report["summary"]["agreement_all_cases"] != 1:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
