"""Receipts: after an action runs, read the stores back, check them against the command, and say what is stored."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from kairo.core.domain.receipt import Change, Receipt
from kairo.core.domain.types import PMAction
from kairo.storage.store import load_schedule, load_todos
from kairo.tools import receipts
from kairo.tools.executors.dispatcher import execute_pm_action
from kairo.tools.receipts import collecting, diff_stores, render, verify

USER = "receipt-user"


def cfg(tmp_path, *, vault: bool = True):
    return SimpleNamespace(user_id=USER, session_id="pm-receipts", data_dir=str(tmp_path),
                           vault_dir=str(tmp_path / "vault") if vault else None)


def stores(**named):
    """Stores from plain lists: stores(todos=[{"id": "t1", ...}])."""
    return {name: {item["id"]: item for item in items} for name, items in named.items()}


def run(action: PMAction, config) -> tuple[dict, list[Receipt]]:
    with collecting() as made:
        result = execute_pm_action(action, config)
    return result, made.receipts


DENTIST = {"id": "e1", "title": "Dentist", "date": "2026-11-17", "start": "14:30", "end": "15:30",
           "recurrence": None, "exceptions": [], "overrides": [], "series_id": None, "notes": ""}
MILK = {"id": "t1", "title": "buy milk", "done": False, "due": None}


# ── What changed ──────────────────────────────────────────────────────────────

def test_diff_lists_what_was_added_changed_and_removed():
    before = stores(todos=[MILK, {"id": "t2", "title": "eggs", "done": False, "due": None}])
    after = stores(todos=[{**MILK, "done": True}], schedule=[DENTIST])

    changes = diff_stores(before, after)

    assert {(c.store, c.kind) for c in changes} == {("todos", "changed"), ("todos", "removed"), ("schedule", "added")}
    done = next(c for c in changes if c.kind == "changed")
    assert done.before["done"] is False and done.after["done"] is True


# ── Checking the stores against the command ───────────────────────────────────

def test_a_stored_item_that_matches_the_command_is_verified():
    receipt = verify(PMAction("todo_add", {"title": "buy milk", "due": None}), True, "Added 'buy milk'.",
                     stores(), stores(todos=[MILK]))
    assert receipt.status == "verified" and not receipt.problems
    assert [(c.store, c.kind, c.after["title"]) for c in receipt.changes] == [("todos", "added", "buy milk")]


def test_a_stored_field_that_differs_from_the_command_is_a_mismatch_naming_the_field():
    receipt = verify(PMAction("todo_add", {"title": "buy milk", "due": "2026-11-13"}), True, "Added.",
                     stores(), stores(todos=[MILK]))
    assert receipt.status == "mismatch"
    assert any("due" in problem and "2026-11-13" in problem for problem in receipt.problems)


def test_a_change_the_command_did_not_ask_for_is_a_mismatch():
    after = stores(todos=[MILK, {"id": "t2", "title": "surprise", "done": False, "due": None}])
    receipt = verify(PMAction("todo_add", {"title": "buy milk"}), True, "Added.", stores(), after)
    assert receipt.status == "mismatch" and any("surprise" in problem for problem in receipt.problems)


def test_a_failed_action_is_reported_as_failed_with_whatever_did_change():
    clean = verify(PMAction("todo_add", {"title": "x"}), False, "Error: disk full", stores(), stores())
    assert clean.status == "failed" and clean.changes == () and clean.message == "Error: disk full"
    partial = verify(PMAction("todo_add", {"title": "buy milk"}), False, "Error: crashed", stores(),
                     stores(todos=[MILK]))
    assert partial.status == "failed" and len(partial.changes) == 1


def test_success_with_nothing_changed_locally_cannot_be_verified():
    # Already checked in today, already done, or written to an external calendar.
    receipt = verify(PMAction("habit_checkin", {"name": "run"}), True, "Logged **run**.", stores(), stores())
    assert receipt.status == "unverified"


def test_actions_that_only_read_have_no_receipt():
    assert verify(PMAction("list_state", {"target": "todos"}), True, "## Todos", stores(), stores()) is None
    assert verify(PMAction("journal_read", {}), True, "...", stores(), stores()) is None
    assert verify(PMAction("explain", {"message": "no"}), False, "no", stores(), stores()) is None


@pytest.mark.parametrize("action, before, after", [
    (PMAction("todo_complete", {"id": "t1"}), stores(todos=[MILK]), stores(todos=[{**MILK, "done": True}])),
    (PMAction("todo_remove", {"id": "t1"}), stores(todos=[MILK]), stores()),
    (PMAction("schedule_add", {"entries": [{"title": "Dentist", "date": "2026-11-17", "start": "14:30",
                                            "end": "15:30", "recurrence": None}]}),
     stores(), stores(schedule=[DENTIST])),
    (PMAction("schedule_update", {"updates": [{"id": "e1", "start": "16:00", "end": "17:00"}]}),
     stores(schedule=[DENTIST]), stores(schedule=[{**DENTIST, "start": "16:00", "end": "17:00"}])),
    (PMAction("schedule_remove", {"ids": ["e1"]}), stores(schedule=[DENTIST]), stores()),
    (PMAction("schedule_add_exception", {"series_id": "e1", "skip_date": "2026-11-24"}),
     stores(schedule=[DENTIST]), stores(schedule=[{**DENTIST, "exceptions": ["2026-11-24"]}])),
    (PMAction("habit_add", {"name": "morning run"}), stores(),
     stores(habits=[{"id": "h1", "name": "morning run", "checkins": []}])),
    (PMAction("habit_checkin", {"name": "morning run", "checkin_date": "2026-11-12"}),
     stores(habits=[{"id": "h1", "name": "morning run", "checkins": []}]),
     stores(habits=[{"id": "h1", "name": "morning run", "checkins": ["2026-11-12"]}])),
    (PMAction("journal_append", {"body": "slept badly, skipped the gym"}), stores(),
     stores(journal=[{"id": "j1", "body": "slept badly, skipped the gym"}])),
    (PMAction("remember", {"fact": "Leo is allergic to peanuts"}), stores(),
     stores(memory=[{"id": "m1", "kind": "fact", "text": "Leo is allergic to peanuts"}])),
    (PMAction("private_note_append", {"note": "my therapist is Dr. Alvarez"}), stores(),
     stores(memory=[{"id": "m2", "kind": "private_note", "text": "my therapist is Dr. Alvarez"}])),
])
def test_each_kind_of_write_is_verified_when_the_stores_show_it(action, before, after):
    receipt = verify(action, True, "ok", before, after)
    assert receipt.status == "verified", receipt.problems


@pytest.mark.parametrize("action, before, after, clue", [
    (PMAction("schedule_update", {"updates": [{"id": "e1", "start": "16:00"}]}),
     stores(schedule=[DENTIST]), stores(schedule=[{**DENTIST, "start": "09:00"}]), "start"),
    (PMAction("schedule_add", {"entries": [{"title": "Dentist", "date": "2026-11-17", "start": "14:30",
                                            "recurrence": {"freq": "weekly", "by_day": ["TU"]}}]}),
     stores(), stores(schedule=[DENTIST]), "recurrence"),
    (PMAction("schedule_add_exception", {"series_id": "e1", "skip_date": "2026-11-24"}),
     stores(schedule=[DENTIST]), stores(schedule=[{**DENTIST, "exceptions": ["2026-12-01"]}]), "2026-11-24"),
    (PMAction("journal_append", {"body": "slept badly, skipped the gym"}), stores(),
     stores(journal=[{"id": "j1", "body": "slept badly"}]), "body"),
    (PMAction("schedule_remove", {"ids": ["e1"]}), stores(schedule=[DENTIST, {**DENTIST, "id": "e2"}]),
     stores(schedule=[DENTIST]), "e1"),
])
def test_each_kind_of_write_is_a_mismatch_when_the_stores_show_something_else(action, before, after, clue):
    receipt = verify(action, True, "ok", before, after)
    assert receipt.status == "mismatch" and any(clue in problem for problem in receipt.problems), receipt.problems


def test_a_learned_memory_alongside_a_write_is_not_a_mismatch():
    after = stores(todos=[MILK], memory=[{"id": "m1", "kind": "fact", "text": "buys milk weekly"}])
    assert verify(PMAction("todo_add", {"title": "buy milk"}), True, "Added.", stores(), after).status == "verified"


# ── Through the real executors ────────────────────────────────────────────────

def test_running_an_action_inside_a_collector_yields_a_verified_receipt(tmp_path):
    result, made = run(PMAction("todo_add", {"title": "renew passport", "due": "2026-12-01"}), cfg(tmp_path))

    assert result["ok"] and [r.status for r in made] == ["verified"]
    stored = made[0].changes[0].after
    assert stored["title"] == "renew passport" and stored["due"] == "2026-12-01"
    assert stored["id"] == load_todos(USER, str(tmp_path)).items[0].id


def test_a_calendar_event_is_read_back_from_the_schedule_store(tmp_path):
    entry = {"title": "gym", "date": "2026-11-13", "start": "06:00", "end": "07:00", "notes": "", "series_id": "",
             "weekday": None, "recurrence": {"freq": "weekly", "interval": 1, "by_day": ["MO", "WE", "FR"]}}
    _, made = run(PMAction("schedule_add", {"entries": [entry]}), cfg(tmp_path))

    assert [r.status for r in made] == ["verified"], made[0].problems
    assert made[0].changes[0].after["id"] == load_schedule(USER, str(tmp_path)).entries[0].id


def test_a_refused_action_yields_a_failed_receipt_and_changes_nothing(tmp_path):
    result, made = run(PMAction("remember", {"fact": "likes tea"}), cfg(tmp_path, vault=False))
    assert not result["ok"] and [r.status for r in made] == ["failed"] and made[0].changes == ()


def test_nothing_is_collected_or_slowed_down_outside_a_collector(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, "read_stores", lambda *a, **k: pytest.fail("no snapshot without a collector"))
    assert execute_pm_action(PMAction("todo_add", {"title": "x"}), cfg(tmp_path))["ok"]


def test_collectors_do_not_see_each_others_receipts(tmp_path):
    with collecting() as outer:
        execute_pm_action(PMAction("todo_add", {"title": "one"}), cfg(tmp_path))
        with collecting() as inner:
            execute_pm_action(PMAction("todo_add", {"title": "two"}), cfg(tmp_path))
    assert len(outer.receipts) == 1 and len(inner.receipts) == 1


def test_a_receipt_can_be_recorded_directly_and_is_dropped_without_a_collector():
    receipt = Receipt("habit_checkin", "unverified", message="Logged.")
    with collecting() as made:
        receipts.record(receipt)
    assert made.receipts == [receipt]
    receipts.record(receipt)  # harmless with no collector


def test_the_collector_records_that_the_typed_workflow_answered():
    with collecting() as made:
        assert made.workflow_replied is False
        receipts.note_workflow_reply()
    assert made.workflow_replied is True
    receipts.note_workflow_reply()  # harmless with no collector


# ── Saying what is stored ─────────────────────────────────────────────────────

def added(store, item):
    return Receipt("x", "verified", (Change(store, "added", None, item),))


def test_an_event_is_read_back_with_its_weekday_date_and_times():
    assert render([added("schedule", DENTIST)]) == "Added to your calendar: Dentist — Tue 17 Nov, 2:30 PM–3:30 PM."


def test_a_repeating_event_says_how_it_repeats():
    gym = {**DENTIST, "title": "gym", "date": "2026-11-13", "start": "06:00", "end": "07:00",
           "recurrence": {"freq": "weekly", "interval": 1, "by_day": ["MO", "WE", "FR"]}}
    assert render([added("schedule", gym)]) == "Added to your calendar: gym — every Mon/Wed/Fri, 6 AM–7 AM."


def test_a_task_says_its_due_date_or_that_it_has_none():
    assert render([added("todos", MILK)]) == "Added task: buy milk — no due date."
    assert render([added("todos", {**MILK, "due": "2026-11-13"})]) == "Added task: buy milk — due Fri 13 Nov."
    assert render([added("todos", {**MILK, "due": "2026-11-13T17:00:00"})]) == \
        "Added task: buy milk — due Fri 13 Nov, 5 PM."


def test_a_change_shows_what_it_was_before():
    moved = Receipt("schedule_update", "verified",
                    (Change("schedule", "changed", DENTIST, {**DENTIST, "start": "16:00", "end": "17:00"}),))
    assert render([moved]) == "Updated: Dentist — Tue 17 Nov, 4 PM–5 PM (was Tue 17 Nov, 2:30 PM–3:30 PM)."


@pytest.mark.parametrize("change, text", [
    (Change("schedule", "removed", DENTIST, None), "Removed from your calendar: Dentist — Tue 17 Nov, 2:30 PM–3:30 PM."),
    (Change("schedule", "changed", DENTIST, {**DENTIST, "exceptions": ["2026-11-24"]}),
     "Skipping Dentist on Tue 24 Nov; the rest of the series stays."),
    (Change("todos", "changed", MILK, {**MILK, "done": True}), "Checked off: buy milk."),
    (Change("todos", "removed", MILK, None), "Removed task: buy milk."),
    (Change("habits", "added", None, {"id": "h1", "name": "morning run", "checkins": []}),
     "Now tracking: morning run."),
    (Change("habits", "changed", {"id": "h1", "name": "morning run", "checkins": []},
            {"id": "h1", "name": "morning run", "checkins": ["2026-11-12"]}), "Logged morning run for Thu 12 Nov."),
    (Change("journal", "added", None, {"id": "j1", "body": "slept badly, skipped the gym"}),
     "Saved to your journal: “slept badly, skipped the gym”"),
    (Change("memory", "added", None, {"id": "m1", "kind": "fact", "text": "Leo is allergic to peanuts"}),
     "Remembered: Leo is allergic to peanuts."),
    (Change("memory", "added", None, {"id": "m2", "kind": "private_note", "text": "passport X1234567"}),
     "Saved to your private notes."),
])
def test_every_kind_of_change_has_a_plain_sentence(change, text):
    assert render([Receipt("x", "verified", (change,))]) == text


def test_a_long_journal_entry_is_shortened_in_the_reply_only():
    body = "word " * 100
    text = render([added("journal", {"id": "j1", "body": body.strip()})])
    assert text.endswith("…”") and len(text) < 260


def test_several_receipts_become_several_lines():
    text = render([added("todos", MILK), added("todos", {**MILK, "id": "t2", "title": "eggs"})])
    assert text.splitlines() == ["Added task: buy milk — no due date.", "Added task: eggs — no due date."]


def test_a_mismatch_says_what_is_stored_and_that_it_differs():
    receipt = Receipt("todo_add", "mismatch", (Change("todos", "added", None, MILK),),
                      problems=("due is None, the command said '2026-11-13'",))
    text = render([receipt])
    assert text.startswith("Added task: buy milk — no due date.")
    assert "not exactly what was asked" in text and "2026-11-13" in text


def test_a_failure_says_nothing_was_saved_without_leaking_an_internal_error():
    internal = Receipt("todo_add", "failed", (), message="Error: sqlite3.OperationalError: disk I/O error")
    assert render([internal]) == "That didn't go through, and nothing was saved."
    explained = Receipt("todo_complete", "failed", (), message="Couldn't find a task matching 'expense report'.")
    assert render([explained]) == "Couldn't find a task matching 'expense report'."


def test_a_failure_that_still_changed_something_says_what_is_stored_now():
    partial = Receipt("schedule_add", "failed", (Change("schedule", "added", None, DENTIST),), message="Error: boom")
    text = render([partial])
    assert text.startswith("Something went wrong partway.") and "Dentist — Tue 17 Nov" in text
