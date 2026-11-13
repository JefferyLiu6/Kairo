"""The blind eval set file: it loads, it is complete, and malformed expectations are rejected."""
from __future__ import annotations

from collections import Counter

import pytest
from pydantic import ValidationError

from evals.blind.spec import Expect, Turn, load_blind_set


def test_the_set_is_the_ten_trial_personas_plus_four_written_later_as_holdouts():
    blind = load_blind_set()
    assert len(blind.scenarios) == 14 and len({s.id for s in blind.scenarios}) == 14
    assert sum(len(s.turns) for s in blind.scenarios) == 127
    assert sum(len(s.turns) for s in blind.scenarios if s.split == "holdout") == 42


def test_the_clock_is_pinned_to_the_thursday_the_messages_were_written_on():
    blind = load_blind_set()
    assert blind.now.isoformat() == "2026-11-12T10:00:00-05:00"
    assert blind.now.strftime("%A") == "Thursday" and blind.timezone == "America/Toronto"


def test_some_personas_are_held_out_so_tuning_cannot_see_them():
    blind = load_blind_set()
    splits = Counter(s.split for s in blind.scenarios)
    assert splits["dev"] == 10 and splits["holdout"] == 4
    # The first three holdouts were seen too often while the graph was built, and became dev.
    assert {s.id for s in blind.scenarios if s.split == "holdout"} == {
        "11_music_teacher", "12_caregiver", "13_oncall_developer", "14_office_manager"}


def test_every_scenario_names_the_things_a_reply_could_invent():
    for scenario in load_blind_set().scenarios:
        assert scenario.entities, scenario.id
        assert all(entity == entity.casefold() for entity in scenario.entities), scenario.id


def test_most_turns_are_scoreable_so_the_right_rate_means_something():
    kinds = Counter(turn.alternatives()[0].kind for s in load_blind_set().scenarios for turn in s.turns)
    assert kinds["open"] <= 12, kinds
    assert kinds["write"] >= 35 and kinds["read"] >= 12 and kinds["approval"] >= 8, kinds


@pytest.mark.parametrize("expect", [
    {"kind": "write"},                                                        # nothing to write
    {"kind": "approval"},                                                     # no approval described
    {"kind": "read"},                                                         # nothing to show
    {"kind": "open", "adds": [{"in": "todos", "match": {"title": "x"}}]},     # open turns expect no change
    {"kind": "ask", "removes": [{"in": "todos", "where": {}}]},
    {"kind": "write", "adds": [{"in": "calendar", "match": {}}]},             # unknown store
    {"kind": "write", "adds": [{"in": "todos", "match": {}}], "typo": 1},     # unknown key
    {"kind": "noop", "given": [{}]},                                          # empty precondition
    {"kind": "noop", "given": [{"in": "todos"}]},                             # store without a pattern
])
def test_a_malformed_expectation_is_rejected_when_the_set_loads(expect):
    with pytest.raises(ValidationError):
        Expect.model_validate(expect)


def test_a_turn_accepts_one_expectation_or_a_list_of_alternatives():
    one = Turn.model_validate({"say": "hi", "expect": {"kind": "open"}})
    two = Turn.model_validate({"say": "hi", "expect": [{"kind": "ask"}, {"kind": "refuse"}]})
    assert [e.kind for e in one.alternatives()] == ["open"]
    assert [e.kind for e in two.alternatives()] == ["ask", "refuse"]
    with pytest.raises(ValidationError):
        Turn.model_validate({"say": "hi", "expect": []})


def test_one_turn_may_write_one_thing_and_ask_approval_for_another():
    """A message with several parts: the safe part is done and the risky one waits."""
    expect = Expect.model_validate({"kind": "approval", "approval": {"action": "schedule_update"},
                                    "adds": [{"in": "todos", "match": {"title": "x"}}]})
    assert expect.adds and expect.approval
