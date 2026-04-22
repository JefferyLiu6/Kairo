"""Executable eval harness for the Kairo controlled workflow."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from assistant.shared.llm_env import load_default_llm_from_env

from ..persistence.control_store import create_approval_request, list_approval_requests, pm_db_path
from ..persistence.journal import journal_read
from ..persistence.store import (
    ScheduleData,
    ScheduleEntry,
    load_schedule,
    load_todos,
    save_schedule,
)
from ..domain.session import normalize_pm_session_id
from ..workflow import (
    extract_structured_pm_request,
    run_typed_pm_turn,
)
from ..domain.types import PMExtraction, PMIntent


DEFAULT_FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "pm_eval_cases.json"
EVAL_SESSION_ID = "pm-eval"


class PMEvalCase(BaseModel):
    suite: Optional[str] = None
    input: str
    expected_intent: PMIntent
    expected_entities: dict[str, Any] = Field(default_factory=dict)
    confidence_min: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_max: float = Field(default=1.0, ge=0.0, le=1.0)
    expected_action_type: str
    requires_approval: bool = False
    expected_mutation: str = "none"


@dataclass
class EvalPMConfig:
    data_dir: str
    vault_dir: Optional[str] = None
    session_id: str = EVAL_SESSION_ID
    provider: str = "openai"
    model: str = "unused"
    api_key: Optional[str] = None
    base_url: Optional[str] = None


@dataclass(frozen=True)
class PMEvalCheck:
    name: str
    passed: bool
    expected: Any = ""
    actual: Any = ""
    detail: str = ""


@dataclass
class PMEvalCaseResult:
    case: PMEvalCase
    extraction: PMExtraction
    checks: list[PMEvalCheck] = field(default_factory=list)
    reply: str = ""
    actual_action_type: str = ""

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def failures(self) -> list[PMEvalCheck]:
        return [check for check in self.checks if not check.passed]


@dataclass
class PMEvalSuiteResult:
    results: list[PMEvalCaseResult]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def checks_total(self) -> int:
        return sum(len(result.checks) for result in self.results)

    @property
    def checks_passed(self) -> int:
        return sum(1 for result in self.results for check in result.checks if check.passed)

    def compute_metrics(self) -> dict[str, Any]:
        intent_total = intent_correct = 0
        entity_tp = entity_fn = 0
        action_total = action_correct = 0
        mutation_total = mutation_correct = 0
        approval_tp = approval_fp = approval_fn = 0
        clarification_expected = clarification_correct = 0
        unsafe_expected = unsafe_blocked = 0

        for result in self.results:
            for check in result.checks:
                if check.name == "extraction.intent":
                    intent_total += 1
                    if check.passed:
                        intent_correct += 1
                elif check.name.startswith("extraction.entities."):
                    if check.passed:
                        entity_tp += 1
                    else:
                        entity_fn += 1
                elif check.name == "workflow.action_type":
                    action_total += 1
                    if check.passed:
                        action_correct += 1
                elif check.name == "workflow.mutation":
                    mutation_total += 1
                    if check.passed:
                        mutation_correct += 1
                elif check.name == "workflow.requires_approval":
                    expected_approval = result.case.requires_approval
                    # Derive from state snapshot comparison via check values
                    actual_created = check.actual if isinstance(check.actual, bool) else False
                    expected_required = check.expected if isinstance(check.expected, bool) else expected_approval
                    if expected_required and actual_created:
                        approval_tp += 1
                    elif not expected_required and actual_created:
                        approval_fp += 1
                    elif expected_required and not actual_created:
                        approval_fn += 1

            if result.case.expected_action_type == "clarification":
                clarification_expected += 1
                if result.actual_action_type == "clarification":
                    clarification_correct += 1

            if result.case.suite == "adversarial" and result.case.expected_action_type in (
                "web_search_blocked", "private_export"
            ):
                unsafe_expected += 1
                if result.case.requires_approval and any(
                    c.name == "workflow.requires_approval" and c.passed for c in result.checks
                ):
                    unsafe_blocked += 1

        entity_total = entity_tp + entity_fn
        precision_denom = approval_tp + approval_fp
        recall_denom = approval_tp + approval_fn

        return {
            "intent_accuracy": _pct(intent_correct, intent_total),
            "entity_exact_match": _pct(entity_tp, entity_total),
            "entity_f1": _f1(entity_tp, 0, entity_fn),
            "action_correctness": _pct(action_correct, action_total),
            "mutation_correctness": _pct(mutation_correct, mutation_total),
            "approval_precision": _pct(approval_tp, precision_denom),
            "approval_recall": _pct(approval_tp, recall_denom),
            "clarification_rate": _pct(clarification_correct, clarification_expected),
            "unsafe_block_rate": _pct(unsafe_blocked, unsafe_expected),
            "_counts": {
                "intent_total": intent_total,
                "entity_checks": entity_total,
                "action_checks": action_total,
                "mutation_checks": mutation_total,
                "clarification_cases": clarification_expected,
                "unsafe_cases": unsafe_expected,
            },
        }

    def suite_breakdown(self) -> dict[str, dict[str, int]]:
        suites: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "passed": 0})
        for result in self.results:
            name = result.case.suite or "untagged"
            suites[name]["total"] += 1
            if result.passed:
                suites[name]["passed"] += 1
        return dict(suites)

    def format_report(self) -> str:
        metrics = self.compute_metrics()
        breakdown = self.suite_breakdown()
        today = date.today().isoformat()

        lines = [
            "# Kairo Eval Report",
            "",
            f"**Date**: {today}  ",
            f"**Cases**: {self.total}  ",
            f"**Passed**: {self.passed}/{self.total} ({_pct(self.passed, self.total):.1f}%)  ",
            f"**Checks**: {self.checks_passed}/{self.checks_total}  ",
            f"**Suites**: {len(breakdown)}",
            "",
            "## Overall Metrics",
            "",
            "| Metric | Score |",
            "|--------|-------|",
            f"| Intent accuracy | {metrics['intent_accuracy']:.1f}% |",
            f"| Entity exact match | {metrics['entity_exact_match']:.1f}% |",
            f"| Entity F1 | {metrics['entity_f1']:.1f}% |",
            f"| Action correctness | {metrics['action_correctness']:.1f}% |",
            f"| Mutation correctness | {metrics['mutation_correctness']:.1f}% |",
            f"| Approval precision | {metrics['approval_precision']:.1f}% |",
            f"| Approval recall | {metrics['approval_recall']:.1f}% |",
            f"| Clarification rate | {metrics['clarification_rate']:.1f}% |",
            f"| Unsafe-action block rate | {metrics['unsafe_block_rate']:.1f}% |",
            "",
            "## Suite Breakdown",
            "",
            "| Suite | Cases | Passed | Pass Rate |",
            "|-------|-------|--------|-----------|",
        ]

        for suite_name in sorted(breakdown):
            s = breakdown[suite_name]
            rate = _pct(s["passed"], s["total"])
            lines.append(f"| {suite_name} | {s['total']} | {s['passed']} | {rate:.1f}% |")

        lines.append("")
        failures = [r for r in self.results if not r.passed]
        if failures:
            lines += ["## Failures", ""]
            for result in failures:
                suite_tag = f"[{result.case.suite}] " if result.case.suite else ""
                lines.append(f"### {suite_tag}`{result.case.input}`")
                lines.append("")
                lines.append(
                    f"- Intent: `{result.extraction.intent.value}` "
                    f"(confidence={result.extraction.confidence:.2f}, source={result.extraction.source})"
                )
                if result.actual_action_type:
                    lines.append(f"- Action: `{result.actual_action_type}`")
                for check in result.failures():
                    lines.append(f"- **{check.name}**: expected `{check.expected}`, got `{check.actual}`")
                    if check.detail:
                        lines.append(f"  _{check.detail}_")
                lines.append("")
        else:
            lines += ["## Failures", "", "_No failures._", ""]

        lines += [
            "---",
            "_Generated by `make eval-report`_",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        metrics = self.compute_metrics()
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "checksTotal": self.checks_total,
            "checksPassed": self.checks_passed,
            "metrics": metrics,
            "suiteBreakdown": self.suite_breakdown(),
            "results": [
                {
                    "suite": result.case.suite,
                    "input": result.case.input,
                    "passed": result.passed,
                    "intent": result.extraction.intent.value,
                    "confidence": result.extraction.confidence,
                    "source": result.extraction.source,
                    "actualActionType": result.actual_action_type,
                    "reply": result.reply,
                    "failures": [
                        {
                            "name": check.name,
                            "expected": check.expected,
                            "actual": check.actual,
                            "detail": check.detail,
                        }
                        for check in result.failures()
                    ],
                }
                for result in self.results
            ],
        }

    def format_text(self) -> str:
        lines = [
            f"Kairo eval: {self.passed}/{self.total} cases passed "
            f"({self.checks_passed}/{self.checks_total} checks)"
        ]
        for result in self.results:
            if result.passed:
                continue
            lines.append(f"\nFAIL [{result.case.suite or 'untagged'}] {result.case.input}")
            lines.append(
                f"- extraction: {result.extraction.intent.value} "
                f"confidence={result.extraction.confidence:.2f} source={result.extraction.source}"
            )
            if result.actual_action_type:
                lines.append(f"- action: {result.actual_action_type}")
            if result.reply:
                lines.append(f"- reply: {result.reply}")
            for check in result.failures():
                lines.append(f"- {check.name}: expected {check.expected!r}, actual {check.actual!r}")
                if check.detail:
                    lines.append(f"  {check.detail}")
        return "\n".join(lines)


@dataclass(frozen=True)
class _StateSnapshot:
    schedule: list[dict[str, Any]]
    todo_count: int
    pending_approvals: int
    pending_action_types: list[str]
    executed_approvals: int
    journal_text: str
    profile_text: str


def load_eval_cases(path: str | Path = DEFAULT_FIXTURE) -> list[PMEvalCase]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError("PM eval fixture must be a JSON list")
    return [PMEvalCase.model_validate(item) for item in raw]


def run_eval_suite(
    cases: Optional[list[PMEvalCase]] = None,
    *,
    fixture_path: str | Path = DEFAULT_FIXTURE,
    data_dir: Optional[str | Path] = None,
    include_workflow: bool = True,
    use_model: bool = False,
    suite_filter: Optional[str] = None,
) -> PMEvalSuiteResult:
    loaded_cases = cases if cases is not None else load_eval_cases(fixture_path)
    if suite_filter:
        loaded_cases = [c for c in loaded_cases if c.suite == suite_filter]

    tmp_context = tempfile.TemporaryDirectory(prefix="pm-eval-") if data_dir is None else nullcontext(str(data_dir))

    with tmp_context as root:
        results: list[PMEvalCaseResult] = []
        for idx, case in enumerate(loaded_cases):
            case_root = os.path.join(str(root), f"case-{idx:03d}")
            config = _build_config(case_root, use_model=use_model)
            results.append(run_eval_case(case, config, include_workflow=include_workflow))
    return PMEvalSuiteResult(results)


def run_eval_case(
    case: PMEvalCase,
    config: EvalPMConfig,
    *,
    include_workflow: bool = True,
) -> PMEvalCaseResult:
    extraction = extract_structured_pm_request(case.input, config)
    checks = score_extraction(case, extraction)
    result = PMEvalCaseResult(case=case, extraction=extraction, checks=checks)

    if include_workflow:
        _seed_workflow_state(case, config)
        before = _snapshot(config)
        reply = run_typed_pm_turn(case.input, config) or ""
        after = _snapshot(config)
        result.reply = reply
        result.actual_action_type = _infer_action_type(case, before, after, reply)
        result.checks.extend(score_workflow(case, before, after, result.actual_action_type, reply))

    return result


def score_extraction(case: PMEvalCase, extraction: PMExtraction) -> list[PMEvalCheck]:
    checks = [
        PMEvalCheck(
            "extraction.intent",
            extraction.intent == case.expected_intent,
            case.expected_intent.value,
            extraction.intent.value,
        ),
        PMEvalCheck(
            "extraction.confidence",
            case.confidence_min <= extraction.confidence <= case.confidence_max,
            f"{case.confidence_min:.2f}..{case.confidence_max:.2f}",
            f"{extraction.confidence:.2f}",
        ),
    ]

    for key, expected in case.expected_entities.items():
        actual = extraction.entities.get(key)
        checks.append(
            PMEvalCheck(
                f"extraction.entities.{key}",
                _entity_matches(key, expected, actual),
                expected,
                actual,
            )
        )
    return checks


def score_workflow(
    case: PMEvalCase,
    before: _StateSnapshot,
    after: _StateSnapshot,
    actual_action_type: str,
    reply: str,
) -> list[PMEvalCheck]:
    checks = [
        PMEvalCheck(
            "workflow.action_type",
            actual_action_type == case.expected_action_type,
            case.expected_action_type,
            actual_action_type,
        ),
        PMEvalCheck(
            "workflow.requires_approval",
            _created_pending_approval(before, after) == case.requires_approval,
            case.requires_approval,
            _created_pending_approval(before, after),
        ),
        PMEvalCheck(
            "workflow.mutation",
            _mutation_matches(case.expected_mutation, before, after, reply),
            case.expected_mutation,
            _describe_mutation(before, after, reply),
        ),
    ]

    if case.expected_mutation == "approval_created" and case.expected_action_type.startswith("schedule_"):
        checks.append(
            PMEvalCheck(
                "workflow.no_preapproval_schedule_mutation",
                before.schedule == after.schedule,
                before.schedule,
                after.schedule,
            )
        )
    return checks


def _build_config(data_dir: str, *, use_model: bool) -> EvalPMConfig:
    kwargs: dict[str, Any] = {"data_dir": data_dir, "vault_dir": os.path.join(data_dir, "vault")}
    if use_model:
        kwargs.update(load_default_llm_from_env())
    return EvalPMConfig(**kwargs)


def _seed_workflow_state(case: PMEvalCase, config: EvalPMConfig) -> None:
    session_id = normalize_pm_session_id(config.session_id)
    text = case.input.lower()
    entries: list[ScheduleEntry] = []
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    if "dentist" in text:
        entries.append(ScheduleEntry(id="dentist1", title="Dentist appointment", start="09:00", end="10:00"))
    if "alex" in text:
        entries.append(ScheduleEntry(id="alex1", title="Coffee with Alex", start="14:00", end="15:00"))
    if "standup" in text:
        entries.append(ScheduleEntry(id="standup1", title="Morning standup", date=tomorrow, start="09:00", end="09:15"))
    if "second meeting" in text or "move the second" in text:
        entries.extend(
            [
                ScheduleEntry(id="m1", title="Team meeting", date=tomorrow, start="09:00", end="10:00"),
                ScheduleEntry(id="m2", title="Budget meeting", date=tomorrow, start="11:00", end="12:00"),
            ]
        )
    if "first meeting" in text or "move the first" in text:
        entries.extend(
            [
                ScheduleEntry(id="m1", title="Sprint planning", date=tomorrow, start="09:00", end="10:00"),
                ScheduleEntry(id="m2", title="Team sync", date=tomorrow, start="11:00", end="12:00"),
            ]
        )
    if "third event" in text:
        entries.extend(
            [
                ScheduleEntry(id="e1", title="Morning standup", date=tomorrow, start="09:00", end="09:30"),
                ScheduleEntry(id="e2", title="Design review", date=tomorrow, start="10:00", end="11:00"),
                ScheduleEntry(id="e3", title="Team lunch", date=tomorrow, start="12:00", end="13:00"),
            ]
        )
    if "second event" in text or "push the second" in text:
        entries.extend(
            [
                ScheduleEntry(id="e1", title="Morning standup", date=tomorrow, start="09:00", end="09:30"),
                ScheduleEntry(id="e2", title="Design review", date=tomorrow, start="10:00", end="11:00"),
            ]
        )

    if case.expected_action_type == "approval_execute":
        if not entries:
            entries.append(ScheduleEntry(id="dentist1", title="Dentist appointment", date=tomorrow, start="09:00", end="10:00"))

    # Seed two generic events so clarification cases have something to ask about
    if case.expected_action_type == "clarification" and not entries:
        entries.extend([
            ScheduleEntry(id="m1", title="Team meeting", date=tomorrow, start="10:00", end="11:00"),
            ScheduleEntry(id="m2", title="Client call", date=tomorrow, start="14:00", end="15:00"),
        ])

    if entries:
        save_schedule(ScheduleData(entries=entries), session_id, config.data_dir)

    if case.expected_action_type == "approval_execute":
        create_approval_request(
            session_id,
            config.data_dir,
            action_type="schedule_remove",
            payload={"ids": ["dentist1"]},
            summary="Remove schedule event: Dentist appointment",
            risk_level="medium",
        )


def _snapshot(config: EvalPMConfig) -> _StateSnapshot:
    session_id = normalize_pm_session_id(config.session_id)
    approvals = list_approval_requests(session_id, config.data_dir, limit=100)
    profile_path = Path(config.vault_dir or "") / "PROFILE.md"
    journal_db = pm_db_path(session_id, config.data_dir)
    return _StateSnapshot(
        schedule=[entry.model_dump() for entry in load_schedule(session_id, config.data_dir).entries],
        todo_count=len(load_todos(session_id, config.data_dir).items),
        pending_approvals=sum(1 for item in approvals if item.status == "pending"),
        pending_action_types=[item.action_type for item in approvals if item.status == "pending"],
        executed_approvals=sum(1 for item in approvals if item.status == "executed"),
        journal_text=journal_read(journal_db),
        profile_text=profile_path.read_text(encoding="utf-8") if profile_path.exists() else "",
    )


def _infer_action_type(
    case: PMEvalCase,
    before: _StateSnapshot,
    after: _StateSnapshot,
    reply: str,
) -> str:
    if after.pending_approvals > before.pending_approvals:
        for action_type in after.pending_action_types:
            if action_type not in before.pending_action_types:
                return action_type
        return after.pending_action_types[0] if after.pending_action_types else "approval_created"
    if after.executed_approvals > before.executed_approvals:
        return "approval_execute"
    if len(after.schedule) > len(before.schedule):
        return "schedule_add"
    if after.todo_count > before.todo_count:
        return "todo_add"
    if after.journal_text != before.journal_text:
        return "journal_append"
    if after.profile_text != before.profile_text:
        return "remember"
    if _looks_like_clarification(reply):
        return "clarification"
    return "none"


def _created_pending_approval(before: _StateSnapshot, after: _StateSnapshot) -> bool:
    return after.pending_approvals > before.pending_approvals


def _mutation_matches(expected: str, before: _StateSnapshot, after: _StateSnapshot, reply: str) -> bool:
    if expected == "todo_created":
        return after.todo_count > before.todo_count
    if expected == "schedule_created":
        return len(after.schedule) > len(before.schedule)
    if expected == "approval_created":
        return after.pending_approvals > before.pending_approvals
    if expected == "approved_action_executed":
        return after.executed_approvals > before.executed_approvals
    if expected == "journal_created":
        return after.journal_text != before.journal_text
    if expected == "shared_memory_created":
        return after.profile_text != before.profile_text
    if expected == "none":
        return (
            after.schedule == before.schedule
            and after.todo_count == before.todo_count
            and after.pending_approvals == before.pending_approvals
            and after.executed_approvals == before.executed_approvals
            and after.journal_text == before.journal_text
            and after.profile_text == before.profile_text
        )
    return False


def _describe_mutation(before: _StateSnapshot, after: _StateSnapshot, reply: str) -> str:
    changes: list[str] = []
    if after.todo_count > before.todo_count:
        changes.append("todo_created")
    if len(after.schedule) > len(before.schedule):
        changes.append("schedule_created")
    if after.pending_approvals > before.pending_approvals:
        changes.append("approval_created")
    if after.executed_approvals > before.executed_approvals:
        changes.append("approved_action_executed")
    if after.journal_text != before.journal_text:
        changes.append("journal_created")
    if after.profile_text != before.profile_text:
        changes.append("shared_memory_created")
    return ", ".join(changes) or "none"


def _entity_matches(key: str, expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool):
        return bool(actual) is expected
    if isinstance(expected, int):
        try:
            return int(actual) == expected
        except (TypeError, ValueError):
            return False
    if isinstance(expected, str):
        expected_norm = _normalize_expected_string(key, expected)
        actual_norm = _normalize_actual_string(actual)
        if key == "query":
            return expected_norm in actual_norm or actual_norm in expected_norm
        return actual_norm == expected_norm
    return actual == expected


def _normalize_expected_string(key: str, value: str) -> str:
    text = value.strip()
    lower = text.lower()
    if key in {"date", "due", "reference_date"}:
        if lower == "tomorrow":
            return (date.today() + timedelta(days=1)).isoformat()
        if lower.startswith("next "):
            weekday = lower.removeprefix("next ").strip()
            parsed = _next_weekday_iso(weekday)
            if parsed:
                return parsed
    return lower


def _normalize_actual_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _next_weekday_iso(name: str) -> str:
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    if name not in weekdays:
        return ""
    today = date.today()
    days = (weekdays[name] - today.weekday()) % 7
    if days == 0:
        days = 7
    return (today + timedelta(days=days)).isoformat()


def _looks_like_clarification(reply: str) -> bool:
    lower = reply.lower()
    return (
        lower.endswith("?")
        or "i need" in lower
        or "which " in lower
        or "what " in lower
        or "or type another date/time" in lower
    )


def _pct(num: int | float, denom: int | float) -> float:
    if denom == 0:
        return 0.0
    return round(100.0 * num / denom, 2)


def _f1(tp: int, fp: int, fn: int) -> float:
    if tp + fp + fn == 0:
        return 0.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    if precision + recall == 0:
        return 0.0
    return round(100.0 * 2 * precision * recall / (precision + recall), 2)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run Kairo eval cases.")
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE), help="Path to pm_eval_cases.json")
    parser.add_argument("--no-workflow", action="store_true", help="Only score extraction")
    parser.add_argument("--use-model", action="store_true", help="Use configured model extraction")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    parser.add_argument("--report", action="store_true", help="Print Markdown eval report")
    parser.add_argument("--suite", default=None, help="Filter to a specific suite name")
    args = parser.parse_args(argv)

    result = run_eval_suite(
        fixture_path=args.fixture,
        include_workflow=not args.no_workflow,
        use_model=args.use_model,
        suite_filter=args.suite,
    )
    if args.report:
        print(result.format_report())
    elif args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(result.format_text())
    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
