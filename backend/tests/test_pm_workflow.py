from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from assistant.http.pm_app import app
import assistant.personal_manager.agent as pm_agent
import assistant.personal_manager.workflow as pm_workflow
from assistant.personal_manager.agent import PMConfig, run_pm
from assistant.personal_manager.application.semantic_memory import SemanticMemoryCandidate
from assistant.personal_manager.persistence.control_store import (
    create_approval_request,
    list_approval_requests,
    list_audit_events,
)
from assistant.personal_manager.domain.commands import validate_action_payload
from assistant.personal_manager.application.clarification import _load_pending, _pending_path
from assistant.personal_manager.persistence.journal import journal_read
from assistant.personal_manager.persistence.personalization import (
    list_field_choices,
    list_behavioral_patterns,
    list_user_preferences,
    promote_patterns_from_choice_memory,
    record_choice_selected,
    upsert_user_preference,
)
from assistant.personal_manager.persistence.recent_context import list_recent_context, save_recent_context
from assistant.personal_manager.persistence.semantic_memory import list_semantic_memory
from assistant.personal_manager.persistence.working_memory import (
    list_working_memory,
    update_working_memory_timestamps,
)
from assistant.personal_manager.resolvers.schedule import resolve_schedule_targets
from assistant.personal_manager.persistence.store import (
    RecurrenceRule,
    ScheduleData,
    ScheduleEntry,
    TodoData,
    TodoItem,
    load_schedule,
    load_todos,
    save_schedule,
    save_todos,
)


def _cfg(tmp_path, session_id: str = "pm-demo") -> PMConfig:
    return PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        session_id=session_id,
    )


def _next_weekday(target_idx: int) -> str:
    today = date.today()
    days = (target_idx - today.weekday()) % 7
    if days == 0:
        days = 7
    return (today + timedelta(days=days)).isoformat()


def _next_month_bounds() -> tuple[str, str]:
    today = date.today()
    month = today.month + 1 if today.month < 12 else 1
    year = today.year if today.month < 12 else today.year + 1
    start = date(year, month, 1)
    end = (date(year, month + 1, 1) - timedelta(days=1)) if month < 12 else date(year, 12, 31)
    return start.isoformat(), end.isoformat()


def test_typed_schedule_command_validation_rejects_invalid_payloads():
    with pytest.raises(ValidationError):
        validate_action_payload(pm_workflow.PMAction("schedule_remove", {"ids": []}))
    with pytest.raises(ValidationError):
        validate_action_payload(
            pm_workflow.PMAction("schedule_add_override", {"series_id": "series1", "override": {"start": "09:00"}})
        )
    with pytest.raises(ValidationError):
        validate_action_payload(
            pm_workflow.PMAction("schedule_remove", {"ids": ["g1"], "googleEvents": [{"title": "Missing provider id"}]})
        )


def test_schedule_resolver_returns_first_class_target_kinds(tmp_path):
    save_schedule(
        ScheduleData(
            entries=[
                ScheduleEntry(id="one1", title="Dentist", date="2026-04-20", start="09:00", end="10:00"),
                ScheduleEntry(
                    id="series1",
                    title="Standup",
                    date="2026-04-20",
                    start="08:30",
                    end="09:00",
                    recurrence=RecurrenceRule(freq="daily"),
                ),
            ]
        ),
        "pm-demo",
        str(tmp_path),
    )

    one_off = resolve_schedule_targets("pm-demo", str(tmp_path), {"query": "dentist"})
    series = resolve_schedule_targets("pm-demo", str(tmp_path), {"query": "standup"})
    occurrence = resolve_schedule_targets(
        "pm-demo",
        str(tmp_path),
        {"query": "standup", "original_date": "2026-04-21"},
    )

    assert one_off.targets[0].kind == "one_off"
    assert series.targets[0].kind == "series"
    assert occurrence.targets[0].kind == "occurrence"


def _install_fake_model_extractions(monkeypatch, responses: dict[str, dict]):
    def fake_model_extract(message, _config):
        data = responses.get(message)
        if data is None:
            return None
        return pm_workflow.PMExtraction(source="model_structured", **data)

    monkeypatch.setattr(pm_workflow, "_extract_pm_request_with_model", fake_model_extract)


def test_add_task_creates_todo_without_approval(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("Add task to call John tomorrow", cfg)

    todos = load_todos("pm-demo", str(tmp_path))
    assert "Added 'call John'" in reply
    assert len(todos.items) == 1
    assert todos.items[0].title == "call John"
    assert todos.items[0].due == (date.today() + timedelta(days=1)).isoformat()
    assert list_approval_requests("pm-demo", str(tmp_path), status="pending") == []


def test_recurring_next_month_schedule_anchors_series_start(tmp_path):
    cfg = _cfg(tmp_path)
    start, end = _next_month_bounds()

    reply = run_pm("I wanna eat breakfast at 8:30 am everyday next month", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    assert len(schedule.entries) == 1
    entry = schedule.entries[0]
    assert entry.title == "eat breakfast"
    assert entry.date == start
    assert entry.recurrence is not None
    assert entry.recurrence.freq == "daily"
    assert entry.recurrence.until == end
    assert "from" in reply
    assert start in reply
    assert end in reply


def test_bulk_delete_keeps_deterministic_scope_when_model_omits_it(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    message = "delete eat breakfast at 8:30 am everyday next month on calendar"
    start, end = _next_month_bounds()
    _install_fake_model_extractions(
        monkeypatch,
        {
            message: {
                "intent": "REMOVE_SCHEDULE_EVENT",
                "entities": {"query": "eat breakfast", "start": "08:30"},
                "confidence": 0.95,
                "missing_fields": [],
                "reasoning_summary": "Delete breakfast events",
            }
        },
    )

    extraction = pm_workflow.extract_structured_pm_request(message, cfg)

    assert extraction.entities["bulk"] is True
    assert extraction.entities["range_start"] == start
    assert extraction.entities["range_end"] == end


def test_model_plan_bulk_delete_keeps_deterministic_scope(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    message = "delete eat breakfast at 8:30 am everyday next month on calendar"
    start, end = _next_month_bounds()
    model_plan = pm_workflow.PMPlanExtraction(
        tasks=[
            pm_workflow.PMTaskExtraction(
                task_id="task-1",
                intent=pm_workflow.PMIntent.REMOVE_SCHEDULE_EVENT,
                entities={"query": "eat breakfast", "start": "08:30"},
                confidence=0.95,
                source="model_structured",
            )
        ],
        confidence=0.95,
        source="model_structured",
    )
    monkeypatch.setattr(pm_workflow, "_extract_pm_plan_with_model", lambda *_args, **_kwargs: model_plan)

    plan = pm_workflow.extract_pm_plan(message, cfg)

    assert plan.tasks[0].entities["bulk"] is True
    assert plan.tasks[0].entities["range_start"] == start
    assert plan.tasks[0].entities["range_end"] == end


def test_bulk_delete_all_matching_title_and_time_needs_no_range(tmp_path):
    cfg = _cfg(tmp_path)
    message = 'remove all the "Scheduled block" from my calendar 8:30am - 9:30am'

    extraction = pm_workflow.extract_structured_pm_request(message, cfg)

    assert extraction.intent == pm_workflow.PMIntent.REMOVE_SCHEDULE_EVENT
    assert extraction.entities["bulk"] is True
    assert extraction.entities["query"] == "Scheduled block"
    assert extraction.entities["start"] == "08:30"
    assert extraction.entities["end"] == "09:30"
    assert "range_start" not in extraction.entities
    assert "range_end" not in extraction.entities


def test_model_plan_bulk_all_matching_time_drops_range_missing(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    message = 'remove all the "Scheduled block" from my calendar 8:30am - 9:30am'
    model_plan = pm_workflow.PMPlanExtraction(
        tasks=[
            pm_workflow.PMTaskExtraction(
                task_id="task-1",
                intent=pm_workflow.PMIntent.REMOVE_SCHEDULE_EVENT,
                entities={"query": "Scheduled block", "start": "08:30", "end": "09:30"},
                confidence=0.95,
                source="model_structured",
            )
        ],
        global_missing_fields=["range_start", "range_end"],
        confidence=0.95,
        source="model_structured",
    )
    monkeypatch.setattr(pm_workflow, "_extract_pm_plan_with_model", lambda *_args, **_kwargs: model_plan)

    plan = pm_workflow.extract_pm_plan(message, cfg)

    assert plan.global_missing_fields == []
    assert plan.tasks[0].entities["bulk"] is True
    assert plan.tasks[0].entities["query"] == "Scheduled block"
    assert plan.tasks[0].entities["start"] == "08:30"
    assert plan.tasks[0].entities["end"] == "09:30"


def test_model_plan_list_state_defaults_to_deterministic_schedule_target(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    message = "what's on my schedule tomorrow?"
    model_plan = pm_workflow.PMPlanExtraction(
        tasks=[
            pm_workflow.PMTaskExtraction(
                task_id="task-1",
                intent=pm_workflow.PMIntent.LIST_STATE,
                entities={},
                confidence=0.98,
                source="model_structured",
            )
        ],
        confidence=0.98,
        source="model_structured",
    )
    monkeypatch.setattr(pm_workflow, "_extract_pm_plan_with_model", lambda *_args, **_kwargs: model_plan)

    plan = pm_workflow.extract_pm_plan(message, cfg)

    assert plan.tasks[0].intent == pm_workflow.PMIntent.LIST_STATE
    assert plan.tasks[0].entities["target"] == "schedule"


def test_model_plan_list_state_todo_override_uses_explicit_schedule_keyword(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    message = "what's on my schedule tomorrow?"
    model_plan = pm_workflow.PMPlanExtraction(
        tasks=[
            pm_workflow.PMTaskExtraction(
                task_id="task-1",
                intent=pm_workflow.PMIntent.LIST_STATE,
                entities={"target": "todos"},
                confidence=0.99,
                source="model_structured",
            )
        ],
        confidence=0.99,
        source="model_structured",
    )
    monkeypatch.setattr(pm_workflow, "_extract_pm_plan_with_model", lambda *_args, **_kwargs: model_plan)

    plan = pm_workflow.extract_pm_plan(message, cfg)

    assert plan.tasks[0].intent == pm_workflow.PMIntent.LIST_STATE
    assert plan.tasks[0].entities["target"] == "schedule"


def test_model_structured_list_state_calendar_alias_normalizes_to_schedule(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    message = "what's on my calendar tomorrow?"
    _install_fake_model_extractions(
        monkeypatch,
        {
            message: {
                "intent": "LIST_STATE",
                "entities": {"target": "calendar"},
                "confidence": 0.97,
                "missing_fields": [],
                "reasoning_summary": "List calendar items",
            }
        },
    )

    extraction = pm_workflow.extract_structured_pm_request(message, cfg)

    assert extraction.intent == pm_workflow.PMIntent.LIST_STATE
    assert extraction.entities["target"] == "schedule"


def test_empty_habit_list_suggests_starter_habits(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("show my habits", cfg)
    list_reply = pm_workflow.execute_pm_action(
        pm_workflow.PMAction("list_state", {"target": "habits"}),
        cfg,
    )["message"]

    assert "Good starter options" in reply
    assert "Drink a glass of water" in reply
    assert "add habit to" in reply
    assert list_reply == reply


def test_tiny_win_routes_to_coaching_not_state_mutation(tmp_path):
    cfg = _cfg(tmp_path)
    message = "Run Tiny Win. Suggest one useful action I can finish in under 10 minutes, then ask me to reply done when it is complete."

    plan = pm_workflow.extract_pm_plan(message, cfg)
    reply = pm_workflow.run_typed_pm_turn(message, cfg)

    assert len(plan.tasks) == 1
    assert plan.tasks[0].intent == pm_workflow.PMIntent.GENERAL_COACHING
    assert reply is None


def test_tiny_win_prefers_deterministic_coaching_over_model_calendar_mutation(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    message = "Run Tiny Win. Suggest one useful action I can finish in under 10 minutes, then ask me to reply done when it is complete."
    model_plan = pm_workflow.PMPlanExtraction(
        tasks=[
            pm_workflow.PMTaskExtraction(
                task_id="task-1",
                intent=pm_workflow.PMIntent.REMOVE_SCHEDULE_EVENT,
                entities={"query": message},
                confidence=0.95,
                source="model_structured",
            )
        ],
        confidence=0.95,
        source="model_structured",
    )
    monkeypatch.setattr(pm_workflow, "_extract_pm_plan_with_model", lambda *_args, **_kwargs: model_plan)

    plan = pm_workflow.extract_pm_plan(message, cfg)

    assert len(plan.tasks) == 1
    assert plan.tasks[0].intent == pm_workflow.PMIntent.GENERAL_COACHING


def test_pet_quest_prompts_route_to_coaching_not_calendar_mutation(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    messages = [
        "Run Morning Launch. Help me choose today's 3 wins, check them against calendar risks, and give me one first action. Do not change anything.",
        "Run Focus Sprint. Help me choose one target for the next 60 minutes, then give me a simple sprint plan with the first step.",
        "Run Calendar Triage. Review today and tomorrow for conflicts, missing buffers, risky transitions, and one adjustment to consider. Do not change anything.",
        "Run Tiny Win. Suggest one useful action I can finish in under 10 minutes, then ask me to reply done when it is complete.",
    ]

    def fake_model_plan(message, _config):
        return pm_workflow.PMPlanExtraction(
            tasks=[
                pm_workflow.PMTaskExtraction(
                    task_id="task-1",
                    intent=pm_workflow.PMIntent.REMOVE_SCHEDULE_EVENT,
                    entities={"query": message},
                    confidence=0.95,
                    source="model_structured",
                )
            ],
            confidence=0.95,
            source="model_structured",
        )

    monkeypatch.setattr(pm_workflow, "_extract_pm_plan_with_model", fake_model_plan)

    for message in messages:
        plan = pm_workflow.extract_pm_plan(message, cfg)
        reply = pm_workflow.run_typed_pm_turn(message, cfg)

        assert len(plan.tasks) == 1
        assert plan.tasks[0].intent == pm_workflow.PMIntent.GENERAL_COACHING
        assert reply is None


def test_delete_schedule_event_creates_approval_without_mutation(tmp_path):
    cfg = _cfg(tmp_path)
    save_schedule(
        ScheduleData(entries=[ScheduleEntry(id="dentist1", title="Dentist appointment")]),
        "pm-demo",
        str(tmp_path),
    )

    reply = run_pm("Delete my dentist appointment", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    approvals = list_approval_requests("pm-demo", str(tmp_path), status="pending")
    assert "Approval required" in reply
    assert len(schedule.entries) == 1
    assert len(approvals) == 1
    assert approvals[0].action_type == "schedule_remove"


def test_approving_schedule_delete_executes_once(tmp_path):
    cfg = _cfg(tmp_path)
    save_schedule(
        ScheduleData(entries=[ScheduleEntry(id="dentist1", title="Dentist appointment")]),
        "pm-demo",
        str(tmp_path),
    )
    run_pm("Delete my dentist appointment", cfg)
    approval = list_approval_requests("pm-demo", str(tmp_path), status="pending")[0]

    first = run_pm(f"approve {approval.id}", cfg)
    second = run_pm(f"approve {approval.id}", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    approvals = list_approval_requests("pm-demo", str(tmp_path))
    assert "Removed 'Dentist appointment' from your calendar." in first
    assert "Removed 'Dentist appointment' from your calendar." in second
    assert schedule.entries == []
    assert approvals[0].status == "executed"


def test_concurrent_approval_execution_is_claimed_once(tmp_path, monkeypatch):
    approval = create_approval_request(
        "pm-demo",
        str(tmp_path),
        action_type="schedule_remove",
        payload={"ids": ["dentist1"]},
        summary="Remove schedule event: Dentist appointment",
        risk_level="medium",
    )
    barrier = threading.Barrier(2)
    executed: list[str] = []

    def slow_execute(action, config):
        executed.append(action.action_type)
        time.sleep(0.05)
        return {"ok": True, "message": "OK: executed approval"}

    monkeypatch.setattr(pm_workflow, "execute_pm_action", slow_execute)

    def approve() -> str:
        barrier.wait()
        return pm_workflow.approve_pm_request(
            approval.id,
            str(tmp_path),
            session_id="pm-demo",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: approve(), range(2)))

    approvals = list_approval_requests("pm-demo", str(tmp_path))
    events = list_audit_events("pm-demo", str(tmp_path))
    executed_events = [e for e in events if e["eventType"] == "approval_executed"]

    assert executed == ["schedule_remove"]
    assert replies.count("OK: executed approval") == 2 or (
        replies.count("OK: executed approval") == 1
        and any("already being executed" in reply for reply in replies)
    )
    assert approvals[0].status == "executed"
    assert len(executed_events) == 1


def test_approval_id_is_scoped_to_chat_session_when_provided(tmp_path, monkeypatch):
    approval = create_approval_request(
        "pm-demo",
        str(tmp_path),
        action_type="schedule_remove",
        payload={"ids": ["dentist1"]},
        summary="Remove schedule event: Dentist appointment",
        risk_level="medium",
    )

    monkeypatch.setattr(
        pm_workflow,
        "execute_pm_action",
        lambda action, config: {"ok": True, "message": "unexpected"},
    )

    reply = pm_workflow.approve_pm_request(
        approval.id,
        str(tmp_path),
        session_id="pm-other",
    )

    approvals = list_approval_requests("pm-demo", str(tmp_path))
    assert "No approval request found" in reply
    assert approvals[0].status == "pending"


def test_rejecting_schedule_delete_leaves_state_unchanged(tmp_path):
    cfg = _cfg(tmp_path)
    save_schedule(
        ScheduleData(entries=[ScheduleEntry(id="dentist1", title="Dentist appointment")]),
        "pm-demo",
        str(tmp_path),
    )
    run_pm("Delete my dentist appointment", cfg)
    approval = list_approval_requests("pm-demo", str(tmp_path), status="pending")[0]

    reply = run_pm(f"reject {approval.id}", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    approvals = list_approval_requests("pm-demo", str(tmp_path))
    assert "cancelled that action" in reply
    assert len(schedule.entries) == 1
    assert approvals[0].status == "rejected"


def test_approve_without_id_asks_when_multiple_pending(tmp_path):
    cfg = _cfg(tmp_path)
    save_schedule(
        ScheduleData(
            entries=[
                ScheduleEntry(id="dentist1", title="Dentist appointment"),
                ScheduleEntry(id="doctor1", title="Doctor appointment"),
            ]
        ),
        "pm-demo",
        str(tmp_path),
    )
    run_pm("Delete my dentist appointment", cfg)
    run_pm("Delete my doctor appointment", cfg)

    reply = run_pm("approve", cfg)

    assert "Multiple pending approvals" in reply
    assert "dentist" in reply.lower()
    assert "doctor" in reply.lower()


def test_private_memory_export_requires_approval(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("Export my private memory", cfg)

    approvals = list_approval_requests("pm-demo", str(tmp_path), status="pending")
    assert "Approval required" in reply
    assert len(approvals) == 1
    assert approvals[0].action_type == "private_export"
    assert list_working_memory("pm-demo", str(tmp_path), status="active") == []


def test_sensitive_web_search_creates_blocked_approval(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("Search the web for my diabetes and bank debt plan", cfg)

    approvals = list_approval_requests("pm-demo", str(tmp_path), status="pending")
    assert "Approval required" in reply
    assert approvals[0].action_type == "web_search_blocked"


def test_react_fallback_is_tool_free(tmp_path, monkeypatch):
    captured: dict[str, object] = {}

    class FakeAgent:
        def stream(self, *_args, **_kwargs):
            yield {"messages": [AIMessage(content="Fallback reply")]}

    def fake_create_react_agent(llm, tools, prompt, checkpointer):
        captured["tools"] = tools
        captured["prompt"] = prompt.content
        return FakeAgent()

    def fail_build_tools(_config):
        raise AssertionError("fallback must not build Kairo tools")

    monkeypatch.setattr(pm_agent, "_get_checkpointer", lambda _data_dir: object())
    monkeypatch.setattr(pm_agent, "build_llm", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(pm_agent, "has_api_key", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(pm_agent, "_build_context_block", lambda _config: "No saved context.")
    monkeypatch.setattr(pm_agent, "create_react_agent", fake_create_react_agent)
    monkeypatch.setattr(pm_agent, "_build_tools", fail_build_tools)

    reply = pm_agent._run_pm_react("I feel overwhelmed today", _cfg(tmp_path))

    assert reply == "Fallback reply"
    assert captured["tools"] == []
    assert "conversation-only mode" in str(captured["prompt"])


def test_unknown_stateful_request_does_not_reach_react_fallback(tmp_path, monkeypatch):
    def fail_fallback(_message, _config):
        raise AssertionError("stateful note request should not enter fallback")

    monkeypatch.setattr(pm_agent, "_run_pm_react", fail_fallback)

    reply = run_pm("Write a note that launch prep matters", _cfg(tmp_path))

    assert "more specific task" in reply


def test_audit_log_records_success_rejection_and_failed_approval(tmp_path):
    cfg = _cfg(tmp_path)
    run_pm("Add task to call John tomorrow", cfg)
    run_pm("Search the web for my diabetes and bank debt plan", cfg)
    web_approval = list_approval_requests("pm-demo", str(tmp_path), status="pending")[0]
    run_pm(f"approve {web_approval.id}", cfg)
    save_schedule(
        ScheduleData(entries=[ScheduleEntry(id="dentist1", title="Dentist appointment")]),
        "pm-demo",
        str(tmp_path),
    )
    run_pm("Delete my dentist appointment", cfg)
    delete_approval = list_approval_requests("pm-demo", str(tmp_path), status="pending")[0]
    run_pm(f"reject {delete_approval.id}", cfg)

    events = list_audit_events("pm-demo", str(tmp_path))
    event_types = {event["eventType"] for event in events}
    assert "action_executed" in event_types
    assert "approval_failed" in event_types
    assert "approval_rejected" in event_types


def test_ambiguous_schedule_request_offers_ranked_choices(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("Schedule dentist appointment", cfg)

    assert "I found a few" in reply
    assert "My recommendation is 1" in reply
    assert "1. Tomorrow at 10 AM" in reply
    assert "2." in reply
    assert "3." in reply
    assert "Or type another date/time." in reply
    pending = _load_pending("pm-demo", str(tmp_path))
    assert pending is not None
    assert pending["type"] == "field_choices"
    assert pending["choices"][0]["confidence_components"]["semantic_fit_confidence"] == 0.85
    assert load_schedule("pm-demo", str(tmp_path)).entries == []


def test_ambiguous_breakfast_routine_asks_whether_to_schedule(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("I wanna eat breakfast", cfg)

    assert "Do you want me to schedule breakfast?" in reply
    assert "1. Today at" in reply
    assert "Good fit for breakfast" in reply
    assert "Or type what you want to eat instead." in reply
    pending = _load_pending("pm-demo", str(tmp_path))
    assert pending is not None
    assert pending["type"] == "field_choices"
    assert pending["plan"]["tasks"][0]["entities"]["ambiguous_life_event"] is True
    assert pending["choices"][0]["values"]["date"] == date.today().isoformat()
    assert load_schedule("pm-demo", str(tmp_path)).entries == []


def test_ambiguous_breakfast_other_text_leaves_scheduling_path(tmp_path):
    cfg = _cfg(tmp_path)
    run_pm("I wanna eat breakfast", cfg)

    reply = pm_workflow.run_typed_pm_turn("pancakes", cfg)

    assert reply is None
    assert _load_pending("pm-demo", str(tmp_path)) is None
    assert load_schedule("pm-demo", str(tmp_path)).entries == []


def test_time_slot_recommendation_uses_typed_context_without_mutation(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("what should I do at 8pm tdy?", cfg)

    assert "At 8 PM today" in reply
    assert "My recommendation is 1" in reply
    assert "Wind down" in reply
    assert "Reply 1, 2, or 3 to put one on your calendar" in reply
    assert load_schedule("pm-demo", str(tmp_path)).entries == []
    pending = _load_pending("pm-demo", str(tmp_path))
    assert pending is not None
    assert pending["type"] == "field_choices"
    assert pending["choices"][0]["values"]["title"] == "Wind down"
    events = list_audit_events("pm-demo", str(tmp_path))
    assert any(event["eventType"] == "time_slot_recommendation" for event in events)


def test_time_slot_recommendation_number_reply_adds_selected_calendar_block(tmp_path):
    cfg = _cfg(tmp_path)
    run_pm("what should I do at 8pm tdy?", cfg)
    pending = _load_pending("pm-demo", str(tmp_path))
    assert pending is not None
    selected = pending["choices"][1]["values"]

    reply = run_pm("2", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    assert reply.startswith(f"Done! Added '{selected['title']}'")
    assert len(schedule.entries) == 1
    assert schedule.entries[0].title == selected["title"]
    assert schedule.entries[0].date == date.today().isoformat()
    assert schedule.entries[0].start == "20:00"
    assert schedule.entries[0].end == selected["end"]
    assert _load_pending("pm-demo", str(tmp_path)) is None
    records = list_working_memory("pm-demo", str(tmp_path))
    assert records[0].status == "resolved"


def test_time_slot_recommendation_label_reply_adds_selected_calendar_block(tmp_path):
    cfg = _cfg(tmp_path)
    run_pm("what should I do at 8pm tdy?", cfg)

    reply = run_pm("the reading one", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    assert reply.startswith("Done! Added 'Read for 20 minutes'")
    assert len(schedule.entries) == 1
    assert schedule.entries[0].title == "Read for 20 minutes"
    assert schedule.entries[0].start == "20:00"


def test_working_memory_cancel_reply_clears_active_state(tmp_path):
    cfg = _cfg(tmp_path)
    run_pm("what should I do at 8pm tdy?", cfg)

    reply = run_pm("cancel", cfg)

    assert "cancelled" in reply.lower()
    assert load_schedule("pm-demo", str(tmp_path)).entries == []
    assert _load_pending("pm-demo", str(tmp_path)) is None
    assert list_working_memory("pm-demo", str(tmp_path))[0].status == "cancelled"


def test_high_confidence_new_request_replaces_active_working_memory(tmp_path):
    cfg = _cfg(tmp_path)
    run_pm("schedule breakfast", cfg)

    reply = run_pm("actually schedule dentist at 9am tomorrow", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    assert reply.startswith("Done! Added")
    assert len(schedule.entries) == 1
    assert "dentist" in schedule.entries[0].title
    assert schedule.entries[0].start == "09:00"
    records = list_working_memory("pm-demo", str(tmp_path))
    assert records[0].status == "replaced"


def test_stale_working_memory_does_not_swallow_new_request(tmp_path):
    cfg = _cfg(tmp_path)
    run_pm("schedule breakfast", cfg)
    active = list_working_memory("pm-demo", str(tmp_path), status="active")[0]
    old = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    update_working_memory_timestamps("pm-demo", str(tmp_path), active.id, last_updated_at=old, expires_at=future)

    reply = run_pm("add pay rent to my todo", cfg)

    todos = load_todos("pm-demo", str(tmp_path))
    assert "Added 'pay rent'" in reply
    assert [item.title for item in todos.items] == ["pay rent"]
    records = list_working_memory("pm-demo", str(tmp_path))
    assert records[0].status == "replaced"


def test_expired_working_memory_is_ignored(tmp_path):
    cfg = _cfg(tmp_path)
    run_pm("schedule breakfast", cfg)
    active = list_working_memory("pm-demo", str(tmp_path), status="active")[0]
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    update_working_memory_timestamps("pm-demo", str(tmp_path), active.id, expires_at=past)

    reply = run_pm("add pay rent to my todo", cfg)

    todos = load_todos("pm-demo", str(tmp_path))
    assert "Added 'pay rent'" in reply
    assert [item.title for item in todos.items] == ["pay rent"]
    statuses = {record.status for record in list_working_memory("pm-demo", str(tmp_path))}
    assert "expired" in statuses


def test_legacy_pending_json_migrates_and_resolves(tmp_path):
    cfg = _cfg(tmp_path)
    path = Path(_pending_path("pm-demo", str(tmp_path)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "intent": "CREATE_SCHEDULE_EVENT",
                "entities": {"title": "dentist"},
                "missing": ["date", "start"],
            }
        ),
        encoding="utf-8",
    )

    reply = run_pm("tomorrow at 9am", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    assert reply.startswith("Done! Added 'dentist'")
    assert [(entry.title, entry.start) for entry in schedule.entries] == [("dentist", "09:00")]
    assert not path.exists()
    assert list_working_memory("pm-demo", str(tmp_path))[0].status == "resolved"


def test_activity_self_disclosure_writes_profile_preference_and_context(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )

    reply = run_pm("I like playing basketball", cfg)

    profile = (tmp_path / "vault" / "PROFILE.md").read_text(encoding="utf-8")
    preferences = list_user_preferences("pm-demo", str(tmp_path))
    contexts = list_recent_context("pm-demo", str(tmp_path), context_type="activity_topic")
    assert "remember that you like basketball" in reply
    assert "User likes playing basketball" in profile
    assert [(pref.scope_type, pref.scope_key, pref.rule_type) for pref in preferences] == [
        ("event_type", "basketball", "preferred_activity")
    ]
    assert contexts[0].payload["activity"] == "basketball"
    assert contexts[0].payload["assistant_invited_schedule"] is True


def test_activity_self_disclosure_swimming_creates_preference_and_memory(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-swim",
    )

    reply = run_pm("i like swimming", cfg)

    preferences = list_user_preferences("pm-swim", str(tmp_path))
    contexts = list_recent_context("pm-swim", str(tmp_path), context_type="activity_topic")
    semantic = list_semantic_memory("pm-swim", str(tmp_path), polarity="positive")
    assert "remember that you like swimming" in reply.lower()
    assert [(pref.scope_type, pref.scope_key, pref.rule_type) for pref in preferences] == [
        ("event_type", "swimming", "preferred_activity")
    ]
    assert contexts[0].payload["activity"] == "swimming"
    assert any(record.object == "swimming" for record in semantic)


def test_activity_self_disclosure_guardrails_do_not_create_play_preference(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-watch",
    )

    run_pm("I like watching basketball", cfg)

    preferences = list_user_preferences("pm-watch", str(tmp_path))
    assert preferences == []


def test_contextual_fragment_creates_confirmation_then_yes_executes(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )
    run_pm("I like playing basketball", cfg)
    style = run_pm("just causal playing", cfg)

    offer = run_pm("Saturday 4 pm at the community court", cfg)

    assert "casual basketball" in style
    assert "schedule Basketball at the community court" in offer
    assert "4 PM-5:30 PM" in offer
    assert load_schedule("pm-demo", str(tmp_path)).entries == []
    pending = _load_pending("pm-demo", str(tmp_path))
    assert pending and pending["type"] == "confirmation"
    assert pending["provenance"]["activity"] == "inferred_from_context"
    assert pending["provenance"]["location"] == "user_provided"

    reply = run_pm("yes", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    assert reply.startswith("Done! Added 'Basketball at the community court'")
    assert [(entry.title, entry.date, entry.start, entry.end) for entry in schedule.entries] == [
        ("Basketball at the community court", _next_weekday(5), "16:00", "17:30")
    ]
    assert list_working_memory("pm-demo", str(tmp_path))[0].status == "resolved"


def test_contextual_non_commitment_does_not_schedule(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )
    run_pm("I like playing basketball", cfg)

    reply = run_pm("Saturday 4 pm works better", cfg)

    assert reply == "Did you want me to schedule that?"
    assert load_schedule("pm-demo", str(tmp_path)).entries == []


def test_activity_date_window_followup_asks_for_time_in_context(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )
    run_pm("I like playing basketball", cfg)
    run_pm("competitive", cfg)

    reply = run_pm("maybe next week", cfg)

    assert reply == "Sure — what day and time should I use for basketball?"
    assert load_schedule("pm-demo", str(tmp_path)).entries == []


def test_contextual_intent_override_wins_over_activity_topic(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )
    run_pm("I like playing basketball", cfg)

    reply = run_pm("add pay rent to my todo", cfg)

    assert "Added 'pay rent'" in reply
    assert [item.title for item in load_todos("pm-demo", str(tmp_path)).items] == ["pay rent"]


def test_explicit_schedule_it_uses_activity_context(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )
    run_pm("I like playing basketball", cfg)

    reply = run_pm("schedule it Saturday 4 pm at the community court", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    assert reply.startswith("Done! Added 'Basketball at the community court'")
    assert [(entry.title, entry.start, entry.end) for entry in schedule.entries] == [
        ("Basketball at the community court", "16:00", "17:30")
    ]


def test_explicit_schedule_with_style_word_is_not_swallowed_by_style_followup(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )
    run_pm("I like playing basketball", cfg)
    run_pm("competitive", cfg)

    reply = run_pm("Schedule casual basketball on Tuesday at 4 pm", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    assert reply.startswith("Done! Added 'casual basketball'")
    assert [(entry.title, entry.date, entry.start, entry.end) for entry in schedule.entries] == [
        ("casual basketball", _next_weekday(1), "16:00", "17:30")
    ]


def test_invited_short_time_fragment_creates_confirmation_then_yes_executes(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )
    run_pm("hi, I like playing basketball", cfg)
    run_pm("Casual", cfg)

    offer = run_pm("tmr 8pm", cfg)

    assert "schedule casual basketball" in offer
    assert "8 PM-9:30 PM" in offer
    assert load_schedule("pm-demo", str(tmp_path)).entries == []
    pending = _load_pending("pm-demo", str(tmp_path))
    assert pending and pending["type"] == "confirmation"
    assert pending["provenance"]["activity"] == "inferred_from_context"
    assert pending["provenance"]["style"] == "recent_context"
    assert pending["relevance"]["score"] >= 0.5

    reply = run_pm("yes", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    assert reply.startswith("Done! Added 'casual basketball'")
    assert [(entry.title, entry.date, entry.start, entry.end) for entry in schedule.entries] == [
        ("casual basketball", (date.today() + timedelta(days=1)).isoformat(), "20:00", "21:30")
    ]
    assert list_working_memory("pm-demo", str(tmp_path))[0].status == "resolved"


def test_memory_query_lists_activity_preferences_and_profile_likes(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )
    run_pm("I like playing basketball", cfg)
    run_pm("Casual", cfg)

    piano_reply = run_pm("I like playing piano", cfg)
    joking_reply = run_pm("im also interested in joking", cfg)

    preferences = list_user_preferences("pm-demo", str(tmp_path))
    preference_keys = {(pref.scope_type, pref.scope_key, pref.rule_type) for pref in preferences}
    assert "remember that you like piano" in piano_reply
    assert "Remembered: im also interested in joking" in joking_reply
    assert ("event_type", "basketball", "preferred_activity") in preference_keys
    assert ("event_type", "piano", "preferred_activity") in preference_keys
    assert any(pref.scope_key == "piano" and pref.value["category"] == "creative" for pref in preferences)
    semantic = list_semantic_memory("pm-demo", str(tmp_path), polarity="positive")
    semantic_objects = {record.object for record in semantic}
    assert {"basketball", "piano", "joking"}.issubset(semantic_objects)

    reply = run_pm("what do I like?", cfg)
    followup = run_pm("what else?", cfg)

    assert "You've told me you like:" in reply
    assert "Basketball — casual." in reply
    assert "Joking." in reply
    assert "Piano." in reply
    assert "Basketball — casual." in followup
    assert "Joking." in followup
    assert "Piano." in followup


def test_explicit_remember_writes_semantic_memory_for_retrieval_backed_recall(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )

    reply = run_pm("remember that I prefer dark roast coffee", cfg)
    memory = run_pm("what do I like?", cfg)

    semantic = list_semantic_memory("pm-demo", str(tmp_path), polarity="positive")
    assert reply == "Remembered: I prefer dark roast coffee"
    assert [(record.predicate, record.object) for record in semantic] == [("likes", "dark roast coffee")]
    assert "Dark Roast Coffee." in memory


def test_model_semantic_memory_interpreter_can_save_unrecognized_interest(tmp_path, monkeypatch):
    from assistant.personal_manager.application import semantic_memory as semantic_memory_app

    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )

    def fake_model_interpreter(message, _config):
        if message != "standup comedy has been my thing lately":
            return []
        return [
            SemanticMemoryCandidate(
                memory_type="generic_interest",
                subject="user",
                predicate="interested_in",
                object="standup comedy",
                qualifiers={},
                polarity="positive",
                confidence=0.82,
                stability="stable",
                scheduling_relevance="none",
                sensitivity="low",
                source="model_memory_interpreter",
                evidence=message,
            )
        ]

    monkeypatch.setattr(semantic_memory_app, "_interpret_with_model", fake_model_interpreter)

    reply = run_pm("standup comedy has been my thing lately", cfg)
    memory = run_pm("what do I like?", cfg)

    semantic = list_semantic_memory("pm-demo", str(tmp_path), polarity="positive")
    assert reply == "Remembered: Standup Comedy."
    assert [(record.source, record.object) for record in semantic] == [
        ("model_memory_interpreter", "standup comedy")
    ]
    assert "Standup Comedy." in memory


@pytest.mark.parametrize(
    ("message", "scope_key", "category", "reply_part"),
    [
        ("I enjoy reading", "reading", "personal", "remember that you like reading"),
        ("I play tennis", "tennis", "exercise", "Casual, training, or competitive"),
        ("I'm into yoga", "yoga", "exercise", "Casual, training, or competitive"),
        ("I prefer guitar", "guitar", "creative", "For fun, practice, or performance"),
    ],
)
def test_activity_self_disclosure_accepts_varied_positive_phrases(
    tmp_path,
    message,
    scope_key,
    category,
    reply_part,
):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / f"{scope_key}-vault"),
        session_id=f"pm-{scope_key}",
    )

    reply = run_pm(message, cfg)

    preferences = list_user_preferences(f"pm-{scope_key}", str(tmp_path))
    contexts = list_recent_context(f"pm-{scope_key}", str(tmp_path), context_type="activity_topic")
    assert reply_part in reply
    assert [(pref.scope_key, pref.rule_type, pref.value["category"]) for pref in preferences] == [
        (scope_key, "preferred_activity", category)
    ]
    assert contexts[0].payload["activity"] == scope_key
    assert load_schedule(f"pm-{scope_key}", str(tmp_path)).entries == []


def test_multi_activity_disclosure_remembers_each_and_ambiguous_fragment_disambiguates(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )

    reply = run_pm("I enjoy reading, I play tennis, and I'm into piano", cfg)

    preferences = list_user_preferences("pm-demo", str(tmp_path))
    preference_keys = {pref.scope_key for pref in preferences}
    assert "reading, tennis, and piano" in reply
    assert preference_keys == {"reading", "tennis", "piano"}
    assert {pref.value["category"] for pref in preferences} == {"personal", "exercise", "creative"}

    memory = run_pm("what do I like?", cfg)
    assert "Reading." in memory
    assert "Tennis." in memory
    assert "Piano." in memory

    offer = run_pm("tmr 7pm", cfg)

    # All three freshly-disclosed activities are within the disambiguation
    # window, so all three appear in the prompt.
    for activity in ("piano", "tennis", "reading"):
        assert activity in offer.lower()
    pending = _load_pending("pm-demo", str(tmp_path))
    assert pending is not None
    assert pending.get("type") == "disambiguation"
    assert load_schedule("pm-demo", str(tmp_path)).entries == []


@pytest.mark.parametrize(
    ("disclosure", "fragment", "expected_title", "expected_date_key", "expected_start", "expected_end", "time_text"),
    [
        ("I enjoy reading", "tmr 8pm", "Reading", "tomorrow", "20:00", "21:00", "8 PM-9 PM"),
        ("I play tennis", "Saturday 4 pm at the club", "Tennis at the club", "saturday", "16:00", "17:30", "4 PM-5:30 PM"),
        ("I prefer guitar", "tmr 6pm", "Guitar", "tomorrow", "18:00", "19:00", "6 PM-7 PM"),
    ],
)
def test_contextual_scheduling_works_across_activity_categories(
    tmp_path,
    disclosure,
    fragment,
    expected_title,
    expected_date_key,
    expected_start,
    expected_end,
    time_text,
):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )
    run_pm(disclosure, cfg)

    offer = run_pm(fragment, cfg)

    assert f"schedule {expected_title}" in offer
    assert time_text in offer
    assert load_schedule("pm-demo", str(tmp_path)).entries == []
    assert _load_pending("pm-demo", str(tmp_path))["type"] == "confirmation"

    reply = run_pm("yes", cfg)

    expected_date = (
        (date.today() + timedelta(days=1)).isoformat()
        if expected_date_key == "tomorrow"
        else _next_weekday(5)
    )
    schedule = load_schedule("pm-demo", str(tmp_path))
    assert reply.startswith(f"Done! Added '{expected_title}'")
    assert [(entry.title, entry.date, entry.start, entry.end) for entry in schedule.entries] == [
        (expected_title, expected_date, expected_start, expected_end)
    ]


def test_mixed_watch_only_and_participation_disclosure_saves_only_played_activity_preference(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )

    reply = run_pm("I like watching tennis and playing piano", cfg)

    preferences = list_user_preferences("pm-demo", str(tmp_path))
    contexts = list_recent_context("pm-demo", str(tmp_path), context_type="activity_topic")
    profile = (tmp_path / "vault" / "PROFILE.md").read_text(encoding="utf-8")
    assert "remember that you like piano" in reply
    assert [(pref.scope_key, pref.rule_type) for pref in preferences] == [("piano", "preferred_activity")]
    assert contexts[0].payload["activity"] == "piano"
    assert "User likes watching tennis" in profile


def test_activity_keyword_without_personal_preference_does_not_create_activity_memory(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )

    reply = run_pm("add tennis shoes to my todo", cfg)

    assert "Added 'tennis shoes'" in reply
    assert list_user_preferences("pm-demo", str(tmp_path)) == []
    assert list_recent_context("pm-demo", str(tmp_path), context_type="activity_topic") == []


def test_self_disclosure_reasoning_confidence_and_guardrail_edges(tmp_path):
    qualified_cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "qualified-vault"),
        session_id="pm-qualified",
    )
    run_pm("I like basketball sometimes", qualified_cfg)
    qualified = list_user_preferences("pm-qualified", str(tmp_path))
    assert [(pref.scope_key, pref.confidence) for pref in qualified] == [("basketball", pytest.approx(0.55))]

    rare_cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "rare-vault"),
        session_id="pm-rare",
    )
    rare_reply = run_pm("I like basketball but rarely play", rare_cfg)
    assert "remember that note about basketball" in rare_reply
    assert list_user_preferences("pm-rare", str(tmp_path)) == []

    comparative_cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "comparative-vault"),
        session_id="pm-comparative",
    )
    run_pm("I like basketball more than tennis", comparative_cfg)
    comparative = list_user_preferences("pm-comparative", str(tmp_path))
    assert [(pref.scope_key, pref.confidence) for pref in comparative] == [("basketball", pytest.approx(0.60))]

    health_cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "health-vault"),
        session_id="pm-health",
    )
    health_reply = run_pm("I'm trying to play less basketball because of my knee", health_cfg)
    assert "won't treat basketball as a scheduling preference" in health_reply
    assert list_user_preferences("pm-health", str(tmp_path)) == []


def test_recent_activity_context_disambiguates_two_fresh_topics(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )
    run_pm("I like playing basketball", cfg)
    run_pm("I also like tennis", cfg)

    reply = run_pm("Saturday 4 pm", cfg)

    assert reply == "Did you mean tennis or basketball?"
    assert load_schedule("pm-demo", str(tmp_path)).entries == []
    pending = _load_pending("pm-demo", str(tmp_path))
    assert pending is not None
    assert pending.get("type") == "disambiguation"


def test_recent_activity_context_latest_wins_when_topics_are_not_close(tmp_path):
    cfg = _cfg(tmp_path)
    now = datetime.now(timezone.utc)
    save_recent_context(
        "pm-demo",
        str(tmp_path),
        context_type="activity_topic",
        updated_at=(now - timedelta(minutes=12)).isoformat(),
        expires_at=(now + timedelta(minutes=18)).isoformat(),
        payload={
            "activity": "basketball",
            "activity_label": "Basketball",
            "category": "exercise",
            "assistant_invited_schedule": True,
        },
    )
    save_recent_context(
        "pm-demo",
        str(tmp_path),
        context_type="activity_topic",
        updated_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=30)).isoformat(),
        payload={
            "activity": "tennis",
            "activity_label": "Tennis",
            "category": "exercise",
            "assistant_invited_schedule": True,
        },
    )

    reply = run_pm("Saturday 4 pm at the club", cfg)

    pending = _load_pending("pm-demo", str(tmp_path))
    assert "schedule Tennis at the club" in reply
    assert pending and pending["type"] == "confirmation"
    task = pending["plan"]["tasks"][0]
    assert task["entities"]["title"] == "Tennis at the club"
    assert pending["relevance"]["score"] >= 0.65
    assert "multiple_fresh_topics_without_activity" in pending["relevance"]["components"]


def test_contextual_confirmation_high_confidence_new_request_replaces_pending(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )
    run_pm("I like playing basketball", cfg)
    run_pm("Casual", cfg)
    run_pm("tmr 8pm", cfg)
    assert _load_pending("pm-demo", str(tmp_path)) is not None

    reply = run_pm("add pay rent to my todo", cfg)

    assert "Added 'pay rent'" in reply
    assert [item.title for item in load_todos("pm-demo", str(tmp_path)).items] == ["pay rent"]
    assert load_schedule("pm-demo", str(tmp_path)).entries == []
    assert any(record.status == "replaced" for record in list_working_memory("pm-demo", str(tmp_path)))


def test_contextual_confirmation_yes_with_time_correction_executes_updated_draft(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )
    run_pm("I like playing basketball", cfg)
    run_pm("Casual", cfg)
    run_pm("tmr 8pm", cfg)

    reply = run_pm("yes and make it 4:30pm", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    assert reply.startswith("Done! Added 'casual basketball'")
    assert [(entry.title, entry.start, entry.end) for entry in schedule.entries] == [
        ("casual basketball", "16:30", "18:00")
    ]
    assert list_working_memory("pm-demo", str(tmp_path))[0].status == "resolved"


def test_contextual_confirmation_no_with_date_correction_refreshes_then_executes(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )
    run_pm("I like playing basketball", cfg)
    run_pm("Casual", cfg)
    run_pm("tmr 8pm", cfg)

    refresh = run_pm("no, Sunday instead", cfg)

    pending = _load_pending("pm-demo", str(tmp_path))
    assert "schedule casual basketball" in refresh
    assert load_schedule("pm-demo", str(tmp_path)).entries == []
    assert pending and pending["type"] == "confirmation"
    assert pending["plan"]["tasks"][0]["entities"]["date"] == _next_weekday(6)

    reply = run_pm("yes", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    assert reply.startswith("Done! Added 'casual basketball'")
    assert [(entry.title, entry.date, entry.start, entry.end) for entry in schedule.entries] == [
        ("casual basketball", _next_weekday(6), "20:00", "21:30")
    ]


def test_contextual_confirmation_cancel_leaves_schedule_unchanged(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )
    run_pm("I like playing basketball", cfg)
    run_pm("Casual", cfg)
    run_pm("tmr 8pm", cfg)

    reply = run_pm("cancel", cfg)

    assert reply == "Got it — cancelled that pending request."
    assert load_schedule("pm-demo", str(tmp_path)).entries == []
    assert any(record.status == "cancelled" for record in list_working_memory("pm-demo", str(tmp_path)))


def test_contextual_activity_does_not_override_explicit_meeting_intent(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )
    run_pm("I like playing basketball", cfg)

    reply = run_pm("I need to meet someone Saturday at 4 pm", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    assert "basketball" not in reply.lower()
    assert len(schedule.entries) == 1
    assert "basketball" not in schedule.entries[0].title.lower()
    assert schedule.entries[0].start == "16:00"


def test_time_slot_recommendation_can_use_preferred_activity(tmp_path):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )
    run_pm("I like playing basketball", cfg)

    reply = run_pm("what should I do at 8pm tdy?", cfg)

    assert "Play basketball" in reply
    assert "matches your basketball preference" in reply


def test_time_slot_recommendation_rotates_recently_shown_defaults(tmp_path):
    cfg = _cfg(tmp_path)

    first = run_pm("what should I do at 8pm tdy?", cfg)
    second = run_pm("what should I do at 8pm tdy?", cfg)

    first_top = next(line for line in first.splitlines() if line.startswith("1. "))
    second_top = next(line for line in second.splitlines() if line.startswith("1. "))
    assert first_top != second_top
    shown = list_field_choices(
        "pm-demo",
        str(tmp_path),
        intent="TIME_SLOT_RECOMMENDATION",
        field_name="suggestion",
    )
    assert len(shown) >= 3


def test_time_slot_recommendation_mentions_calendar_conflict(tmp_path):
    cfg = _cfg(tmp_path)
    save_schedule(
        ScheduleData(
            entries=[
                ScheduleEntry(
                    id="dinner",
                    title="Dinner",
                    date=date.today().isoformat(),
                    start="20:00",
                    end="21:00",
                )
            ]
        ),
        "pm-demo",
        str(tmp_path),
    )

    reply = run_pm("what should I do at 8pm tdy?", cfg)

    assert "you already have Dinner" in reply
    assert "My recommendation is to keep that" in reply
    assert "After that, consider:" in reply
    assert len(load_schedule("pm-demo", str(tmp_path)).entries) == 1


def test_time_slot_recommendation_prioritizes_due_todo(tmp_path):
    cfg = _cfg(tmp_path)
    save_todos(
        TodoData(items=[TodoItem(title="Pay rent", due=date.today().isoformat())]),
        "pm-demo",
        str(tmp_path),
    )

    reply = run_pm("what should I do at 8pm tdy?", cfg)

    assert "1. Make progress on 'Pay rent'" in reply
    assert "due today" in reply
    assert load_todos("pm-demo", str(tmp_path)).items[0].done is False


def test_schedule_create_understands_next_friday_after_lunch(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("Schedule dentist appointment next Friday after lunch", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    assert reply.startswith("Done! Added 'dentist appointment'")
    assert len(schedule.entries) == 1
    assert schedule.entries[0].title == "dentist appointment"
    assert schedule.entries[0].date == _next_weekday(4)
    assert schedule.entries[0].start == "13:00"
    assert schedule.entries[0].end == "14:00"


def test_typed_workflow_no_longer_builds_unused_context_snapshot(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("add breakfast at 8am tomorrow", cfg)

    assert not hasattr(pm_workflow, "_context_snapshot")
    assert reply.startswith("Done! Added")
    assert load_schedule("pm-demo", str(tmp_path)).entries[0].title == "breakfast"


def test_need_to_life_event_with_tmr_creates_schedule_without_clarification(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("I NEED TO EAT BREAKFAST AT 8 AM TMR", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    assert reply.startswith("Done! Added 'Eat breakfast'")
    assert len(schedule.entries) == 1
    assert schedule.entries[0].title == "Eat breakfast"
    assert schedule.entries[0].date == (date.today() + timedelta(days=1)).isoformat()
    assert schedule.entries[0].start == "08:00"
    assert schedule.entries[0].end == "09:00"
    assert list_approval_requests("pm-demo", str(tmp_path), status="pending") == []


def test_multiple_bare_calendar_times_use_defaults_without_clarification(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("Add next Friday 11 AM, tomorrow 8 AM, tomorrow 9 AM to my calendar", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    assert reply.startswith("Done! Added 3 events:")
    assert [(entry.title, entry.date, entry.start, entry.end) for entry in schedule.entries] == [
        ("Scheduled block", _next_weekday(4), "11:00", "12:00"),
        ("Scheduled block", (date.today() + timedelta(days=1)).isoformat(), "08:00", "09:00"),
        ("Scheduled block", (date.today() + timedelta(days=1)).isoformat(), "09:00", "10:00"),
    ]
    assert list_approval_requests("pm-demo", str(tmp_path), status="pending") == []


def test_vague_schedule_move_offers_ranked_destination_choices(tmp_path):
    cfg = _cfg(tmp_path)
    save_schedule(
        ScheduleData(entries=[ScheduleEntry(id="dentist1", title="Dentist appointment")]),
        "pm-demo",
        str(tmp_path),
    )

    reply = run_pm("Move my dentist thing", cfg)

    assert "I found a few" in reply
    assert "My recommendation is 1" in reply
    assert "Or type another date/time." in reply
    assert list_approval_requests("pm-demo", str(tmp_path), status="pending") == []


def test_ordinal_schedule_update_resolves_second_meeting(tmp_path):
    cfg = _cfg(tmp_path)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    save_schedule(
        ScheduleData(
            entries=[
                ScheduleEntry(id="m1", title="Team meeting", date=tomorrow, start="09:00", end="10:00"),
                ScheduleEntry(id="d1", title="Dentist appointment", date=tomorrow, start="10:00", end="11:00"),
                ScheduleEntry(id="m2", title="Budget meeting", date=tomorrow, start="11:00", end="12:00"),
            ]
        ),
        "pm-demo",
        str(tmp_path),
    )

    reply = run_pm("Move the second meeting tomorrow to 3pm", cfg)

    approvals = list_approval_requests("pm-demo", str(tmp_path), status="pending")
    schedule = load_schedule("pm-demo", str(tmp_path))
    assert "Approval required" in reply
    assert approvals[0].action_type == "schedule_update"
    assert approvals[0].payload["updates"] == [{"id": "m2", "start": "15:00", "end": "16:00"}]
    assert [entry.start for entry in schedule.entries if entry.id == "m2"] == ["11:00"]


def test_structured_extraction_reports_confidence_and_missing_fields():
    extraction = pm_workflow.extract_structured_pm_request("Move the thing to 3pm")

    assert extraction.intent == pm_workflow.PMIntent.UPDATE_SCHEDULE_EVENT
    assert extraction.confidence < 0.65
    assert "schedule event id or title" in extraction.missing_fields


def test_model_assisted_schedule_move_only_creates_approval(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    next_friday = _next_weekday(4)
    save_schedule(
        ScheduleData(entries=[ScheduleEntry(id="dentist1", title="Dentist appointment")]),
        "pm-demo",
        str(tmp_path),
    )
    _install_fake_model_extractions(
        monkeypatch,
        {
            "push my dentist thing to after lunch next Friday": {
                "intent": "UPDATE_SCHEDULE_EVENT",
                "entities": {
                    "query": "dentist",
                    "date": next_friday,
                    "start": "13:00",
                    "end": "14:00",
                },
                "confidence": 0.94,
                "missing_fields": [],
                "reasoning_summary": "Dentist event move with destination",
            }
        },
    )

    reply = run_pm("push my dentist thing to after lunch next Friday", cfg)

    approvals = list_approval_requests("pm-demo", str(tmp_path), status="pending")
    schedule = load_schedule("pm-demo", str(tmp_path))
    assert "Approval required" in reply
    assert approvals[0].action_type == "schedule_update"
    assert approvals[0].payload["updates"] == [
        {"id": "dentist1", "date": next_friday, "start": "13:00", "end": "14:00"}
    ]
    assert schedule.entries[0].date is None


def test_model_assisted_missing_destination_offers_ranked_choices(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    save_schedule(
        ScheduleData(
            entries=[
                ScheduleEntry(id="m1", title="Team meeting", date=tomorrow, start="09:00", end="10:00"),
                ScheduleEntry(id="m2", title="Budget meeting", date=tomorrow, start="11:00", end="12:00"),
            ]
        ),
        "pm-demo",
        str(tmp_path),
    )
    _install_fake_model_extractions(
        monkeypatch,
        {
            "move the second meeting tomorrow": {
                "intent": "UPDATE_SCHEDULE_EVENT",
                "entities": {"ordinal": 2, "category": "meeting", "reference_date": tomorrow},
                "confidence": 0.82,
                "missing_fields": ["new date or time"],
                "reasoning_summary": "Existing meeting identified; destination absent",
            }
        },
    )

    reply = run_pm("move the second meeting tomorrow", cfg)

    assert "I found a few" in reply
    assert "1. Tomorrow at 2 PM" in reply
    assert "Or type another date/time." in reply
    assert list_approval_requests("pm-demo", str(tmp_path), status="pending") == []


def test_model_assisted_delete_vague_alex_event_requires_approval(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    save_schedule(
        ScheduleData(entries=[ScheduleEntry(id="alex1", title="Coffee with Alex")]),
        "pm-demo",
        str(tmp_path),
    )
    _install_fake_model_extractions(
        monkeypatch,
        {
            "delete the thing with Alex": {
                "intent": "REMOVE_SCHEDULE_EVENT",
                "entities": {"query": "Alex"},
                "confidence": 0.86,
                "missing_fields": [],
                "reasoning_summary": "Vague Alex item maps to schedule event",
            }
        },
    )

    reply = run_pm("delete the thing with Alex", cfg)

    approvals = list_approval_requests("pm-demo", str(tmp_path), status="pending")
    assert "Approval required" in reply
    assert approvals[0].action_type == "schedule_remove"
    assert approvals[0].payload == {"ids": ["alex1"]}


def test_model_assisted_extraction_handles_todo_journal_and_memory(tmp_path, monkeypatch):
    cfg = PMConfig(
        provider="openai",
        model="unused",
        data_dir=str(tmp_path),
        vault_dir=str(tmp_path / "vault"),
        session_id="pm-demo",
    )
    _install_fake_model_extractions(
        monkeypatch,
        {
            "remind me about passport renewal after work": {
                "intent": "CREATE_TODO",
                "entities": {"title": "passport renewal"},
                "confidence": 0.9,
                "missing_fields": [],
                "reasoning_summary": "Reminder becomes todo",
            },
            "log that I felt anxious today": {
                "intent": "JOURNAL_ACTION",
                "entities": {"operation": "append", "body": "I felt anxious today"},
                "confidence": 0.92,
                "missing_fields": [],
                "reasoning_summary": "Log phrase maps to journal",
            },
            "remember I prefer short morning workouts": {
                "intent": "SAVE_MEMORY",
                "entities": {
                    "operation": "save_fact",
                    "fact": "I prefer short morning workouts",
                    "sensitive": False,
                },
                "confidence": 0.91,
                "missing_fields": [],
                "reasoning_summary": "Preference maps to shared memory",
            },
        },
    )

    todo_reply = run_pm("remind me about passport renewal after work", cfg)
    journal_reply = run_pm("log that I felt anxious today", cfg)
    memory_reply = run_pm("remember I prefer short morning workouts", cfg)

    todos = load_todos("pm-demo", str(tmp_path))
    profile = (tmp_path / "vault" / "PROFILE.md").read_text(encoding="utf-8")
    assert "Added 'passport renewal'" in todo_reply
    assert todos.items[0].title == "passport renewal"
    assert "Saved to your journal" in journal_reply
    assert memory_reply == "Remembered: I prefer short morning workouts"
    assert "I prefer short morning workouts" in profile


def test_plan_executes_schedule_todo_and_journal_in_order(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("Tomorrow 8am breakfast, remind me to call John, and log that I felt anxious", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    todos = load_todos("pm-demo", str(tmp_path))
    journal = journal_read(pm_workflow.pm_db_path("pm-demo", str(tmp_path)))
    assert "All done:" in reply
    assert [(entry.title, entry.start) for entry in schedule.entries] == [("breakfast", "08:00")]
    assert [item.title for item in todos.items] == ["call John"]
    assert "felt anxious" in journal


def test_plan_blocks_all_execution_when_one_task_is_unclear(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("Tomorrow 8am breakfast, schedule dentist appointment, and remind me to call John", cfg)

    assert "I found a few" in reply
    assert "My recommendation is 1" in reply
    assert load_schedule("pm-demo", str(tmp_path)).entries == []
    assert load_todos("pm-demo", str(tmp_path)).items == []


def test_plan_clarification_completes_pending_plan(tmp_path):
    cfg = _cfg(tmp_path)
    first = run_pm("Tomorrow 8am breakfast, schedule dentist appointment, and remind me to call John", cfg)

    reply = run_pm("tomorrow 10am", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    todos = load_todos("pm-demo", str(tmp_path))
    assert "I found a few" in first
    assert "All done:" in reply
    assert [(entry.title, entry.start) for entry in schedule.entries] == [
        ("breakfast", "08:00"),
        ("dentist appointment", "10:00"),
    ]
    assert [item.title for item in todos.items] == ["call John"]


def test_plan_numbered_choice_completes_pending_plan(tmp_path):
    cfg = _cfg(tmp_path)
    first = run_pm("Tomorrow 8am breakfast, schedule dentist appointment, and remind me to call John", cfg)

    reply = run_pm("1", cfg)

    schedule = load_schedule("pm-demo", str(tmp_path))
    todos = load_todos("pm-demo", str(tmp_path))
    assert "My recommendation is 1" in first
    assert "All done:" in reply
    assert [(entry.title, entry.start) for entry in schedule.entries] == [
        ("breakfast", "08:00"),
        ("dentist appointment", "10:00"),
    ]
    assert [item.title for item in todos.items] == ["call John"]


def test_explicit_preference_ranks_above_semantic_default(tmp_path):
    cfg = _cfg(tmp_path)
    upsert_user_preference(
        "pm-demo",
        str(tmp_path),
        scope_type="event_type",
        scope_key="breakfast",
        rule_type="start_after",
        value={"time": "10:00"},
    )

    reply = run_pm("schedule breakfast", cfg)

    assert "1. Tomorrow at 10 AM" in reply
    assert "Matches your saved preference" in reply


def test_unknown_event_type_stays_semantically_neutral(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("schedule alpha planning block", cfg)
    pending = _load_pending("pm-demo", str(tmp_path))

    assert "Open option with no known conflict" in reply
    assert "Good fit for" not in reply
    assert pending is not None
    assert pending["choices"][0]["confidence_components"]["semantic_fit_confidence"] == 0.0


def test_choice_ranking_penalizes_recent_repetition_and_diversifies(tmp_path):
    cfg = _cfg(tmp_path)
    record_choice_selected(
        "pm-demo",
        str(tmp_path),
        intent="CREATE_SCHEDULE_EVENT",
        field_name="date_start",
        value={
            "date": (date.today() + timedelta(days=1)).isoformat(),
            "start": "10:00",
            "end": "11:00",
        },
        label="Tomorrow at 10 AM",
        scope_type="event_type",
        scope_key="dentist",
        source="test",
    )

    reply = run_pm("schedule dentist appointment", cfg)
    pending = _load_pending("pm-demo", str(tmp_path))

    assert pending is not None
    assert pending["type"] == "field_choices"
    starts = [choice["values"]["start"] for choice in pending["choices"]]
    pairs = [(choice["values"]["date"], choice["values"]["start"]) for choice in pending["choices"]]
    assert len(pairs) == len(set(pairs))
    assert len(set(starts)) >= 2
    assert any(choice["signals"].get("diversity_bonus", 0) > 0 for choice in pending["choices"][1:])
    assert "1." in reply and "2." in reply and "3." in reply


def test_repeated_choices_promote_event_type_pattern(tmp_path):
    base = date.today()
    for idx in range(4):
        selected_at = (base - timedelta(days=idx)).isoformat() + "T12:00:00+00:00"
        record_choice_selected(
            "pm-demo",
            str(tmp_path),
            intent="CREATE_SCHEDULE_EVENT",
            field_name="date_start",
            value={
                "date": (base + timedelta(days=idx + 1)).isoformat(),
                "start": "09:00",
                "end": "10:00",
            },
            label="9 AM",
            scope_type="event_type",
            scope_key="breakfast",
            source="test",
            selected_at=selected_at,
        )

    promoted = promote_patterns_from_choice_memory(
        "pm-demo",
        str(tmp_path),
        intent="CREATE_SCHEDULE_EVENT",
        field_name="date_start",
        scope_type="event_type",
        scope_key="breakfast",
    )
    patterns = list_behavioral_patterns("pm-demo", str(tmp_path))

    assert promoted
    assert patterns[0].scope_type == "event_type"
    assert patterns[0].scope_key == "breakfast"
    assert patterns[0].rule_type == "preferred_window"
    assert patterns[0].confidence >= 0.77


def test_plan_queues_risky_action_and_runs_later_safe_action(tmp_path):
    cfg = _cfg(tmp_path)
    save_schedule(
        ScheduleData(entries=[ScheduleEntry(id="dentist1", title="Dentist appointment")]),
        "pm-demo",
        str(tmp_path),
    )

    reply = run_pm("delete my dentist appointment, and remind me to call John", cfg)

    approvals = list_approval_requests("pm-demo", str(tmp_path), status="pending")
    todos = load_todos("pm-demo", str(tmp_path))
    schedule = load_schedule("pm-demo", str(tmp_path))
    assert "All done:" in reply
    assert len(approvals) == 1
    assert approvals[0].action_type == "schedule_remove"
    assert [item.title for item in todos.items] == ["call John"]
    assert len(schedule.entries) == 1


def test_plan_hard_failure_stops_later_steps(tmp_path):
    cfg = _cfg(tmp_path)

    reply = run_pm("remember that I prefer tea, and tomorrow 8am breakfast", cfg)

    assert "I hit a problem partway through" in reply
    assert "Stopped at step 1" in reply
    assert load_schedule("pm-demo", str(tmp_path)).entries == []


def test_model_plan_extractor_can_return_multiple_tasks(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)

    def fake_model_plan(message, _config):
        if message != "please handle my morning stuff":
            return None
        return pm_workflow.PMPlanExtraction(
            tasks=[
                pm_workflow.PMTaskExtraction(
                    task_id="task-1",
                    intent=pm_workflow.PMIntent.CREATE_SCHEDULE_EVENT,
                    entities={"title": "breakfast", "date": _tomorrow_iso(), "start": "08:00"},
                    confidence=0.94,
                    source="model_structured",
                ),
                pm_workflow.PMTaskExtraction(
                    task_id="task-2",
                    intent=pm_workflow.PMIntent.CREATE_TODO,
                    entities={"title": "call John"},
                    confidence=0.9,
                    source="model_structured",
                ),
            ],
            confidence=0.9,
            source="model_structured",
        )

    monkeypatch.setattr(pm_workflow, "_extract_pm_plan_with_model", fake_model_plan)

    reply = run_pm("please handle my morning stuff", cfg)

    assert "All done:" in reply
    assert load_schedule("pm-demo", str(tmp_path)).entries[0].title == "breakfast"
    assert load_todos("pm-demo", str(tmp_path)).items[0].title == "call John"


def _tomorrow_iso() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


def test_low_confidence_model_extraction_falls_back_to_regex(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        pm_workflow,
        "_extract_pm_request_with_model",
        lambda _message, _config: pm_workflow.PMExtraction(
            intent="UNKNOWN",
            entities={},
            confidence=0.1,
            missing_fields=[],
            source="model_structured",
        ),
    )

    reply = run_pm("Add task to call John tomorrow", cfg)

    todos = load_todos("pm-demo", str(tmp_path))
    assert "Added 'call John'" in reply
    assert todos.items[0].title == "call John"


def test_approval_endpoints_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    cfg = _cfg(tmp_path)
    save_schedule(
        ScheduleData(entries=[ScheduleEntry(id="dentist1", title="Dentist appointment")]),
        "pm-demo",
        str(tmp_path),
    )
    run_pm("Delete my dentist appointment", cfg)
    approval = list_approval_requests("pm-demo", str(tmp_path), status="pending")[0]
    client = TestClient(app)

    listed = client.get(
        "/personal-manager/approvals",
        params={"sessionId": "pm-demo"},
    )
    approved = client.post(
        f"/personal-manager/approvals/{approval.id}/approve",
        params={"sessionId": "pm-demo"},
    )

    assert listed.status_code == 200
    assert listed.json()["approvals"][0]["id"] == approval.id
    assert approved.status_code == 200
    assert approved.json()["ok"] is True
    assert load_schedule("pm-demo", str(tmp_path)).entries == []


def test_eval_fixture_is_well_formed():
    with open("tests/fixtures/pm_eval_cases.json", encoding="utf-8") as f:
        cases = json.load(f)

    assert cases
    assert all("expected_intent" in case for case in cases)
    assert all("expected_entities" in case for case in cases)
    assert all("confidence_min" in case for case in cases)
    assert all("confidence_max" in case for case in cases)
    assert all("expected_action_type" in case for case in cases)
