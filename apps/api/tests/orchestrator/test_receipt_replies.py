"""Writes in web chat are answered from what was stored, with no judge and no rewriting model."""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import time_machine

from kairo.agents.orchestrator import agent as orch
from kairo.agents.orchestrator import memory as orch_memory
from kairo.agents.orchestrator.harness import HarnessVerdict
from kairo.agents.orchestrator.translator import StructuredAction
from kairo.core.domain.receipt import Receipt
from kairo.storage.decision_log import list_turn_decisions
from kairo.storage.store import load_schedule, load_todos
from kairo.tools import receipts

THREAD = "pm-receipt-replies"
THURSDAY = datetime(2026, 11, 12, 10, 0, tzinfo=ZoneInfo("America/Toronto"))


@pytest.fixture(autouse=True)
def fresh_orchestrator_memory():
    orch_memory._sessions.clear()
    yield
    orch_memory._sessions.clear()


@pytest.fixture
def chat(tmp_path, monkeypatch):
    """Send messages through the real orchestrator and typed workflow, with every model call forbidden.

    The translator is replaced by one that passes the message through, so what is
    under test is everything after it: workflow, storage, read-back and reply.
    """
    config = orch.OrchestratorConfig(session_id=THREAD, user_id="receipt-chat", data_dir=str(tmp_path),
                                     provider="test", model="test", pm_provider="test", pm_model="unused")
    calls: list[str] = []

    def forbidden(name):
        def fail(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"{name} must not run for a write that was read back")
        return fail

    monkeypatch.setattr(orch, "_route", lambda *_a, **_k: True)
    monkeypatch.setattr(orch, "_translate", lambda message, *_a, **_k: StructuredAction(
        intent="write", timeframe=None, entities=[], confidence=0.95, pm_prompt=message, is_write=True))
    monkeypatch.setattr(orch, "_judge", forbidden("judge"))
    monkeypatch.setattr(orch, "_humanize", forbidden("humanize"))
    monkeypatch.setattr(orch, "_llm", forbidden("model"))

    def send(message: str) -> str:
        with time_machine.travel(THURSDAY, tick=True):
            return orch.run_orchestrator(message, config)

    send.config, send.calls, send.data_dir = config, calls, str(tmp_path)
    return send


def test_an_added_task_is_answered_from_the_stored_record(chat):
    reply = chat("add todo review Q4 hiring plan")

    assert reply == "Added task: review Q4 hiring plan — no due date."
    assert [item.title for item in load_todos("receipt-chat", chat.data_dir).items] == ["review Q4 hiring plan"]
    assert chat.calls == []


def test_an_added_event_is_read_back_with_weekday_date_and_time(chat):
    reply = chat("Schedule a board prep session Tuesday at 3pm")

    (event,) = load_schedule("receipt-chat", chat.data_dir).entries
    assert (event.date, event.start) == ("2026-11-17", "15:00")
    assert reply == "Added to your calendar: board prep session — Tue 17 Nov, 3 PM–4 PM."


def test_a_reply_shows_what_was_stored_even_when_that_is_not_what_was_said(chat):
    # The regex workflow reads the "1" in "1:1" as the time. The reply must not hide that.
    reply = chat("Add a weekly 1:1 with Sara every Wednesday at 10am")

    assert "1 AM" in reply and "10 AM" not in reply


def test_an_approved_change_shows_before_and_after(chat):
    chat("Schedule a board prep session Tuesday at 3pm")
    prompt = chat("Move the board prep session to 4pm")
    assert prompt.startswith("Approval required [")  # unchanged: nothing is stored until approval

    reply = chat("approve")

    assert reply == "Updated: board prep session — Tue 17 Nov, 4 PM–5 PM (was Tue 17 Nov, 3 PM–4 PM)."
    assert load_schedule("receipt-chat", chat.data_dir).entries[0].start == "16:00"


def test_a_typed_answer_that_wrote_nothing_is_passed_through_without_a_judge(chat):
    reply = chat("complete renew passport")

    assert "couldn't confirm" not in reply.lower()
    assert load_todos("receipt-chat", chat.data_dir).items == []
    assert chat.calls == []


def test_a_refused_write_says_so_instead_of_asking_the_user_to_check(chat):
    # The web path has no memory vault configured, so the executor refuses.
    reply = chat("remember that I prefer deep work in the mornings")

    assert "not configured" in reply and "couldn't confirm" not in reply.lower()
    assert chat.calls == []


def test_the_decision_trace_records_that_the_reply_came_from_a_read_back(chat):
    chat("add todo review Q4 hiring plan")

    rows = [row for row in list_turn_decisions(THREAD, chat.data_dir, user_id="receipt-chat")
            if row["workingMemory"]["source"] == "orchestrator"]
    assert len(rows) == 1
    assert rows[0]["routing"]["mode"] == "EXECUTED" and "read back" in rows[0]["routing"]["reason"]


def test_a_success_that_changed_nothing_still_goes_to_the_judge(chat, monkeypatch):
    """Already done, or written to an external calendar: the stores cannot confirm it, so nothing is skipped."""
    judged: list[str] = []

    async def already_logged(_prompt, _config):
        receipts.record(Receipt("habit_checkin", "unverified", message="Logged **stretch**."))
        receipts.note_workflow_reply()
        return "Logged **stretch**."

    def judge(_message, _action, pm_output, _profile, _config):
        judged.append(pm_output)
        return HarnessVerdict("pass", 1.0, "ok", "", "null")

    monkeypatch.setattr(orch, "_call_pm", already_logged)
    monkeypatch.setattr(orch, "_judge", judge)
    monkeypatch.setattr(orch, "_humanize", lambda _message, pm_output, *_a: pm_output + " (humanized)")

    assert chat("I did my stretch today") == "Logged **stretch**. (humanized)"
    assert judged == ["Logged **stretch**."]


def test_a_workflow_call_with_no_evidence_is_judged_as_before(tmp_path, monkeypatch):
    """With the workflow stubbed there is no receipt and no typed-answer mark, so nothing is skipped."""
    config = orch.OrchestratorConfig(session_id=THREAD, data_dir=str(tmp_path), provider="test", model="test",
                                     pm_provider="test", pm_model="test")
    judged: list[str] = []

    async def stub_pm(_prompt, _config):
        return "Added 'x'."

    def judge(_message, _action, pm_output, _profile, _config):
        judged.append(pm_output)
        return HarnessVerdict("pass", 1.0, "ok", "", "null")

    monkeypatch.setattr(orch, "_route", lambda *_a, **_k: True)
    monkeypatch.setattr(orch, "_translate", lambda message, *_a, **_k: StructuredAction(
        intent="add_todo", timeframe=None, entities=[], confidence=0.95, pm_prompt=message, is_write=True))
    monkeypatch.setattr(orch, "_call_pm", stub_pm)
    monkeypatch.setattr(orch, "_judge", judge)
    monkeypatch.setattr(orch, "_humanize", lambda _message, pm_output, *_a: pm_output + " (humanized)")

    async def run() -> str:
        reply = ""
        async for kind, value in orch.astream_orchestrator("add todo x", config):
            if kind == "done":
                reply = value
        return reply

    assert asyncio.run(run()) == "Added 'x'. (humanized)" and judged == ["Added 'x'."]
