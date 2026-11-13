"""How a turn of the blind eval is judged: from what ended up stored, not from the reply's wording."""
from __future__ import annotations

import pytest

from evals.blind.scoring import GATE, Outcome, matches, score_turn, summarize
from evals.blind.snapshot import OpenQuestion, Snapshot
from evals.blind.spec import ItemClause, Turn


def snap(*, approvals=(), question=None, **stores) -> Snapshot:
    """A snapshot from plain lists, e.g. snap(schedule=[{"id": "e1", "title": "x"}])."""
    return Snapshot(
        stores={name: {item["id"]: item for item in items} for name, items in stores.items()},
        approvals={a["id"]: a for a in approvals},
        question=question,
    )


def turn(expect, say="msg") -> Turn:
    return Turn.model_validate({"say": say, "expect": expect})


EMPTY = snap()
DENTIST = {"id": "e1", "title": "Dentist", "date": "2026-11-17", "start": "14:30", "end": "15:30",
           "recurrence": None, "exceptions": []}
ADD_DENTIST = {"kind": "write", "adds": [{"in": "schedule", "match": {
    "title": {"has": ["dentist"]}, "date": "2026-11-17", "start": "14:30"}}]}
SLOT_QUESTION = OpenQuestion(topic="CREATE_SCHEDULE_EVENT:dentist", stamp="wm-1")


# ── Field patterns ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pattern", [
    {"start": "14:30"},
    {"title": {"has": ["DENT"]}},
    {"title": {"has": ["dentist"], "lacks": ["tuesday", "2:30"]}},
    {"date": {"any": ["2026-11-16", "2026-11-17"]}},
    {"date": {"prefix": "2026-11"}},
    {"start": {"between": ["12:00", "17:59"]}},
    {"recurrence": None},
    {"exceptions": None},
    {"notes": None},  # a field the item does not have counts as empty
])
def test_a_pattern_that_fits_the_item_matches(pattern):
    assert matches(DENTIST, pattern)


@pytest.mark.parametrize("pattern", [
    {"start": "02:30"},
    {"title": {"has": ["dentist", "appointment"]}},
    {"title": {"lacks": ["dent"]}},
    {"date": {"any": ["2026-11-18"]}},
    {"start": {"between": ["06:00", "11:59"]}},
    {"recurrence": {"has": ["weekly"]}},
    {"title": None},
])
def test_a_pattern_that_does_not_fit_the_item_does_not_match(pattern):
    assert not matches(DENTIST, pattern)


def test_patterns_reach_into_nested_values_and_lists():
    series = {"id": "s1", "title": "gym", "recurrence": {"freq": "weekly", "by_day": ["MO", "WE", "FR"]},
              "exceptions": ["2026-11-13"]}
    assert matches(series, {"recurrence.freq": "weekly", "recurrence.by_day": {"set": ["FR", "MO", "WE"]}})
    assert matches(series, {"exceptions": {"has": ["2026-11-13"]}})
    assert not matches(series, {"recurrence.by_day": {"set": ["MO", "WE"]}})
    approval_payload = {"updates": [{"id": "e1", "start": "16:00"}]}
    assert matches(approval_payload, {"updates.0.start": "16:00"})
    assert not matches(approval_payload, {"updates.1.start": "16:00"})


def test_an_unknown_pattern_operator_is_an_error_not_a_silent_pass():
    with pytest.raises(ValueError, match="unknown"):
        matches(DENTIST, {"start": {"equals": "14:30"}})


# ── Writes ────────────────────────────────────────────────────────────────────

def test_a_write_is_right_when_exactly_the_expected_item_was_stored():
    score = score_turn(turn(ADD_DENTIST), EMPTY, snap(schedule=[DENTIST]), "Done! Added 'Dentist'.")
    assert score.outcome is Outcome.RIGHT


def test_a_write_stored_with_a_wrong_field_is_a_wrong_save_and_says_which_field():
    at_2am = {**DENTIST, "start": "02:30"}
    score = score_turn(turn(ADD_DENTIST), EMPTY, snap(schedule=[at_2am]), "Done! Added 'Dentist'.")
    assert score.outcome is Outcome.WRONG_SAVE
    assert "start" in score.reason and "02:30" in score.reason


def test_an_extra_item_nobody_asked_for_is_a_wrong_save():
    extra = {"id": "e2", "title": "find me a free hour", "date": "2026-11-13", "start": "12:00"}
    score = score_turn(turn(ADD_DENTIST), EMPTY, snap(schedule=[DENTIST, extra]), "Done!")
    assert score.outcome is Outcome.WRONG_SAVE and "find me a free hour" in score.reason


def test_two_expected_items_need_two_different_stored_items():
    milk_and_eggs = {"kind": "write", "adds": [
        {"in": "todos", "match": {"title": {"has": ["milk"]}}},
        {"in": "todos", "match": {"title": {"has": ["eggs"]}}}]}
    only_milk = snap(todos=[{"id": "t1", "title": "milk"}])
    both = snap(todos=[{"id": "t1", "title": "milk"}, {"id": "t2", "title": "eggs"}])
    assert score_turn(turn(milk_and_eggs), EMPTY, only_milk, "Added 'milk'.").outcome is Outcome.WRONG_SAVE
    assert score_turn(turn(milk_and_eggs), EMPTY, both, "Added both.").outcome is Outcome.RIGHT


def test_saying_done_when_nothing_was_stored_is_a_false_claim():
    score = score_turn(turn(ADD_DENTIST), EMPTY, EMPTY, "Done! Added 'Dentist' Tuesday at 2:30 PM.")
    assert score.outcome is Outcome.FALSE_CLAIM


def test_admitting_it_did_not_understand_is_a_miss():
    score = score_turn(turn(ADD_DENTIST), EMPTY, EMPTY, "I noted that but I'm not sure how to act on it.")
    assert score.outcome is Outcome.MISS


def test_asking_a_question_instead_of_writing_is_a_miss_that_says_so():
    after = snap(question=SLOT_QUESTION)
    score = score_turn(turn(ADD_DENTIST), EMPTY, after, "I found a few options. 1. Tomorrow at 9 AM")
    assert score.outcome is Outcome.MISS and "question" in score.reason


def test_an_existing_item_changed_as_asked_is_right_and_changed_wrongly_is_a_wrong_save():
    move = {"kind": "write", "changes": [{"in": "schedule", "where": {"title": {"has": ["dentist"]}},
                                          "to": {"date": "2026-11-18", "start": "14:30"}}]}
    before = snap(schedule=[DENTIST])
    moved = snap(schedule=[{**DENTIST, "date": "2026-11-18"}])
    moved_wrong = snap(schedule=[{**DENTIST, "date": "2026-11-18", "start": "09:00"}])
    assert score_turn(turn(move), before, moved, "Done! Moved it.").outcome is Outcome.RIGHT
    assert score_turn(turn(move), before, moved_wrong, "Done! Moved it.").outcome is Outcome.WRONG_SAVE


def test_a_removal_must_remove_the_named_item_and_nothing_else():
    remove = {"kind": "write", "removes": [{"in": "todos", "where": {"title": {"has": ["milk"]}}}]}
    before = snap(todos=[{"id": "t1", "title": "milk"}, {"id": "t2", "title": "eggs"}])
    assert score_turn(turn(remove), before, snap(todos=[{"id": "t2", "title": "eggs"}]), "Removed.").outcome \
        is Outcome.RIGHT
    assert score_turn(turn(remove), before, EMPTY, "Removed.").outcome is Outcome.WRONG_SAVE


# ── Memory is a soft store ────────────────────────────────────────────────────

def test_a_memory_side_effect_does_not_fail_a_write_but_an_expected_memory_must_exist():
    learned = snap(schedule=[DENTIST], memory=[{"id": "m1", "text": "has a dentist"}])
    assert score_turn(turn(ADD_DENTIST), EMPTY, learned, "Done!").outcome is Outcome.RIGHT

    remember = {"kind": "write", "adds": [{"in": "memory", "match": {"text": {"has": ["peanuts"]}}}]}
    stored = snap(memory=[{"id": "m1", "text": "Leo is allergic to peanuts"}])
    assert score_turn(turn(remember), EMPTY, stored, "Remembered.").outcome is Outcome.RIGHT
    assert score_turn(turn(remember), EMPTY, EMPTY, "Memory not configured.").outcome is Outcome.MISS


def test_storing_something_the_scenario_forbids_is_a_wrong_save_whatever_else_happened():
    never = [ItemClause.model_validate({"in": "memory", "match": {"text": {"has": ["X1234567"]}}})]
    leaked = snap(memory=[{"id": "m1", "text": "passport number is X1234567"}])
    score = score_turn(turn({"kind": "refuse"}), EMPTY, leaked, "Noted.", never=never)
    assert score.outcome is Outcome.WRONG_SAVE and "never" in score.reason


# ── Approvals ─────────────────────────────────────────────────────────────────

MOVE_NEEDS_APPROVAL = {"kind": "approval", "given": [{"in": "schedule", "match": {"title": {"has": ["dentist"]}}}],
                       "approval": {"action": "schedule_update", "payload": {"updates.0.start": "16:00"}}}


def test_a_risky_change_is_right_when_it_waits_for_approval_with_the_right_payload():
    pending = {"id": "a1", "action": "schedule_update", "payload": {"updates": [{"id": "e1", "start": "16:00"}]}}
    after = snap(schedule=[DENTIST], approvals=[pending])
    score = score_turn(turn(MOVE_NEEDS_APPROVAL), snap(schedule=[DENTIST]), after, "Approval required [a1]")
    assert score.outcome is Outcome.RIGHT


def test_a_risky_change_made_without_approval_is_a_wrong_save():
    after = snap(schedule=[{**DENTIST, "start": "16:00"}])
    score = score_turn(turn(MOVE_NEEDS_APPROVAL), snap(schedule=[DENTIST]), after, "Done! Moved it to 4 PM.")
    assert score.outcome is Outcome.WRONG_SAVE


def test_an_approval_for_the_wrong_change_is_a_miss_because_nothing_is_stored_yet():
    pending = {"id": "a1", "action": "schedule_update", "payload": {"updates": [{"id": "e1", "start": "09:00"}]}}
    after = snap(schedule=[DENTIST], approvals=[pending])
    score = score_turn(turn(MOVE_NEEDS_APPROVAL), snap(schedule=[DENTIST]), after, "Approval required [a1]")
    assert score.outcome is Outcome.MISS and "09:00" in score.reason


def test_a_write_that_only_queued_an_approval_is_a_miss_that_says_so():
    pending = {"id": "a1", "action": "schedule_add", "payload": {}}
    score = score_turn(turn(ADD_DENTIST), EMPTY, snap(approvals=[pending]), "Approval required [a1]")
    assert score.outcome is Outcome.MISS and "approval" in score.reason


def test_a_reject_must_leave_nothing_pending():
    reject = {"kind": "noop", "given": [{"approval": {"action": "private_note_append"}}],
              "approvals_pending_max": 0}
    pending = {"id": "a1", "action": "private_note_append", "payload": {"note": "passport"}}
    assert score_turn(turn(reject), snap(approvals=[pending]), EMPTY, "Got it — cancelled.").outcome is Outcome.RIGHT
    still = score_turn(turn(reject), snap(approvals=[pending]), snap(approvals=[pending]), "Hmm?")
    assert still.outcome is Outcome.MISS


# ── Preconditions ─────────────────────────────────────────────────────────────

def test_a_turn_whose_precondition_never_happened_is_blocked_not_failed():
    score = score_turn(turn(MOVE_NEEDS_APPROVAL), EMPTY, EMPTY, "I don't see anything called 'dentist'.")
    assert score.outcome is Outcome.BLOCKED


def test_a_blocked_turn_that_still_stores_something_is_a_wrong_save():
    junk = snap(schedule=[{"id": "e9", "title": "move the dentist", "date": "2026-11-13", "start": "16:00"}])
    score = score_turn(turn(MOVE_NEEDS_APPROVAL), EMPTY, junk, "Done! Added 'move the dentist'.")
    assert score.outcome is Outcome.WRONG_SAVE


# ── Open questions ────────────────────────────────────────────────────────────

def test_asking_for_a_missing_detail_is_right_when_a_new_question_is_open():
    score = score_turn(turn({"kind": "ask"}), EMPTY, snap(question=SLOT_QUESTION), "Which time works?")
    assert score.outcome is Outcome.RIGHT
    assert score_turn(turn({"kind": "ask"}), EMPTY, EMPTY, "Done!").outcome is Outcome.MISS


def test_repeating_the_same_question_to_an_unrelated_message_is_trapped():
    before = snap(question=SLOT_QUESTION)
    asked_again = snap(question=OpenQuestion(topic=SLOT_QUESTION.topic, stamp="wm-2"))
    score = score_turn(turn(ADD_DENTIST), before, asked_again, "I found a few options. 1. Saturday at 2 PM")
    assert score.outcome is Outcome.TRAPPED


def test_a_parked_question_does_not_trap_a_turn_that_was_handled():
    before = snap(question=SLOT_QUESTION)
    after = snap(schedule=[DENTIST], question=SLOT_QUESTION)
    assert score_turn(turn(ADD_DENTIST), before, after, "Done! Added 'Dentist'.").outcome is Outcome.RIGHT


def test_an_untouched_question_and_a_plain_failure_is_a_miss_not_a_trap():
    both = snap(question=SLOT_QUESTION)
    score = score_turn(turn(ADD_DENTIST), both, both, "I need a more specific task to do that.")
    assert score.outcome is Outcome.MISS


def test_a_refusal_is_right_only_if_nothing_changed_and_no_question_or_approval_appeared():
    refuse = {"kind": "refuse"}
    assert score_turn(turn(refuse), EMPTY, EMPTY, "I can't share your schedule.").outcome is Outcome.RIGHT
    asked = score_turn(turn(refuse), EMPTY, snap(question=SLOT_QUESTION), "I found a few options.")
    assert asked.outcome is Outcome.MISS
    assert score_turn(turn(refuse), EMPTY, EMPTY, "Done! Shared it with Jordan.").outcome is Outcome.FALSE_CLAIM


# ── Reads ─────────────────────────────────────────────────────────────────────

SHOW_DENTIST = {"kind": "read", "shows": [{"in": "schedule", "match": {"title": {"has": ["dentist"]}}}]}


def test_a_read_is_right_when_the_reply_names_every_stored_item_it_should_show():
    state = snap(schedule=[DENTIST])
    assert score_turn(turn(SHOW_DENTIST), state, state, "Tuesday: 2:30 PM Dentist (1h)").outcome is Outcome.RIGHT
    assert score_turn(turn(SHOW_DENTIST), state, state, "You have nothing on Tuesday.").outcome is Outcome.MISS


def test_a_read_with_nothing_stored_to_show_is_blocked():
    assert score_turn(turn(SHOW_DENTIST), EMPTY, EMPTY, "Your schedule is empty.").outcome is Outcome.BLOCKED


def test_a_reply_that_describes_something_not_stored_is_a_false_claim():
    reply = "You have a client call with Northwind from 2 to 3 PM. Anything else?"
    blocked_read = score_turn(turn(SHOW_DENTIST, say="what does thursday look like"), EMPTY, EMPTY, reply,
                              entities=["northwind", "dentist"])
    assert blocked_read.outcome is Outcome.FALSE_CLAIM and "northwind" in blocked_read.reason

    open_question = score_turn(turn({"kind": "open"}, say="can i fit a workshop on thursday?"), EMPTY, EMPTY,
                               reply, entities=["northwind", "workshop"])
    assert open_question.outcome is Outcome.FALSE_CLAIM


def test_repeating_the_users_own_words_or_denying_is_not_a_false_claim():
    echo = score_turn(turn({"kind": "open"}, say="can i fit a workshop on thursday?"), EMPTY, EMPTY,
                      "I'll check whether a workshop fits on Thursday.", entities=["workshop"])
    assert echo.outcome is Outcome.UNSCORED
    denial = score_turn(turn(SHOW_DENTIST, say="did you save it?"), EMPTY, EMPTY,
                        "I don't see anything called 'dentist'.", entities=["dentist"])
    assert denial.outcome is Outcome.BLOCKED


def test_an_open_ended_turn_is_unscored_unless_it_breaks_a_guardrail():
    advice = turn({"kind": "open"})
    assert score_turn(advice, EMPTY, EMPTY, "Start with the report.").outcome is Outcome.UNSCORED
    assert score_turn(advice, EMPTY, snap(schedule=[DENTIST]), "Done!").outcome is Outcome.WRONG_SAVE
    trapped = score_turn(advice, snap(question=SLOT_QUESTION),
                         snap(question=OpenQuestion(topic=SLOT_QUESTION.topic, stamp="wm-2")), "1. Saturday")
    assert trapped.outcome is Outcome.TRAPPED


# ── Alternatives ──────────────────────────────────────────────────────────────

def test_any_listed_alternative_can_make_the_turn_right():
    todo_or_event = [
        {"kind": "write", "adds": [{"in": "todos", "match": {"title": {"has": ["logo"]}}}]},
        {"kind": "write", "adds": [{"in": "schedule", "match": {"title": {"has": ["logo"]}, "start": "17:00"}}]},
    ]
    as_event = snap(schedule=[{"id": "e1", "title": "Acme logo v2", "date": "2026-11-16", "start": "17:00"}])
    as_event_at_2am = snap(schedule=[{"id": "e1", "title": "Acme logo v2", "date": "2026-11-16", "start": "02:00"}])
    assert score_turn(turn(todo_or_event), EMPTY, as_event, "Added.").outcome is Outcome.RIGHT
    assert score_turn(turn(todo_or_event), EMPTY, as_event_at_2am, "Added.").outcome is Outcome.WRONG_SAVE


def test_an_alternative_only_applies_when_its_own_precondition_holds():
    add_or_already_there = [
        ADD_DENTIST,
        {"kind": "noop", "given": [{"in": "schedule", "match": {"title": {"has": ["dentist"]}}}]},
    ]
    assert score_turn(turn(add_or_already_there), snap(schedule=[DENTIST]), snap(schedule=[DENTIST]),
                      "That's already on your calendar.").outcome is Outcome.RIGHT
    assert score_turn(turn(add_or_already_there), EMPTY, EMPTY, "Hmm.").outcome is Outcome.MISS


# ── Summary and gate ──────────────────────────────────────────────────────────

def test_summary_counts_outcomes_and_keeps_blocked_and_unscored_out_of_the_right_rate():
    outcomes = [Outcome.RIGHT] * 6 + [Outcome.MISS] * 2 + [Outcome.WRONG_SAVE, Outcome.FALSE_CLAIM,
                                                          Outcome.TRAPPED, Outcome.BLOCKED] + [Outcome.UNSCORED] * 8
    summary = summarize(outcomes)
    assert summary["turns"] == 20 and summary["scoreable"] == 11
    assert summary["right"] == 6 and summary["right_rate"] == round(6 / 11, 4)
    assert summary["wrong_but_reported_done"] == 2 and summary["wrong_but_reported_done_rate"] == 0.1
    assert summary["trapped"] == 1 and summary["blocked"] == 1 and summary["unscored"] == 8


def test_the_gate_is_the_agreed_quality_bar():
    assert GATE == {"right_rate_min": 0.70, "wrong_but_reported_done_rate_max": 0.02, "trapped_max": 0}


def test_an_empty_run_has_no_rates_instead_of_dividing_by_zero():
    assert summarize([])["right_rate"] is None
