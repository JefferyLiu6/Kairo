"""
One run of the blind eval against one path, and its report.

Each scenario gets a fresh data directory and user. The clock is pinned to the
set's `now` for the whole run, so "tomorrow" and "next Tuesday" mean the same
dates every time and expectations can be written as plain dates.
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path as FilePath
from tempfile import TemporaryDirectory
from typing import Any, Callable, Iterator, Optional, Sequence

import time_machine

from evals.blind.adapters import Path
from evals.blind.scoring import GATE, Outcome, gate, score_turn, summarize
from evals.blind.spec import BlindSet, Scenario, fingerprint
from evals.paths import API_ROOT

ARTIFACTS = API_ROOT.parents[1] / "artifacts" / "blind"
_GUARDRAIL_OUTCOMES = (Outcome.WRONG_SAVE, Outcome.TRAPPED, Outcome.FALSE_CLAIM)


@contextmanager
def _pinned(blind: BlindSet) -> Iterator[None]:
    """Pin the clock and timezone to the set's, never trace eval turns, and put everything back after."""
    settings = {"TZ": blind.timezone, "KAIRO_TIMEZONE": blind.timezone, "LANGSMITH_TRACING": "false"}
    saved = {name: os.environ.get(name) for name in (*settings, "LANGCHAIN_TRACING_V2")}
    os.environ.update(settings)
    os.environ.pop("LANGCHAIN_TRACING_V2", None)
    time.tzset()
    try:
        with time_machine.travel(blind.now, tick=True):
            yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        time.tzset()


def _run_scenario(scenario: Scenario, path: Path, on_turn: Optional[Callable]) -> dict[str, Any]:
    records = []
    with TemporaryDirectory(prefix=f"blind-{scenario.id}-") as data_dir:
        session = path.open(scenario.id, data_dir)
        before = session.snapshot()
        for index, turn in enumerate(scenario.turns, 1):
            started = time.perf_counter()
            error = None
            try:
                sent = session.send(turn.say)
                reply, usage = sent.reply, (sent.model_calls, sent.input_tokens, sent.output_tokens)
            except Exception as exc:  # a crash is a result to record, not a reason to lose the run
                error, reply, usage = f"{type(exc).__name__}: {exc}"[:200], "", (0, 0, 0)
            seconds = round(time.perf_counter() - started, 3)
            after = session.snapshot()
            score = score_turn(turn, before, after, reply, entities=scenario.entities, never=scenario.never)
            outcome, reason = score.outcome, score.reason
            if error and outcome not in _GUARDRAIL_OUTCOMES:
                outcome, reason = Outcome.MISS, f"the path raised {error}"
            record = {
                "turn": index, "say": turn.say, "reply": reply, "outcome": outcome.value, "reason": reason,
                "seconds": seconds, "model_calls": usage[0], "input_tokens": usage[1], "output_tokens": usage[2],
            }
            if error:
                record["error"] = error
            records.append(record)
            if on_turn:
                on_turn(scenario, record)
            before = after
    return {"id": scenario.id, "persona": scenario.persona, "split": scenario.split, "turns": records}


def _metrics(records: Sequence[dict[str, Any]], usd_per_mtok: Optional[tuple[float, float]]) -> dict[str, Any]:
    metrics = summarize(Outcome(record["outcome"]) for record in records)
    seconds = sorted(record["seconds"] for record in records)
    input_tokens = sum(record["input_tokens"] for record in records)
    output_tokens = sum(record["output_tokens"] for record in records)
    metrics.update({
        "latency_median_s": round(statistics.median(seconds), 3) if seconds else None,
        "latency_p95_s": seconds[max(0, -(-len(seconds) * 95 // 100) - 1)] if seconds else None,
        "model_calls_per_turn": round(sum(r["model_calls"] for r in records) / len(records), 2) if records else None,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": None if usd_per_mtok is None else round(
            input_tokens * usd_per_mtok[0] / 1e6 + output_tokens * usd_per_mtok[1] / 1e6, 6),
    })
    return metrics


def _code_version() -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=API_ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
    try:
        return {"commit": git("rev-parse", "--short", "HEAD"), "dirty": bool(git("status", "--porcelain"))}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}


def run_blind_eval(
    blind: BlindSet,
    path: Path,
    *,
    only: Optional[Sequence[str]] = None,
    on_turn: Optional[Callable[[Scenario, dict[str, Any]], None]] = None,
    usd_per_mtok: Optional[tuple[float, float]] = None,
) -> dict[str, Any]:
    """Run every scenario (or just `only`) through `path` and return the report."""
    unknown = set(only or ()) - {scenario.id for scenario in blind.scenarios}
    if unknown:
        raise ValueError(f"unknown scenario(s): {', '.join(sorted(unknown))}")
    chosen = [scenario for scenario in blind.scenarios if not only or scenario.id in only]

    started_at, started = datetime.now(timezone.utc).isoformat(), time.perf_counter()
    with _pinned(blind):
        scenarios = [_run_scenario(scenario, path, on_turn) for scenario in chosen]
    records = [record for scenario in scenarios for record in scenario["turns"]]

    metrics = {"overall": _metrics(records, usd_per_mtok)}
    for split in ("dev", "holdout"):
        metrics[split] = _metrics([r for s in scenarios if s["split"] == split for r in s["turns"]], usd_per_mtok)
    return {
        "schema": 1,
        "path": {"name": path.name, **path.describe()},
        "fixture": {"sha256": fingerprint(blind), "now": blind.now.isoformat(), "turns": len(records)},
        "code": _code_version(),
        "started_at": started_at,
        "duration_s": round(time.perf_counter() - started, 1),
        "metrics": metrics,
        "bars": GATE,
        "gate": gate(metrics["overall"]),
        "scenarios": scenarios,
    }


def save_report(report: dict[str, Any], target: str | FilePath) -> FilePath:
    """Write the report as JSON. An existing file is evidence and is never replaced."""
    target = FilePath(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "x", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)
    return target


def default_report_path(report: dict[str, Any]) -> FilePath:
    stamp = report["started_at"][:19].replace("-", "").replace(":", "")
    return ARTIFACTS / f"{report['path']['name']}-{stamp}Z.json"


def _percent(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def render_summary(report: dict[str, Any]) -> str:
    """The report as text: results against the bar, then every turn that was not right."""
    overall, path = report["metrics"]["overall"], report["path"]
    lines = [
        f"Blind eval · path: {path['name']} · model: {path.get('model') or 'none'} · "
        f"{overall['turns']} turns · set {report['fixture']['sha256']} · clock {report['fixture']['now']}",
        "",
        f"  {'':28}{'result':>10}{'bar':>10}",
        f"  {'Right (of scoreable turns)':28}{_percent(overall['right_rate']):>10}"
        f"{'>= ' + _percent(GATE['right_rate_min']):>10}   {overall['right']} of {overall['scoreable']}",
        f"  {'Wrong but reported done':28}{_percent(overall['wrong_but_reported_done_rate']):>10}"
        f"{'<= ' + _percent(GATE['wrong_but_reported_done_rate_max']):>10}   "
        f"{overall['wrong_save']} wrong saves, {overall['false_claim']} false claims, of {overall['turns']}",
        f"  {'Trapped turns':28}{overall['trapped']:>10}{'<= ' + str(GATE['trapped_max']):>10}",
        f"  {'Missed':28}{overall['miss']:>10}",
        f"  {'Blocked by an earlier turn':28}{overall['blocked']:>10}",
        f"  {'Open-ended, not scored':28}{overall['unscored']:>10}",
        "",
        f"  Dev {_percent(report['metrics']['dev']['right_rate'])} right · "
        f"holdout {_percent(report['metrics']['holdout']['right_rate'])} right",
        f"  Reply time: median {overall['latency_median_s']}s, 95th percentile {overall['latency_p95_s']}s · "
        f"model calls per turn: {overall['model_calls_per_turn']} · "
        f"tokens: {overall['input_tokens']} in, {overall['output_tokens']} out"
        + (f" · cost ${overall['cost_usd']}" if overall["cost_usd"] is not None else ""),
        "",
        "  Gate: " + ("PASS" if report["gate"]["passed"] else "FAIL — " + "; ".join(report["gate"]["failed"])),
    ]
    for scenario in report["scenarios"]:
        rest = [record for record in scenario["turns"] if record["outcome"] != Outcome.RIGHT.value]
        tally = f"{len(scenario['turns']) - len(rest)}/{len(scenario['turns'])} right"
        lines += ["", f"{scenario['id']} ({scenario['split']}) — {tally}"]
        for record in rest:
            lines.append(f"  {record['turn']:>2}. {record['outcome']:<11} {record['say']}")
            if record["reason"]:
                lines.append(f"      {record['reason']}")
    return "\n".join(lines)
