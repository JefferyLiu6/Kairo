"""How a reply to a pending question is resolved — one owner, one test per rule (Phase 4c/4e)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from kairo.agents.personal_manager.agent import PMConfig, run_pm
from kairo.agents.personal_manager.application import clarification as c
from kairo.agents.personal_manager.application.pending_reply import PENDING_RULES, OpenQuestion, PendingTurn, load_open_question, pm_asked_a_question, resolve_pending_reply, route_message, still_open_after_error, still_open_reply
from kairo.agents.personal_manager.application.todo_selection import prepare_todo_target
from kairo.core.domain.pending import ConfirmationPending, FieldChoicesPending, FreeformPending, TodoListPending
from kairo.core.domain.types import PMIntent, PMPlanExtraction, PMTaskExtraction
from kairo.storage.decision_log import TurnDecision
from kairo.storage.store import TodoData, TodoItem, save_todos
from kairo.storage.working_memory import list_working_memory, update_working_memory_timestamps

THREAD = "pm-rules"
PLAN = PMPlanExtraction(tasks=[PMTaskExtraction(
    task_id="task-1", intent=PMIntent.CREATE_SCHEDULE_EVENT,
    entities={"title": "golf", "date": "2026-11-04"}, missing_fields=["start"],
)])
CHOICES = [{"id": "1", "label": "Tomorrow at 9 AM", "values": {"start": "09:00"}},
           {"id": "2", "label": "Tomorrow at 2 PM", "values": {"start": "14:00"}}]
NOTHING = PMPlanExtraction(tasks=[PMTaskExtraction(task_id="task-1", intent=PMIntent.UNKNOWN, confidence=0.2)],
                           confidence=0.2)


def _cfg(tmp_path):
    return PMConfig(provider="openai", model="unused", data_dir=str(tmp_path), session_id=THREAD)


def _turn(tmp_path, message, extract=lambda *_: NOTHING):
    d = TurnDecision(session_id=THREAD, message_preview=message)
    return PendingTurn.load(message, _cfg(tmp_path), THREAD, THREAD, d, extract=extract)


def _choices(tmp_path):
    c._save_pending_field_choices(THREAD, str(tmp_path), PLAN, blocking_task_id="task-1",
                                  missing=["start"], choices=CHOICES)


def _confirmation(tmp_path):
    c._save_pending_confirmation(THREAD, str(tmp_path), PLAN, blocking_task_id="task-1",
                                 confirmation_summary="Book golf tomorrow at 9?", original_message="golf tmr",
                                 provenance={"source": "test"})


def _last_status(tmp_path):
    return list_working_memory(THREAD, str(tmp_path))[0].status


def test_pending_rules_run_in_policy_order():
    # The order is the policy. In particular a high-confidence new request must be
    # recognised before the confirmation editor reads it as a correction, and an
    # answer to the question is the last thing tried. Reordering = update this list.
    assert [rule.__name__ for rule in PENDING_RULES] == [
        "_new_time_slot_request_replaces_choices",
        "_cancel",
        "_todo_selection",
        "_disambiguation",
        "_new_request_or_stale",
        "_confirmation",
        "_something_else",
        "_more_options",
        "_answer_to_question",
    ]


def test_nothing_pending_runs_no_rules(tmp_path):
    turn = _turn(tmp_path, "add milk", extract=lambda *_: pytest.fail("no question, no guard"))
    assert resolve_pending_reply(turn) is None
    assert turn.d.wm_outcome == "none" and not turn.was_active


# ── One test per rule ─────────────────────────────────────────────────────────

def test_time_slot_request_replaces_open_choices(tmp_path):
    _choices(tmp_path)
    turn = _turn(tmp_path, "what should I do at 8pm tdy?")

    assert resolve_pending_reply(turn) is None
    assert turn.state is None and not turn.was_active
    assert turn.d.wm_outcome == "replaced_new_request" and _last_status(tmp_path) == "replaced"


@pytest.mark.parametrize("message", ["never mind", "cancel", "nope", "Never mind, cancel that"])
def test_cancel_ends_the_question(tmp_path, message):
    _choices(tmp_path)
    turn = _turn(tmp_path, message)

    assert resolve_pending_reply(turn) == "Got it — cancelled that pending request."
    assert turn.d.execution_mode == "cancel" and _last_status(tmp_path) == "cancelled"


def test_todo_selection_picks_by_number_or_asks_again(tmp_path):
    save_todos(TodoData(items=[TodoItem(id="t1", title="call mom"), TodoItem(id="t2", title="call bank")]),
               THREAD, str(tmp_path))
    plan = PMPlanExtraction(tasks=[PMTaskExtraction(task_id="task-1", intent=PMIntent.COMPLETE_TODO,
                                                    entities={"query": "call"}, confidence=0.9)])
    config = SimpleNamespace(data_dir=str(tmp_path))
    assert prepare_todo_target(plan, config, THREAD, THREAD)  # ambiguous: asks which one

    vague = _turn(tmp_path, "the big one")
    assert resolve_pending_reply(vague).startswith("Which one?")
    assert vague.d.wm_outcome == "kept"

    picked = _turn(tmp_path, "2")
    assert resolve_pending_reply(picked) is None
    assert picked.plan.tasks[0].entities["id"] == "t2" and _last_status(tmp_path) == "resolved"


def test_disambiguation_turns_a_named_activity_into_a_confirmation(tmp_path):
    c._save_pending_disambiguation(THREAD, str(tmp_path), candidates=[
        {"activity": "tennis", "label": "Tennis"}, {"activity": "golf", "label": "Golf"}],
        parsed_date="2026-11-04", parsed_start="09:00", reply="Tennis or golf?", original_message="play tmr 9am")
    turn = _turn(tmp_path, "golf")

    reply = resolve_pending_reply(turn)

    assert "golf" in reply.lower()
    assert isinstance(c.load_pending_state(THREAD, str(tmp_path)), ConfirmationPending)
    assert turn.d.wm_outcome == "resolved" and turn.d.wm_after == "active:awaiting_confirmation"


def test_a_confident_new_request_replaces_the_question(tmp_path):
    _confirmation(tmp_path)
    new = PMPlanExtraction(tasks=[PMTaskExtraction(task_id="task-1", intent=PMIntent.CREATE_SCHEDULE_EVENT,
                                                   entities={"title": "dentist"}, confidence=0.95)],
                           confidence=0.95)
    turn = _turn(tmp_path, "Schedule dentist appointment tomorrow at 9am", extract=lambda *_: new)

    assert resolve_pending_reply(turn) is None
    assert turn.plan is new and turn.d.wm_outcome == "replaced_new_request"
    assert _last_status(tmp_path) == "replaced"  # never reached the confirmation editor


def test_a_stale_question_is_dropped_for_an_unrelated_message(tmp_path):
    _choices(tmp_path)
    record = list_working_memory(THREAD, str(tmp_path))[0]
    update_working_memory_timestamps(THREAD, str(tmp_path), record.id, last_updated_at="2020-01-01T00:00:00+00:00")
    # Under 80 characters, almost anything counts as a reply to the question; this is not.
    turn = _turn(tmp_path, "honestly I have been wondering what you think about how my week has been going overall")

    assert resolve_pending_reply(turn) is None
    assert turn.state is None and turn.d.wm_outcome == "replaced_stale"


def test_yes_confirms_and_hands_the_plan_on(tmp_path):
    _confirmation(tmp_path)
    turn = _turn(tmp_path, "yes")

    assert resolve_pending_reply(turn) is None
    assert turn.plan is not None and turn.d.wm_outcome == "resolved"


def test_a_correction_refreshes_the_confirmation(tmp_path):
    _confirmation(tmp_path)
    turn = _turn(tmp_path, "make it 3pm")

    reply = resolve_pending_reply(turn)

    assert reply and turn.d.execution_mode == "confirmation" and turn.d.wm_outcome == "kept"


def test_something_else_replays_time_slot_options(tmp_path):
    run_pm("what should I do at 8pm tdy?", _cfg(tmp_path))
    first = c.load_pending_state(THREAD, str(tmp_path))
    turn = _turn(tmp_path, "something else")

    reply = resolve_pending_reply(turn)

    assert reply and turn.d.execution_mode == "time_slot_replay" and turn.d.wm_outcome == "replay"
    assert isinstance(first, FieldChoicesPending)
    assert isinstance(c.load_pending_state(THREAD, str(tmp_path)), FieldChoicesPending)


def test_more_options_ends_the_question_with_a_hint(tmp_path):
    _choices(tmp_path)
    turn = _turn(tmp_path, "more options")

    assert "fresh set of options" in resolve_pending_reply(turn)
    assert turn.d.wm_outcome == "more_options" and _last_status(tmp_path) == "replaced"


def test_a_number_answers_open_choices(tmp_path):
    _choices(tmp_path)
    turn = _turn(tmp_path, "2")

    assert resolve_pending_reply(turn) is None
    assert turn.plan.tasks[0].entities["start"] == "14:00" and turn.d.wm_outcome == "resolved"


def test_a_freeform_answer_rebuilds_the_command(tmp_path):
    c._save_pending(THREAD, str(tmp_path), PMIntent.CREATE_SCHEDULE_EVENT, {"title": "dentist"}, ["start"])
    turn = _turn(tmp_path, "tomorrow at 3pm")

    assert resolve_pending_reply(turn) is None
    assert turn.d.wm_outcome == "resolved"
    assert turn.plan is not None or "dentist" in turn.effective_message


def test_an_unrelated_reply_keeps_the_question(tmp_path):
    _confirmation(tmp_path)
    turn = _turn(tmp_path, "hmm let me think")

    assert resolve_pending_reply(turn) is None
    assert turn.d.wm_outcome == "kept"


# ── The orchestrator's decisions ──────────────────────────────────────────────

_Q = OpenQuestion(id="wm-1", state=FreeformPending(intent=PMIntent.CREATE_TODO, missing=["title"]))


@pytest.mark.parametrize("message, question, route", [
    ("approve", None, "approval_command"),
    ("approve", _Q, "answer_question"),            # "approve" the suggestion, not the queue
    ("reject 1a2b3c4d", _Q, "approval_command"),   # an id always means the queue
    ("2", _Q, "answer_question"),
    ("add milk", None, "new_request"),
])
def test_route_message(message, question, route):
    assert route_message(message, question) == route


def test_load_open_question_ignores_a_shown_task_list(tmp_path):
    c._store_pending(THREAD, str(tmp_path), TodoListPending(candidates=[{"id": "t1", "title": "x"}]),
                     source="test", summary="shown")
    assert load_open_question(THREAD, str(tmp_path), user_id=THREAD) is None
    _choices(tmp_path)
    assert isinstance(load_open_question(THREAD, str(tmp_path), user_id=THREAD).state, FieldChoicesPending)


def test_pm_asked_a_question_only_after_a_successful_reply():
    assert pm_asked_a_question("Which time works?", lambda: _Q)
    assert not pm_asked_a_question("Added 'milk'.", lambda: None)
    assert not pm_asked_a_question("Error: boom", lambda: pytest.fail("errors are not questions"))


# Regression: edd8784 "pending mission error" — a PM failure must keep the options open.
def test_still_open_after_error_needs_the_same_untouched_question():
    same, other = _Q, OpenQuestion(id="wm-2", state=_Q.state)
    assert still_open_after_error(_Q, "Error: list index out of range", lambda: same) is same
    assert still_open_after_error(_Q, "Error: failed mid-write", lambda: other) is None  # it changed
    assert still_open_after_error(_Q, "Error: x", lambda: None) is None                 # it was cleared
    assert still_open_after_error(None, "Error: x", lambda: pytest.fail("no question before")) is None
    assert still_open_after_error(_Q, "Added 'milk'.", lambda: pytest.fail("not an error")) is None


def test_still_open_reply_relists_the_choices(tmp_path):
    _choices(tmp_path)
    reply = still_open_reply(load_open_question(THREAD, str(tmp_path), user_id=THREAD))
    assert "nothing was saved" in reply and "1. Tomorrow at 9 AM" in reply and "2. Tomorrow at 2 PM" in reply
    assert "answer my last question again" in still_open_reply(_Q)
