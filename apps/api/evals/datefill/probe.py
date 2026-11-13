"""
Cases, judging and a run of the model date test.

A trial is one message answered once in one arm. Its outcome is one of:

    right      the calls match an acceptable answer
    safe_miss  nothing wrong would be stored: the model asked when it could have
               answered, made no call, or made a call the contract rejects
    wrong      the model filled in something that is not what was said; stored
               as-is this would be a wrong save
    error      the request failed; not a verdict on the model, left out of rates

The bars were fixed before the first run (see BARS): the product allows at most
2% wrong-but-reported-done, and a wrong date from the model is exactly that,
because nothing downstream can know the user meant another day.
"""
from __future__ import annotations

import json
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.blind.scoring import assign, matches, mismatched_fields
from evals.datefill.tools import ARMS, openai_tools, single_call, system_prompt, validate_call
from evals.paths import API_ROOT, FIXTURES

DEFAULT_CASES = FIXTURES / "date_fill_cases.json"
ARTIFACTS = API_ROOT.parents[1] / "artifacts" / "datefill"

BARS = {"go": {"wrong_rate_max": 0.02, "right_rate_min": 0.90}, "mitigate": {"wrong_rate_max": 0.05}}

Category = Literal["relative_day", "weekday", "absolute_date", "time_of_day", "duration", "digit_in_title",
                   "recurrence", "todo_due", "must_ask", "non_english"]
ToolName = Literal["create_event", "add_todo", "ask_user"]
# Which mistake to report when several fields are off: the date first, then time, repeat, title.
_FIELD_KINDS = (("date", "wrong_date"), ("due_date", "wrong_date"), ("start", "wrong_time"), ("end", "wrong_time"),
                ("due_time", "wrong_time"), ("recurrence", "wrong_recurrence"), ("title", "wrong_title"))


# ── Cases ─────────────────────────────────────────────────────────────────────

class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpectedCall(_Model):
    tool: ToolName
    args: dict[str, Any] = Field(default_factory=dict)   # a pattern, as in evals.blind.spec

    def pattern(self) -> dict[str, Any]:
        """The pattern to match, with the default that an event repeats only when the case says so."""
        if self.tool == "create_event" and not any(key.startswith("recurrence") for key in self.args):
            return {**self.args, "recurrence": None}
        return self.args


class Alternative(_Model):
    """One acceptable answer: a single call (`tool`, `args`) or several (`calls`)."""

    tool: Optional[ToolName] = None
    args: dict[str, Any] = Field(default_factory=dict)
    calls: Optional[list[ExpectedCall]] = None

    @model_validator(mode="after")
    def _one_form(self) -> "Alternative":
        if (self.tool is None) == (self.calls is None):
            raise ValueError("an alternative is either one `tool` call or a list of `calls`")
        if self.calls is not None and (self.args or not self.calls):
            raise ValueError("`calls` must be non-empty and replaces `tool` and `args`")
        return self

    def expected(self) -> list[ExpectedCall]:
        return self.calls if self.calls is not None else [ExpectedCall(tool=self.tool, args=self.args)]

    def is_ask(self) -> bool:
        return all(call.tool == "ask_user" for call in self.expected())


class Case(_Model):
    id: str
    say: str = Field(min_length=1)
    category: Category
    source: str
    expect: list[Alternative] = Field(min_length=1)
    note: str = ""


def load_cases(path: Union[str, Path] = DEFAULT_CASES) -> list[Case]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = [Case.model_validate(item) for item in raw["cases"]]
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("case ids must be unique")
    return cases


# ── Judging ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Call:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Verdict:
    outcome: str   # right | safe_miss | wrong
    kind: str      # ok, asked, no_call, invalid, guessed, wrong_date, wrong_time, wrong_recurrence, ...
    detail: str = ""


def _fits(alternative: Alternative, calls: Sequence[Call]) -> bool:
    expected = alternative.expected()
    if len(expected) != len(calls):
        return False
    table = [[call.name == want.tool and matches(call.args, want.pattern()) for call in calls] for want in expected]
    return assign(table) is not None


def _closest_mistake(case: Case, calls: Sequence[Call]) -> Verdict:
    """Compare a filled-in answer with the fill-in alternative it is nearest to."""
    best: Optional[tuple[int, Verdict]] = None
    for alternative in (a for a in case.expect if not a.is_ask()):
        expected = alternative.expected()
        if len(expected) != len(calls):
            found = (99, Verdict("wrong", "wrong_count", f"{len(calls)} call(s), expected {len(expected)}"))
        else:
            # Pair calls with expectations in order of tool name; the sets are tiny.
            pairs = list(zip(sorted(expected, key=lambda e: e.tool), sorted(calls, key=lambda c: c.name)))
            if any(want.tool != call.name for want, call in pairs):
                names = ", ".join(call.name for call in calls)
                found = (50, Verdict("wrong", "wrong_tool", f"called {names}, expected {expected[0].tool}"))
            else:
                problems: dict[str, str] = {}
                for want, call in pairs:
                    problems.update(mismatched_fields(call.args, want.pattern()))
                kind = next((kind for prefix, kind in _FIELD_KINDS
                             if any(path == prefix or path.startswith(prefix + ".") for path in problems)),
                            "wrong_other")
                detail = "; ".join(f"{path} {problem}" for path, problem in problems.items())
                found = (len(problems), Verdict("wrong", kind, detail))
        if best is None or found[0] < best[0]:
            best = found
    return best[1]


def judge(case: Case, calls: Sequence[Call], *, now: Optional[datetime] = None) -> Verdict:
    if not calls:
        return Verdict("safe_miss", "no_call", "the model called no tool")
    for call in calls:
        problem = validate_call(call.name, call.args, now=now)
        if problem:
            return Verdict("safe_miss", "invalid", f"{call.name}: {problem}")
    if any(_fits(alternative, calls) for alternative in case.expect):
        return Verdict("right", "ok")
    if all(call.name == "ask_user" for call in calls):
        return Verdict("safe_miss", "asked", str(calls[0].args.get("question", "")))
    if all(alternative.is_ask() for alternative in case.expect):
        filled = next(call for call in calls if call.name != "ask_user")
        return Verdict("wrong", "guessed", f"{filled.name} {json.dumps(filled.args, ensure_ascii=False)}")
    return _closest_mistake(case, calls)


# ── A run ─────────────────────────────────────────────────────────────────────

Ask = Callable[[str, str, str], list[Call]]   # (arm, system prompt, message) -> the model's tool calls


def summarize(trials: Sequence[dict[str, Any]]) -> dict[str, Any]:
    judged = [trial for trial in trials if trial["outcome"] != "error"]
    count = Counter(trial["outcome"] for trial in judged)
    total = len(judged)

    def rate(outcome: str) -> Optional[float]:
        return round(count[outcome] / total, 4) if total else None

    by_category: dict[str, dict[str, int]] = {}
    for trial in judged:
        row = by_category.setdefault(trial["category"], {"trials": 0, "right": 0, "safe_miss": 0, "wrong": 0})
        row["trials"] += 1
        row[trial["outcome"]] += 1
    outcomes_by_case: dict[str, set[str]] = {}
    for trial in judged:
        outcomes_by_case.setdefault(trial["id"], set()).add(trial["outcome"])
    return {
        "trials": total,
        "errors": len(trials) - total,
        "right_rate": rate("right"),
        "safe_miss_rate": rate("safe_miss"),
        "wrong_rate": rate("wrong"),
        "kinds": dict(Counter(trial["kind"] for trial in judged if trial["outcome"] != "right")),
        "by_category": by_category,
        "unstable": sorted(case_id for case_id, outcomes in outcomes_by_case.items() if len(outcomes) > 1),
        "latency_median_s": round(statistics.median(t["seconds"] for t in judged), 3) if judged else None,
    }


def decide(summary: dict[str, Any]) -> str:
    """go, mitigate or no_go against BARS; no_data if nothing was judged."""
    wrong, right = summary.get("wrong_rate"), summary.get("right_rate")
    if wrong is None or right is None:
        return "no_data"
    if wrong <= BARS["go"]["wrong_rate_max"] and right >= BARS["go"]["right_rate_min"]:
        return "go"
    return "mitigate" if wrong <= BARS["mitigate"]["wrong_rate_max"] else "no_go"


def _trial(case: Case, arm: str, repeat: int, system: str, ask: Ask, now: datetime) -> dict[str, Any]:
    record = {"id": case.id, "say": case.say, "category": case.category, "repeat": repeat}
    started = time.perf_counter()
    try:
        calls = ask(arm, system, case.say)
    except Exception as exc:  # a failed request says nothing about the model's dates
        return {**record, "outcome": "error", "kind": "error", "detail": f"{type(exc).__name__}: {exc}"[:200],
                "calls": [], "seconds": round(time.perf_counter() - started, 3)}
    record["seconds"] = round(time.perf_counter() - started, 3)
    verdict = judge(case, calls, now=now)
    return {**record, "outcome": verdict.outcome, "kind": verdict.kind, "detail": verdict.detail,
            "calls": [{"name": call.name, "args": call.args} for call in calls]}


def run_probe(
    cases: Sequence[Case],
    ask: Ask,
    *,
    now: datetime,
    repeats: int = 3,
    arms: Sequence[str] = ARMS,
    workers: int = 8,
    model: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Answer every case `repeats` times in every arm and return the report."""
    report_arms: dict[str, Any] = {}
    for arm in arms:
        system = system_prompt(arm, now)
        jobs = [(case, repeat) for case in cases for repeat in range(1, repeats + 1)]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            trials = list(pool.map(lambda job: _trial(job[0], arm, job[1], system, ask, now), jobs))
        report_arms[arm] = {"summary": summarize(trials), "trials": trials}

    def standing(arm: str) -> tuple[float, float]:
        summary = report_arms[arm]["summary"]
        return (summary["wrong_rate"] if summary["wrong_rate"] is not None else 2.0, -(summary["right_rate"] or 0.0))

    best = min(report_arms, key=standing)
    return {
        "schema": 1,
        "model": model or {},
        "now": now.isoformat(),
        "cases": len(cases),
        "repeats": repeats,
        "bars": BARS,
        "best_arm": best,
        "decision": decide(report_arms[best]["summary"]),
        "arms": report_arms,
    }


def model_asker(provider: str, model: str, api_key: Optional[str], base_url: Optional[str]) -> Ask:
    """Ask a real chat model, forcing it to answer with one or more of the tools."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from kairo.core.llm_env import build_llm

    llm = build_llm(provider, model, api_key, base_url)
    many = llm.bind_tools(openai_tools(), tool_choice="any")
    one = llm.bind_tools(openai_tools(), tool_choice="any", parallel_tool_calls=False)

    def ask(arm: str, system: str, say: str) -> list[Call]:
        bound = one if single_call(arm) else many
        response = bound.invoke([SystemMessage(content=system), HumanMessage(content=say)])
        calls = [Call(call["name"], call["args"]) for call in response.tool_calls]
        # Arguments the SDK could not parse are still an answer: a malformed one.
        calls += [Call(call.get("name") or "", {}) for call in getattr(response, "invalid_tool_calls", [])]
        return calls

    return ask


# ── Report ────────────────────────────────────────────────────────────────────

def save_report(report: dict[str, Any], target: Union[str, Path]) -> Path:
    """Write the report as JSON. An existing file is evidence and is never replaced."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "x", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)
    return target


def _percent(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def render(report: dict[str, Any]) -> str:
    """The report as text: each arm against the bars, the decision, then every answer that was not right."""
    model = report["model"].get("model") or "unknown model"
    go = BARS["go"]
    lines = [
        f"Model date test · {model} · {report['cases']} cases x {report['repeats']} repeats · clock {report['now']}",
        f"Bars: go needs wrong <= {_percent(go['wrong_rate_max'])} and right >= {_percent(go['right_rate_min'])}; "
        f"wrong above {_percent(BARS['mitigate']['wrong_rate_max'])} is no-go",
        "",
        f"  {'arm':<16}{'right':>8}{'safe miss':>11}{'wrong':>8}{'errors':>8}{'median':>9}   mistakes",
    ]
    for arm, data in report["arms"].items():
        summary = data["summary"]
        kinds = ", ".join(f"{kind} {n}" for kind, n in sorted(summary["kinds"].items(), key=lambda kv: -kv[1]))
        median = "n/a" if summary["latency_median_s"] is None else f"{summary['latency_median_s']:.2f}s"
        lines.append(f"  {arm:<16}{_percent(summary['right_rate']):>8}{_percent(summary['safe_miss_rate']):>11}"
                     f"{_percent(summary['wrong_rate']):>8}{summary['errors']:>8}{median:>9}   {kinds or 'none'}")
    lines += ["", f"Best arm: {report['best_arm']} → {report['decision'].upper().replace('_', '-')}"]

    best = report["arms"][report["best_arm"]]
    lines += ["", f"By category ({report['best_arm']}):"]
    for category, row in sorted(best["summary"]["by_category"].items()):
        lines.append(f"  {category:<16}{row['right']:>3}/{row['trials']:<3} right, {row['wrong']} wrong, "
                     f"{row['safe_miss']} safe miss")
    if best["summary"]["unstable"]:
        lines += ["", "Answers that changed between repeats: " + ", ".join(best["summary"]["unstable"])]
    for arm, data in report["arms"].items():
        misses = [trial for trial in data["trials"] if trial["outcome"] not in ("right",)]
        lines += ["", f"Not right in {arm} ({len(misses)}):"]
        seen: Counter = Counter()
        for trial in misses:
            key = (trial["id"], trial["kind"], trial["detail"])
            seen[key] += 1
            if seen[key] == 1:
                lines.append(f"  {trial['id']} {trial['kind']:<17} {trial['say']}")
                lines.append(f"      {trial['detail']}")
    return "\n".join(lines)
