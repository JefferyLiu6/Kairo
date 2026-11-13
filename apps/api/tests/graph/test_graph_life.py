"""Habits and the journal through the coordinator's own tools, and memory through its agent."""
from __future__ import annotations

from kairo.storage.control_store import list_approval_requests, pm_db_path
from kairo.storage.habits import habit_rows
from kairo.storage.snapshot import read_stores

from tests.graph.conftest import USER, call

TO_MEMORY = call("hand_off", domain="memory")


def habits(chat):
    return [(h["name"], h["checkins"]) for h in habit_rows(pm_db_path(USER, chat.data_dir))]


def stored(chat, store):
    return list(read_stores(USER, chat.data_dir)[store].values())


# ── Habits ────────────────────────────────────────────────────────────────────

def test_a_habit_is_tracked_logged_once_and_shown(chat):
    chat.model.append(call("add_habit", name="morning run"))
    assert chat("Add a habit called morning run") == "Now tracking: morning run."

    (run,) = habit_rows(pm_db_path(USER, chat.data_dir))
    chat.model.append(call("log_habit", habit_id=run["id"], date=None))
    assert chat("I did my morning run") == "Logged morning run for Thu 12 Nov."
    assert habits(chat) == [("morning run", ["2026-11-12"])]
    assert f"- [{run['id']}] morning run" in chat.asked[1]["system"]      # the coordinator was shown the habit

    chat.model.append(call("log_habit", habit_id=run["id"], date=None))
    assert chat("Log morning run for today") == "morning run is already logged for Thu 12 Nov. Streak: 1 day."
    assert habits(chat) == [("morning run", ["2026-11-12"])]

    chat.model.append(call("show_habits"))
    assert chat("How's my morning run streak?").splitlines() == [
        "Your habits:", "- morning run — 1-day streak, last logged Thu 12 Nov"]
    assert len(chat.asked) == 4                                           # one model call per turn


def test_a_habit_id_that_is_not_tracked_is_sent_back(chat):
    chat.model.extend([call("log_habit", habit_id="made-up1", date=None),
                       call("ask_user", question="I'm not tracking a morning run yet. Should I start?")])
    assert chat("I did my morning run") == "I'm not tracking a morning run yet. Should I start?"
    assert "made-up1" in chat.asked[1]["turns"][-1][1] and habits(chat) == []


def test_a_routine_the_user_asked_to_be_remembered_goes_to_memory_even_if_the_model_calls_it_a_habit(chat):
    as_habit = call("add_habit", name="sleep until noon on Sundays")
    chat.model.extend([as_habit, as_habit, call("remember", fact="i sleep until noon on sundays")])

    assert chat("remember that i sleep until noon on sundays") == "Remembered: i sleep until noon on sundays."
    assert habits(chat) == [] and [a["prompt"] for a in chat.asked] == ["coordinator", "coordinator", "memory_agent"]


def test_stopping_a_habit_waits_for_approval(chat):
    chat.model.append(call("add_habit", name="stretch"))
    chat("Track a habit: stretch")
    (stretch,) = habit_rows(pm_db_path(USER, chat.data_dir))
    chat.model.append(call("remove_habit", habit_id=stretch["id"]))

    assert "Stop tracking habit: stretch" in chat("stop tracking stretch") and habits(chat) == [("stretch", [])]
    assert chat("approve") == "Stopped tracking: stretch." and habits(chat) == []


# ── Journal ───────────────────────────────────────────────────────────────────

def test_a_journal_entry_is_saved_in_the_users_words_and_read_back(chat):
    chat.model.append(call("write_journal", body="rough day, but I finally shipped the eval and I feel lighter"))
    reply = chat("journal: rough day, but I finally shipped the eval and I feel lighter")
    assert reply == "Saved to your journal: “rough day, but I finally shipped the eval and I feel lighter”"

    chat.model.append(call("show_journal", query=None))
    assert chat("show my journal").splitlines() == [
        "Your latest journal entries:", "- Thu 12 Nov: rough day, but I finally shipped the eval and I feel lighter"]


def test_a_reworded_journal_entry_is_sent_back_and_the_users_words_are_what_is_stored(chat):
    said = "journal: rough day, but I finally shipped the eval and I feel lighter"
    chat.model.extend([call("write_journal", body="The user had a difficult day but completed their evaluation work."),
                       call("write_journal", body="rough day, but I finally shipped the eval and I feel lighter")])
    chat(said)
    assert [entry["body"] for entry in stored(chat, "journal")] == ["rough day, but I finally shipped the eval and I feel lighter"]


# ── Memory ────────────────────────────────────────────────────────────────────

def test_a_fact_is_remembered_by_the_memory_agent_without_a_vault_being_configured(chat):
    assert chat.config.vault_dir is None                # web chat configures none
    chat.model.extend([TO_MEMORY, call("remember", fact="I prefer meetings after 10am")])

    assert chat("remember that I prefer meetings after 10am") == "Remembered: I prefer meetings after 10am."
    assert [m["text"] for m in stored(chat, "memory") if m["kind"] == "fact"] == ["I prefer meetings after 10am"]
    coordinator, agent = chat.asked
    assert agent["prompt"] == "memory_agent" and "Nothing is remembered about the user yet." in agent["system"]
    assert agent["tools"] == ["remember", "export_data", "show_memory", "show_stored_data", "show_activity",
                              "answer", "ask_user", "hand_back"]

    chat.model.extend([TO_MEMORY, call("show_memory")])
    assert chat("what do you know about me?").splitlines() == ["What I remember about you:", "- I prefer meetings after 10am"]
    assert "- I prefer meetings after 10am" in chat.asked[3]["system"]


def test_a_sensitive_fact_is_never_stored_outright_and_the_prompt_does_not_repeat_it(chat):
    chat.model.extend([TO_MEMORY, call("remember", fact="my passport number is X1234567")])

    prompt = chat("Remember that my passport number is X1234567")

    (pending,) = list_approval_requests(USER, chat.data_dir, status="pending")
    assert pending.action_type == "private_note_append" and pending.payload["note"] == "my passport number is X1234567"
    assert prompt.startswith(f"Approval required [{pending.id}]") and "X1234567" not in prompt
    assert stored(chat, "memory") == []

    assert "cancelled" in chat("reject").lower()
    assert stored(chat, "memory") == [] and list_approval_requests(USER, chat.data_dir, status="pending") == []


def test_an_approved_private_note_is_kept_and_acknowledged_without_being_repeated(chat):
    chat.model.extend([TO_MEMORY, call("remember", fact="my passport number is X1234567")])
    chat("Remember that my passport number is X1234567")

    assert chat("approve") == "Saved to your private notes."
    assert [m["kind"] for m in stored(chat, "memory")] == ["private_note"]

    chat.model.extend([TO_MEMORY, call("show_memory")])
    shown = chat("what do you remember about me?")
    assert "1 private note" in shown and "X1234567" not in shown and "X1234567" not in chat.asked[3]["system"]


def test_an_export_waits_for_approval_and_a_data_question_is_answered_with_counts(chat):
    chat.model.extend([TO_MEMORY, call("export_data")])
    assert "Export your private data" in chat("Export my data")
    (pending,) = list_approval_requests(USER, chat.data_dir, status="pending")
    assert pending.action_type == "private_export" and pending.risk_level == "high"

    chat.model.extend([TO_MEMORY, call("show_stored_data")])
    assert chat("What data do you store about me?").startswith("Here is everything I store for you:\n- Tasks: 0")


def test_the_audit_log_is_read_from_the_log_not_made_up(chat):
    chat.model.extend([TO_MEMORY, call("show_activity")])
    assert chat("Show me the audit log") == "Nothing has been recorded in your activity log yet."

    chat.model.extend([call("add_todos", items=[{"title": "rotate API keys", "due_date": None, "due_time": None}]),
                       TO_MEMORY, call("remember", fact="my passport number is X1234567")])
    chat("Add todo: rotate API keys")
    chat("Remember that my passport number is X1234567")
    chat("reject")

    chat.model.extend([TO_MEMORY, call("show_activity")])
    shown = chat("Show me the audit log").splitlines()
    assert shown[0] == "Your latest activity, newest first:"
    # (Every test message is sent at the same pinned minute, so the order within it is not asserted.)
    assert sorted(line.split(" — ", 1)[1] for line in shown[1:]) == [
        "asked for approval: Save a sensitive fact to your private notes",
        "done: Add todo: rotate API keys",
        "rejected: Save a sensitive fact to your private notes",
    ]
    assert all(line.startswith("- Thu 12 Nov 10:00 — ") for line in shown[1:])
    assert "X1234567" not in "\n".join(shown)
