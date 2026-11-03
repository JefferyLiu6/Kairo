"""Golden multi-turn transcripts: replay each conversation in cases/*.json and
check the reply and the stored state after every turn.

These pin today's behaviour so refactors can prove nothing changed. No model
calls: provider keys are removed, so only the typed (deterministic) path runs.

Case file shape:
{
  "description": "...",
  "seed": {"schedule": [{"id": "...", "title": "..."}], "todos": [{"title": "..."}]},
  "model_plans": {                             # optional: fake model output per message
    "Add golf tmr": {"tasks": [{"task_id": "task-1", "intent": "CREATE_SCHEDULE_EVENT", ...}]}
  },
  "turns": [
    {"user": "approve {approval_id}",          # {approval_id} = newest pending approval
     "expect": {
       "reply_contains": ["..."],
       "reply_not_contains": ["..."],
       "approvals": {"pending": 0, "executed": 1},   # count per status
       "schedule_titles": ["..."],                   # exact, in order
       "todo_titles": ["..."],
       "has_pending_question": false                 # clarification / choice waiting
     }}
  ]
}
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import kairo.agents.personal_manager.workflow as pm_workflow
from kairo.agents.personal_manager.agent import PMConfig, run_pm
from kairo.agents.personal_manager.application.clarification import _load_pending
from kairo.storage.control_store import list_approval_requests
from kairo.storage.store import ScheduleData, ScheduleEntry, TodoData, TodoItem, load_schedule, load_todos, save_schedule, save_todos

SESSION = "pm-golden"
CASES_DIR = Path(__file__).parent / "cases"
CASE_FILES = sorted(CASES_DIR.glob("*.json"))


@pytest.fixture(autouse=True)
def _no_model_calls(monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OLLAMA_API_KEY", "OLLAMA", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


def _seed(seed: dict, data_dir: str) -> None:
    if seed.get("schedule"):
        entries = [ScheduleEntry(**entry) for entry in seed["schedule"]]
        save_schedule(ScheduleData(entries=entries), SESSION, data_dir)
    if seed.get("todos"):
        items = [TodoItem(**item) for item in seed["todos"]]
        save_todos(TodoData(items=items), SESSION, data_dir)


def _install_model_plans(plans: dict, monkeypatch) -> None:
    """Stand in for the model planner: known messages get a fixed plan, others get none."""
    parsed = {
        message: pm_workflow.PMPlanExtraction(source="model_structured", **plan)
        for message, plan in plans.items()
    }
    monkeypatch.setattr(
        pm_workflow, "_extract_pm_plan_with_model", lambda message, *_args, **_kwargs: parsed.get(message)
    )


def _fill_placeholders(message: str, data_dir: str) -> str:
    if "{approval_id}" in message:
        pending = list_approval_requests(SESSION, data_dir, status="pending")
        assert pending, "turn uses {approval_id} but no approval is pending"
        message = message.replace("{approval_id}", pending[-1].id)
    return message


def _check(expect: dict, reply: str, data_dir: str, where: str) -> None:
    for text in expect.get("reply_contains", []):
        assert text in reply, f"{where}: expected {text!r} in reply:\n{reply}"
    for text in expect.get("reply_not_contains", []):
        assert text not in reply, f"{where}: did not expect {text!r} in reply:\n{reply}"
    if "approvals" in expect:
        approvals = list_approval_requests(SESSION, data_dir)
        for status, count in expect["approvals"].items():
            actual = sum(1 for a in approvals if a.status == status)
            assert actual == count, f"{where}: {status} approvals = {actual}, expected {count}"
    if "schedule_titles" in expect:
        titles = [e.title for e in load_schedule(SESSION, data_dir).entries]
        assert titles == expect["schedule_titles"], f"{where}: schedule = {titles}"
    if "todo_titles" in expect:
        titles = [t.title for t in load_todos(SESSION, data_dir).items]
        assert titles == expect["todo_titles"], f"{where}: todos = {titles}"
    if "has_pending_question" in expect:
        pending = _load_pending(SESSION, data_dir) is not None
        assert pending == expect["has_pending_question"], f"{where}: pending question = {pending}"


@pytest.mark.parametrize("case_file", CASE_FILES, ids=[f.stem for f in CASE_FILES])
def test_golden_transcript(case_file: Path, tmp_path, monkeypatch):
    case = json.loads(case_file.read_text(encoding="utf-8"))
    data_dir = str(tmp_path)
    cfg = PMConfig(provider="openai", model="unused", data_dir=data_dir, session_id=SESSION)
    _seed(case.get("seed", {}), data_dir)
    if case.get("model_plans"):
        _install_model_plans(case["model_plans"], monkeypatch)

    for i, turn in enumerate(case["turns"], start=1):
        message = _fill_placeholders(turn["user"], data_dir)
        reply = run_pm(message, cfg)
        _check(turn.get("expect", {}), reply, data_dir, f"{case_file.stem} turn {i} ({message!r})")
