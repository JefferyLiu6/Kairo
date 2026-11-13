"""Checks that compare a proposal with the message's own wording. A message they cannot read is not checked."""
from __future__ import annotations

import pytest

from kairo.domains.checks import range_the_wording_gives, repeat_problem, times_the_wording_gives


@pytest.mark.parametrize("say, times", [
    ("client call with Northwind thurs 2-3pm", {"14:00", "15:00"}),
    ("workshop 11-1pm on friday", {"11:00", "13:00"}),
    ("standup 9:30 to 10am", {"09:30", "10:00"}),
    ("lunch 12-1pm", {"12:00", "13:00"}),
    ("add shift tomorrow 7pm to 7:30am", {"19:00", "07:30"}),
    ("move the discovery call to 4pm", {"16:00"}),
    ("dinner at 7", set()),                       # a bare hour does not say which half of the day
    ("night shift 1900-0730 fri sat sun", set()),  # not a form this reads
])
def test_clock_times_and_ranges_stated_in_a_message(say, times):
    assert times_the_wording_gives(say) == times


@pytest.mark.parametrize("say, stated", [
    ("client call with Northwind thurs 2-3pm", ("14:00", "15:00")),
    ("workshop 11-1pm on friday", ("11:00", "13:00")),
    ("add shift tomorrow 7pm to 7:30am", ("19:00", "07:30")),
    ("move the 2pm call to 3pm", None),            # two times, not a range
    ("meet 2026-11-13 2pm", None),
])
def test_a_time_range_stated_in_a_message(say, stated):
    assert range_the_wording_gives(say) == stated


def weekly(*days, interval=1):
    return {"freq": "weekly", "interval": interval, "by_day": list(days)}


DAILY = {"freq": "daily", "interval": 1, "by_day": []}


@pytest.mark.parametrize("say, recurrence", [
    ("Schedule a pipeline review with the team every Monday at 9am", weekly("MO")),
    ("Schedule gym every Monday, Wednesday and Friday at 6am", weekly("FR", "MO", "WE")),
    ("remind me to take my pill every morning at 8", DAILY),
    ("remind me to take my pill every morning at 8", weekly("MO", "TU", "WE", "TH", "FR", "SA", "SU")),
    ("standup every weekday at 9:15", weekly("MO", "TU", "WE", "TH", "FR")),
    ("bins out every other thursday at 7pm", weekly("TH", interval=2)),
    ("weekly review at 4pm on fridays", weekly("FR")),
    ("lunch with Sam on friday at noon", None),            # no repeat asked for: nothing to check
    ("lunch with Sam on friday at noon", weekly("FR")),    # only what the wording clearly says is checked
])
def test_a_repeat_that_agrees_with_the_message_passes(say, recurrence):
    assert repeat_problem(say, recurrence) is None


@pytest.mark.parametrize("say, recurrence, clue", [
    ("Schedule a pipeline review with the team every Monday at 9am", None, "repeat"),
    ("Schedule gym every Monday, Wednesday and Friday at 6am", weekly("MO", "WE"), "FR"),
    ("Add a weekly 1:1 with Sara every Wednesday at 10am", weekly("TH"), "WE"),
    ("remind me to take my pill every morning at 8", weekly("MO"), "every day"),
    ("standup every weekday at 9:15", DAILY, "MO"),
    ("bins out every other thursday at 7pm", weekly("TH"), "other"),
])
def test_a_repeat_that_contradicts_the_message_is_explained(say, recurrence, clue):
    assert clue in repeat_problem(say, recurrence)
