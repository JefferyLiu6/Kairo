"""The model date test: can a model turn date and time phrasing into correct typed tool arguments?"""
from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from evals.blind.spec import load_blind_set
from evals.datefill.probe import (
    BARS, Call, Case, decide, judge, load_cases, render, run_probe, save_report, summarize,
)
from evals.datefill.tools import (
    ARMS, calendar_hint, date_contradiction, dates_the_wording_allows, openai_tools, single_call, system_prompt,
    validate_call,
)

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=ZoneInfo("America/Toronto"))


def case(expect, say="dentist tomorrow at 3pm", category="relative_day") -> Case:
    return Case.model_validate({"id": "c1", "say": say, "category": category, "source": "new", "expect": expect})


def event(**args) -> Call:
    return Call("create_event", {"title": "Dentist", "date": "2026-11-13", "start": "15:00", "end": None,
                                 "recurrence": None, **args})


def todo(**args) -> Call:
    return Call("add_todo", {"title": "call mom", "due_date": None, "due_time": None, **args})


ASK = Call("ask_user", {"question": "What time?"})
DENTIST_AT_3 = [{"tool": "create_event", "args": {"date": "2026-11-13", "start": "15:00"}}]


# ── What the model is given ───────────────────────────────────────────────────

def test_the_calendar_hint_spells_out_the_next_two_weeks_with_weekdays():
    lines = calendar_hint(NOW).splitlines()
    assert len(lines) == 14
    assert lines[0] == "Thu 2026-11-12 (today)" and lines[1] == "Fri 2026-11-13 (tomorrow)"
    assert lines[5] == "Tue 2026-11-17" and lines[-1] == "Wed 2026-11-25"


def test_the_first_two_arms_differ_only_in_what_they_say_about_the_calendar():
    plain, with_calendar = system_prompt("date_only", NOW), system_prompt("calendar", NOW)
    assert ARMS == ("date_only", "calendar", "calendar_single")
    assert "2026-11-12" in plain and "Tue 2026-11-17" not in plain and "Thursday" not in plain
    assert "Tue 2026-11-17" in with_calendar and "Thursday" in with_calendar
    assert plain.split("\n\n")[-1] == with_calendar.split("\n\n")[-1]  # same rules
    with pytest.raises(ValueError):
        system_prompt("psychic", NOW)


def test_the_third_arm_keeps_the_calendar_prompt_and_allows_only_one_call():
    assert system_prompt("calendar_single", NOW) == system_prompt("calendar", NOW)
    assert [single_call(arm) for arm in ARMS] == [False, False, True]


def test_the_tools_are_strict_and_named_for_what_they_propose():
    tools = {tool["function"]["name"]: tool["function"] for tool in openai_tools()}
    assert set(tools) == {"create_event", "add_todo", "ask_user"}
    assert all(tool["strict"] and tool["parameters"]["additionalProperties"] is False for tool in tools.values())
    assert set(tools["create_event"]["parameters"]["required"]) == {"title", "date", "start", "end", "recurrence"}


@pytest.mark.parametrize("call", [
    Call("create_event", {"title": "x", "date": "tomorrow", "start": "15:00", "end": None, "recurrence": None}),
    Call("create_event", {"title": "x", "date": "2026-11-13", "start": "3pm", "end": None, "recurrence": None}),
    Call("create_event", {"title": "x", "date": "2026-11-13", "start": "15:00", "end": None,
                          "recurrence": {"freq": "weekly", "interval": 1, "by_day": ["Monday"]}}),
    Call("add_todo", {"title": "x", "due_date": "12/1", "due_time": None}),
    Call("create_event", {"title": "x"}),
    Call("delete_everything", {}),
])
def test_arguments_that_break_the_contract_are_rejected(call):
    assert validate_call(call.name, call.args) is not None


def test_a_date_or_time_already_gone_is_rejected_once_the_clock_is_known():
    last_january = event(date="2026-01-14").args
    assert validate_call("create_event", last_january) is None
    assert "past" in validate_call("create_event", last_january, now=NOW)
    assert "past" in validate_call("add_todo", todo(due_date="2026-11-11").args, now=NOW)
    # It is 10:00 on the 12th: 9:30 today has gone, 15:00 today has not, and a task due today is fine.
    assert "passed" in validate_call("create_event", event(date="2026-11-12", start="09:30").args, now=NOW)
    assert validate_call("create_event", event(date="2026-11-12", start="15:00").args, now=NOW) is None
    assert validate_call("add_todo", todo(due_date="2026-11-12", due_time="09:00").args, now=NOW) is None
    # A repeating event may begin today even though this morning's occurrence has gone.
    daily = event(date="2026-11-12", start="08:00", recurrence={"freq": "daily", "interval": 1, "by_day": []})
    assert validate_call("create_event", daily.args, now=NOW) is None


@pytest.mark.parametrize("recurrence", [
    {"freq": "daily", "interval": 1, "by_day": ["MO", "TU"]},   # daily repeats do not list weekdays
    {"freq": "weekly", "interval": 1, "by_day": []},             # weekly repeats must
])
def test_a_repeat_must_be_daily_or_weekly_on_named_days(recurrence):
    assert validate_call("create_event", event(recurrence=recurrence).args) is not None


def test_well_formed_arguments_pass():
    assert validate_call(event().name, event().args) is None
    assert validate_call(todo().name, todo(due_date="2026-11-13").args) is None
    assert validate_call(ASK.name, ASK.args) is None


# ── Judging one answer ────────────────────────────────────────────────────────

def test_the_right_date_and_time_is_right():
    assert judge(case(DENTIST_AT_3), [event()]).outcome == "right"


@pytest.mark.parametrize("call, kind", [
    (event(date="2026-11-14"), "wrong_date"),
    (event(start="03:00"), "wrong_time"),
    (event(date="2026-11-14", start="03:00"), "wrong_date"),  # the date is reported first
    (event(recurrence={"freq": "weekly", "interval": 1, "by_day": ["FR"]}), "wrong_recurrence"),
    (todo(due_date="2026-11-13", due_time="15:00"), "wrong_tool"),
])
def test_a_filled_in_mistake_is_wrong_and_says_what_kind(call, kind):
    verdict = judge(case(DENTIST_AT_3), [call])
    assert (verdict.outcome, verdict.kind) == ("wrong", kind)


def test_asking_when_the_message_was_clear_is_a_safe_miss():
    verdict = judge(case(DENTIST_AT_3), [ASK])
    assert (verdict.outcome, verdict.kind) == ("safe_miss", "asked")


def test_guessing_when_only_a_question_was_acceptable_is_wrong():
    ambiguous = case([{"tool": "ask_user"}], say="meeting tomorrow at 7")
    assert judge(ambiguous, [ASK]).outcome == "right"
    verdict = judge(ambiguous, [event(start="07:00")])
    assert (verdict.outcome, verdict.kind) == ("wrong", "guessed")


def test_no_call_or_a_malformed_call_is_a_safe_miss_because_nothing_would_be_stored():
    assert judge(case(DENTIST_AT_3), []).kind == "no_call"
    malformed = judge(case(DENTIST_AT_3), [event(start="3pm")])
    assert (malformed.outcome, malformed.kind) == ("safe_miss", "invalid")
    last_year = judge(case(DENTIST_AT_3), [event(date="2026-01-14")], now=NOW)
    assert (last_year.outcome, last_year.kind) == ("safe_miss", "invalid") and "past" in last_year.detail


def test_any_listed_alternative_is_right():
    either = case([{"tool": "create_event", "args": {"date": "2026-11-17", "start": "14:30"}}, {"tool": "ask_user"}],
                  say="dentist next tuesday at 2:30")
    assert judge(either, [event(date="2026-11-17", start="14:30")]).outcome == "right"
    assert judge(either, [ASK]).outcome == "right"
    assert judge(either, [event(date="2026-11-17", start="02:30")]).kind == "wrong_time"


def test_an_end_time_is_only_checked_when_the_message_gave_one():
    assert judge(case(DENTIST_AT_3), [event(end="16:00")]).outcome == "right"
    half_hour = case([{"tool": "create_event", "args": {"date": "2026-11-13", "start": "15:00", "end": "15:30"}}])
    assert judge(half_hour, [event(end="16:00")]).kind == "wrong_time"


def test_a_repeat_is_expected_only_when_asked_for():
    weekly = case([{"tool": "create_event", "args": {
        "start": "09:00", "recurrence.freq": "weekly", "recurrence.by_day": {"set": ["MO"]}}}])
    monday = {"freq": "weekly", "interval": 1, "by_day": ["MO"]}
    assert judge(weekly, [event(date="2026-11-16", start="09:00", recurrence=monday)]).outcome == "right"
    assert judge(weekly, [event(date="2026-11-16", start="09:00")]).kind == "wrong_recurrence"


def test_several_calls_can_be_one_answer_and_an_extra_call_is_wrong():
    three_days = case([{"calls": [
        {"tool": "create_event", "args": {"date": "2026-11-16", "start": "07:00"}},
        {"tool": "create_event", "args": {"date": "2026-11-18", "start": "07:00"}},
    ]}], say="gym mon wed 7am")
    mon, wed = event(date="2026-11-16", start="07:00"), event(date="2026-11-18", start="07:00")
    assert judge(three_days, [wed, mon]).outcome == "right"
    assert judge(three_days, [mon]).kind == "wrong_count"
    assert judge(case(DENTIST_AT_3), [event(), todo()]).kind == "wrong_count"


def test_a_title_check_catches_date_words_left_in_the_title():
    one_on_one = case([{"tool": "create_event", "args": {
        "title": {"has": ["1:1", "sara"], "lacks": ["wednesday", "10am"]}, "start": "10:00",
        "recurrence.freq": "weekly", "recurrence.by_day": {"set": ["WE"]}}}])
    wednesday = {"freq": "weekly", "interval": 1, "by_day": ["WE"]}
    good = event(title="1:1 with Sara", date="2026-11-18", start="10:00", recurrence=wednesday)
    leaky = event(title="1:1 with Sara every Wednesday at 10am", date="2026-11-18", start="10:00",
                  recurrence=wednesday)
    assert judge(one_on_one, [good]).outcome == "right"
    assert judge(one_on_one, [leaky]).kind == "wrong_title"


# ── The case file ─────────────────────────────────────────────────────────────

def test_the_case_file_covers_every_kind_of_date_phrasing():
    cases = load_cases()
    assert len(cases) >= 60 and len({c.id for c in cases}) == len(cases)
    categories = {c.category for c in cases}
    assert categories == {"relative_day", "weekday", "absolute_date", "time_of_day", "duration", "digit_in_title",
                          "recurrence", "todo_due", "must_ask", "non_english"}
    assert all(sum(c.category == category for c in cases) >= 2 for category in categories)


def test_no_case_reuses_a_held_out_message():
    held_out = {turn.say for s in load_blind_set().scenarios if s.split == "holdout" for turn in s.turns}
    assert not held_out & {c.say for c in load_cases()}


def test_a_case_needs_at_least_one_expectation_with_a_known_tool():
    with pytest.raises(ValueError):
        case([])
    with pytest.raises(ValueError):
        case([{"tool": "send_email", "args": {}}])
    with pytest.raises(ValueError):
        case([{"tool": "create_event", "args": {}, "calls": []}])


# ── A run ─────────────────────────────────────────────────────────────────────

TWO_CASES = [
    case(DENTIST_AT_3),
    Case.model_validate({"id": "c2", "say": "meeting tomorrow at 7", "category": "must_ask", "source": "new",
                         "expect": [{"tool": "ask_user"}]}),
]


def scripted(answers):
    """A stand-in model: answers[(arm, say)] is a list of calls, or an exception to raise."""
    def ask(arm: str, system: str, say: str):
        answer = answers[(arm, say)]
        if isinstance(answer, Exception):
            raise answer
        return answer
    return ask


def test_every_case_runs_in_every_arm_the_given_number_of_times():
    seen = []

    def ask(arm, system, say):
        seen.append((arm, say, "Tue 2026-11-17" in system))
        return [event()] if "dentist" in say else [ASK]

    report = run_probe(TWO_CASES, ask, now=NOW, repeats=3, workers=1, arms=("date_only", "calendar"))

    assert len(seen) == 2 * 2 * 3
    assert {(arm, has_calendar) for arm, _, has_calendar in seen} == {("date_only", False), ("calendar", True)}
    assert report["arms"]["calendar"]["summary"]["trials"] == 6
    assert report["arms"]["calendar"]["summary"]["right_rate"] == 1.0
    assert report["now"] == "2026-11-12T10:00:00-05:00" and report["repeats"] == 3
    assert report["arms"]["calendar"]["trials"][0]["seconds"] >= 0
    assert report["arms"]["calendar"]["summary"]["latency_median_s"] is not None


def test_the_summary_separates_safe_misses_from_wrong_answers_and_breaks_them_down():
    answers = {
        ("date_only", "dentist tomorrow at 3pm"): [event(start="03:00")],
        ("date_only", "meeting tomorrow at 7"): [event(start="07:00")],
        ("calendar", "dentist tomorrow at 3pm"): [ASK],
        ("calendar", "meeting tomorrow at 7"): [ASK],
    }
    report = run_probe(TWO_CASES, scripted(answers), now=NOW, repeats=1, workers=1, arms=("date_only", "calendar"))

    worse, better = report["arms"]["date_only"]["summary"], report["arms"]["calendar"]["summary"]
    assert worse["wrong_rate"] == 1.0 and worse["kinds"] == {"wrong_time": 1, "guessed": 1}
    assert better["right_rate"] == 0.5 and better["safe_miss_rate"] == 0.5 and better["wrong_rate"] == 0.0
    assert better["by_category"]["must_ask"] == {"trials": 1, "right": 1, "safe_miss": 0, "wrong": 0}
    trial = report["arms"]["date_only"]["trials"][0]
    assert trial["id"] == "c1" and trial["kind"] == "wrong_time" and "03:00" in trial["detail"]
    assert trial["calls"] == [{"name": "create_event", "args": event(start="03:00").args}]


def test_a_failed_request_is_an_error_not_a_verdict_on_the_model():
    answers = {(arm, c.say): TimeoutError("slow") for arm in ARMS for c in TWO_CASES}
    summary = run_probe(TWO_CASES, scripted(answers), now=NOW, repeats=1, workers=1)["arms"]["calendar"]["summary"]
    assert summary["errors"] == 2 and summary["trials"] == 0 and summary["right_rate"] is None


def test_unstable_cases_are_listed_when_repeats_disagree():
    flips = iter([[event()], [event(start="03:00")], [ASK], [ASK]] * 2)

    def ask(arm, system, say):
        return next(flips)

    report = run_probe(TWO_CASES, ask, now=NOW, repeats=2, workers=1, arms=("calendar",))
    assert report["arms"]["calendar"]["summary"]["unstable"] == ["c1"]


def test_the_decision_follows_the_bars_set_before_the_run():
    assert BARS == {"go": {"wrong_rate_max": 0.02, "right_rate_min": 0.90}, "mitigate": {"wrong_rate_max": 0.05}}
    assert decide({"wrong_rate": 0.01, "right_rate": 0.95}) == "go"
    assert decide({"wrong_rate": 0.01, "right_rate": 0.80}) == "mitigate"   # safe, but asks too often
    assert decide({"wrong_rate": 0.04, "right_rate": 0.95}) == "mitigate"
    assert decide({"wrong_rate": 0.08, "right_rate": 0.95}) == "no_go"
    assert decide({"wrong_rate": None, "right_rate": None}) == "no_data"


def test_the_report_names_the_best_arm_and_its_decision():
    answers = {
        ("date_only", "dentist tomorrow at 3pm"): [event(start="03:00")],
        ("date_only", "meeting tomorrow at 7"): [ASK],
        ("calendar", "dentist tomorrow at 3pm"): [event()],
        ("calendar", "meeting tomorrow at 7"): [ASK],
    }
    report = run_probe(TWO_CASES, scripted(answers), now=NOW, repeats=1, workers=1, model={"model": "fake"},
                       arms=("date_only", "calendar"))
    assert report["best_arm"] == "calendar" and report["decision"] == "go" and report["model"] == {"model": "fake"}
    text = render(report)
    assert "calendar" in text and "date_only" in text and "GO" in text and "wrong_time" in text


def test_a_saved_report_is_not_overwritten(tmp_path):
    report = run_probe(TWO_CASES, lambda arm, system, say: [ASK], now=NOW, repeats=1, workers=1)
    target = save_report(report, tmp_path / "r.json")
    assert json.loads(target.read_text())["decision"] == report["decision"]
    with pytest.raises(FileExistsError):
        save_report(report, target)


def test_summarize_counts_nothing_as_no_rates():
    assert summarize([])["right_rate"] is None


# ── The date check: does the proposed date agree with the date words in the message? ──

D = date  # shorthand for the table below


@pytest.mark.parametrize("say, allowed", [
    ("dentist tomorrow at 3pm", {D(2026, 11, 13)}),
    ("study sesh tmrw 8pm w/ jess", {D(2026, 11, 13)}),
    ("team dinner the day after tomorrow at 7pm", {D(2026, 11, 14)}),
    ("call the bank tonight at 8", {D(2026, 11, 12)}),
    ("coffee with Priya this afternoon at 2", {D(2026, 11, 12)}),
    ("haircut saturday at 9am", {D(2026, 11, 14)}),
    ("i need to finish the report by friday", {D(2026, 11, 13)}),          # the nearest Friday, not next week's
    ("cal fri 3pm standup 30m", {D(2026, 11, 13)}),
    ("meet Alex next friday at 4pm", {D(2026, 11, 13), D(2026, 11, 20)}),   # "next" is read both ways
    ("discovery call next wednesday at 11am", {D(2026, 11, 18)}),           # already in next week: one reading
    ("dentist next monday", {D(2026, 11, 16)}),
    ("standup thursday 9:30am", {D(2026, 11, 12), D(2026, 11, 19)}),       # today is Thursday
    ("lunch with Sam on the 20th at noon", {D(2026, 11, 20)}),
    ("dad's birthday dinner on the 3rd at 7pm", {D(2026, 12, 3)}),         # this month's 3rd has passed
    ("bio exam nov 24 at 9am", {D(2026, 11, 24)}),
    ("recital on December 5th at 6 in the evening", {D(2026, 12, 5)}),
    ("conference on jan 14 at 9am", {D(2027, 1, 14)}),                     # January has passed this year
    ("flight to Vancouver 12/1 at 6:45am", {D(2026, 12, 1)}),
    ("add event 2026-11-16 09:00-10:30 quarterly planning", {D(2026, 11, 16)}),
])
def test_date_words_in_a_message_pin_down_the_date(say, allowed):
    assert dates_the_wording_allows(say, NOW) == allowed


@pytest.mark.parametrize("say", [
    "buy milk",
    "set up a meeting with Jordan",
    "lunch with Sam next week",
    "submit expense report by end of month",
    "gym mon wed fri 7am",                       # several days: left to the repeat check
    "Schedule gym every Monday at 6am",          # a repeat, not one date
    "周六下午三点 团队聚餐",                        # only English date words are read
])
def test_a_message_without_one_clear_date_word_is_not_checked(say):
    assert dates_the_wording_allows(say, NOW) is None


def test_a_proposed_date_that_contradicts_the_wording_is_caught():
    say = "i need to finish the report by friday"
    assert date_contradiction(say, "add_todo", todo(due_date="2026-11-13").args, NOW) is None
    problem = date_contradiction(say, "add_todo", todo(due_date="2026-11-20").args, NOW)
    assert "2026-11-20" in problem and "2026-11-13" in problem
    assert "no date" in date_contradiction("call the bank tonight at 8", "add_todo", todo().args, NOW)


def test_the_date_check_leaves_questions_repeats_and_unclear_messages_alone():
    weekly = event(date="2026-11-16", recurrence={"freq": "weekly", "interval": 1, "by_day": ["FR"]})
    assert date_contradiction("standup friday", weekly.name, weekly.args, NOW) is None
    assert date_contradiction("dentist tomorrow", ASK.name, ASK.args, NOW) is None
    assert date_contradiction("buy milk", "add_todo", todo(due_date="2026-12-25").args, NOW) is None


def test_a_wrong_date_the_wording_contradicts_is_stopped_before_it_is_stored():
    friday = case([{"tool": "add_todo", "args": {"due_date": "2026-11-13"}}], say="finish the report by friday")
    verdict = judge(friday, [todo(due_date="2026-11-20")], now=NOW)
    assert (verdict.outcome, verdict.kind) == ("safe_miss", "date_check") and "2026-11-20" in verdict.detail
    assert judge(friday, [todo(due_date="2026-11-13")], now=NOW).outcome == "right"
    # Without the clock there is nothing to check against, so the mistake stands.
    assert judge(friday, [todo(due_date="2026-11-20")]).kind == "wrong_date"


def test_a_wrong_date_with_no_date_word_to_check_against_is_still_wrong():
    month_end = case([{"tool": "add_todo", "args": {"due_date": "2026-11-30"}}], say="submit expenses by end of month")
    assert judge(month_end, [todo(due_date="2026-12-31")], now=NOW).kind == "wrong_date"


# ── A bare hour: pick the likelier reading and show it ────────────────────────

def test_the_rules_say_to_pick_a_bare_hour_and_to_ask_only_when_a_date_or_time_is_missing():
    rules = system_prompt("calendar_single", NOW).split("\n\n")[-1]
    assert "likelier" in rules and "ask_user" in rules
    assert "is not yours to" not in rules  # the earlier rule, which the smaller models ignored


@pytest.mark.parametrize("case_id", ["ma4", "fr7", "fr8", "v5", "v6"])
def test_a_bare_hour_case_accepts_either_reading_or_a_question(case_id):
    bare = next(c for c in load_cases() if c.id == case_id)
    day = bare.expect[0].args["date"]
    hours = bare.expect[0].args["start"]["any"]
    assert len(hours) == 2 and int(hours[1][:2]) - int(hours[0][:2]) == 12
    for hour in hours:
        assert judge(bare, [event(date=day, start=hour)], now=NOW).outcome == "right"
    assert judge(bare, [ASK], now=NOW).outcome == "right"
    assert judge(bare, [event(date=day, start="13:00")], now=NOW).outcome == "wrong"
