"""
The scores ledger: one line per recorded blind-eval run.

A prompt's version says that it changed, not whether it got better. The ledger
ties each run's scores to the prompt versions, model and eval set that produced
them, so a change can be compared with what came before it. It is committed
with the code (apps/api/evals/scores.jsonl) and only ever appended to.

    python -m evals.blind --path orchestrator --record "why this run was made"
    python -m evals.blind --history

tests/evals/test_blind_ledger.py fails when the current prompts have no
recorded web-chat run, so a prompt edit is not finished until it is scored.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

from evals.paths import API_ROOT

LEDGER = API_ROOT / "evals" / "scores.jsonl"
_METRICS = ("right_rate", "wrong_but_reported_done_rate", "trapped", "model_calls_per_turn", "latency_median_s",
            "latency_p95_s", "input_tokens", "output_tokens", "cost_usd")


def prompt_set(versions: Mapping[str, str]) -> Optional[str]:
    """One short name for a whole set of prompt versions; None for a path that sends no prompt."""
    if not versions:
        return None
    joined = "\n".join(f"{name}@{version}" for name, version in sorted(versions.items()))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def entry_from_report(report: Mapping[str, Any], note: str, *, report_file: Optional[str] = None) -> dict[str, Any]:
    """The ledger line for a finished run. `note` says why the run was made."""
    if not note.strip():
        raise ValueError("a ledger entry needs a note saying why the run was made")
    path, overall, code = report["path"], report["metrics"]["overall"], report["code"]
    commit = code.get("commit")
    return {
        "recorded_at": report["started_at"][:19] + "+00:00",
        "note": note.strip(),
        "path": path["name"],
        "model": path.get("model"),
        "pm_model": path.get("pm_model"),
        "prompt_set": path.get("prompt_set"),
        "prompts": path.get("prompts") or {},
        "bundle": path.get("bundle"),   # prompt plus tool definitions, for a path that calls tools
        "eval_set": report["fixture"]["sha256"],
        "turns": report["fixture"]["turns"],
        "code": f"{commit}+uncommitted" if commit and code.get("dirty") else commit,
        **{name: overall.get(name) for name in _METRICS},
        "dev_right_rate": report["metrics"]["dev"]["right_rate"],
        "holdout_right_rate": report["metrics"]["holdout"]["right_rate"],
        "gate_passed": report["gate"]["passed"],
        "report": report_file,
    }


def append(entry: Mapping[str, Any], ledger: Union[str, Path] = LEDGER) -> None:
    ledger = Path(ledger)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load(ledger: Union[str, Path] = LEDGER) -> list[dict[str, Any]]:
    try:
        lines = Path(ledger).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def scored(prompts: Optional[str], *, path: str = "orchestrator", ledger: Union[str, Path] = LEDGER) -> bool:
    """Whether a run on `path` is recorded for this prompt set."""
    return any(entry["path"] == path and entry["prompt_set"] == prompts for entry in load(ledger))


def _percent(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def render_history(entries: Sequence[Mapping[str, Any]]) -> str:
    """The recorded runs as a table, oldest first, so a change can be read against what came before."""
    if not entries:
        return "No runs recorded yet."
    lines = [f"{'recorded':<17}{'path':<14}{'model':<14}{'prompts':<14}{'right':>6}{'wrong':>7}{'trapped':>8}"
             f"{'calls':>7}{'median':>8}  note"]
    for entry in entries:
        median = "n/a" if entry.get("latency_median_s") is None else f"{entry['latency_median_s']:.1f}s"
        lines.append(
            f"{entry['recorded_at'][:16].replace('T', ' '):<17}{entry['path']:<14}{str(entry.get('model') or '-'):<14}"
            f"{str(entry.get('prompt_set') or '-'):<14}{_percent(entry.get('right_rate')):>6}"
            f"{_percent(entry.get('wrong_but_reported_done_rate')):>7}{entry.get('trapped', 0):>8}"
            f"{entry.get('model_calls_per_turn') or 0:>7}{median:>8}  {entry['note']}"
        )
    return "\n".join(lines)
