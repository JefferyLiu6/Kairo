"""Typed pending state (Phase 4b): validated on write, validated on read, one mode table."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

import assistant.orchestrator.agent as orch
from assistant.personal_manager.application import clarification as c
from assistant.personal_manager.application.pending_reply import still_open_reply
from assistant.personal_manager.application.todo_selection import remember_todo_list
from assistant.personal_manager.domain.pending import (
    PENDING_MODES,
    ConfirmationPending,
    DisambiguationPending,
    FieldChoicesPending,
    FreeformPending,
    PlanPending,
    TodoListPending,
    parse_pending,
)
from assistant.personal_manager.domain.types import PMIntent, PMPlanExtraction, PMTaskExtraction
from assistant.personal_manager.persistence.store import TodoData, TodoItem, save_todos
from assistant.personal_manager.persistence.working_memory import (
    list_working_memory,
    load_active_working_memory,
    save_working_memory,
)

THREAD = "pm-pending"
PLAN = PMPlanExtraction(tasks=[PMTaskExtraction(
    task_id="task-1", intent=PMIntent.CREATE_SCHEDULE_EVENT,
    entities={"title": "golf", "date": "tomorrow"}, missing_fields=["start"],
)])
CHOICES = [{"id": "1", "label": "Tomorrow at 9 AM", "values": {"start": "09:00"}, "score": 0.9},
           {"id": "2", "label": "Tomorrow at 2 PM", "values": {"start": "14:00"}}]


def _writers(d):
    return {
        FreeformPending: lambda: c._save_pending(THREAD, d, PMIntent.CREATE_SCHEDULE_EVENT, {"title": "golf"}, ["start"]),
        PlanPending: lambda: c._save_pending_plan(THREAD, d, PLAN, blocking_task_id="task-1", missing=["start"]),
        FieldChoicesPending: lambda: c._save_pending_field_choices(
            THREAD, d, PLAN, blocking_task_id="task-1", missing=["start"], choices=CHOICES),
        DisambiguationPending: lambda: c._save_pending_disambiguation(
            THREAD, d, candidates=[{"activity": "gym", "label": "Gym"}], parsed_date="2026-11-04",
            parsed_start="09:00", reply="Which one?", original_message="book it"),
        ConfirmationPending: lambda: c._save_pending_confirmation(
            THREAD, d, PLAN, blocking_task_id="task-1", confirmation_summary="Golf at 9?",
            original_message="golf tmr", provenance={"source": "pattern"}),
    }


@pytest.mark.parametrize("kind", [FreeformPending, PlanPending, FieldChoicesPending,
                                  DisambiguationPending, ConfirmationPending])
def test_every_kind_round_trips_with_its_mode_from_the_table(tmp_path, kind):
    _writers(str(tmp_path))[kind]()

    state = c.load_pending_state(THREAD, str(tmp_path))
    record = load_active_working_memory(THREAD, str(tmp_path))

    assert isinstance(state, kind)
    assert (record.mode, record.expected_reply) == PENDING_MODES[state.type]


def test_choices_keep_their_source_specific_fields(tmp_path):
    _writers(str(tmp_path))[FieldChoicesPending]()
    state = c.load_pending_state(THREAD, str(tmp_path))
    assert state.choices[0].label == "Tomorrow at 9 AM" and state.choices[0].score == 0.9


def test_an_incomplete_pending_state_cannot_be_saved(tmp_path):
    with pytest.raises(ValidationError):
        c._save_pending_field_choices(THREAD, str(tmp_path), PLAN, blocking_task_id="task-1",
                                      missing=["start"], choices=[])
    with pytest.raises(ValidationError):
        c._save_pending_field_choices(THREAD, str(tmp_path), PLAN, blocking_task_id="task-1",
                                      missing=["start"], choices=[{"id": "1"}])  # no label
    assert load_active_working_memory(THREAD, str(tmp_path)) is None


def test_rows_written_before_type_existed_still_load_as_freeform():
    assert isinstance(parse_pending({"intent": "CREATE_TODO", "entities": {}, "missing": ["title"]}),
                      FreeformPending)


def test_a_corrupt_row_ends_the_question_instead_of_crashing(tmp_path, caplog):
    save_working_memory(THREAD, str(tmp_path), mode="awaiting_choice", source="test",
                        expected_reply="choice_index_or_freeform",
                        payload={"type": "field_choices", "missing": ["start"]}, summary="broken")

    assert c._load_pending(THREAD, str(tmp_path)) is None
    assert "discarding invalid pending state" in caplog.text
    assert list_working_memory(THREAD, str(tmp_path))[0].status == "expired"


def _orch_config(tmp_path):
    return orch.OrchestratorConfig(session_id=THREAD, data_dir=str(tmp_path), provider="test",
                                   model="test", pm_provider="test", pm_model="test")


def test_orchestrator_sees_a_typed_question_and_lists_open_choices(tmp_path):
    _writers(str(tmp_path))[FieldChoicesPending]()

    question = orch._open_question(_orch_config(tmp_path))

    assert isinstance(question.state, FieldChoicesPending) and question.id
    reply = still_open_reply(question)
    assert "1. Tomorrow at 9 AM" in reply and "2. Tomorrow at 2 PM" in reply


def test_a_shown_task_list_is_not_a_question(tmp_path):
    save_todos(TodoData(items=[TodoItem(id="t1", title="call mom")]), THREAD, str(tmp_path))
    config = _orch_config(tmp_path)
    assert remember_todo_list(config, THREAD, THREAD, "## Todos\n- [ ] [t1] call mom")

    assert isinstance(c.load_pending_state(THREAD, str(tmp_path)), TodoListPending)
    assert orch._open_question(config) is None
