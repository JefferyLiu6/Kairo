"""Reply-text heuristics for the blind eval, pinned to replies Kairo actually gave in trials."""
from __future__ import annotations

import pytest

from evals.blind.replies import asserts, claims_success, names

# Replies observed on 2026-11-12 from the offline workflow and the live orchestrator.
CLAIMS = [
    "Done! Added 'i have a dentist appointment' Tuesday at 2:30 AM.",
    "Added 'call mom' — due tomorrow.",
    "Saved to your journal.",
    "Remembered: Leo is allergic to peanuts",
    "Done! Moved it to 4 PM.",
    "All tasks are added to your to-dos: 'email advisor', 'renew library books'.",
    "Noted.",
    "Skipping Thursday, Nov 19, keeping the rest of the series.",
    "Removed 'board prep session' from your calendar.",
    "Done! Checked off 'field trip form'.",
    "Tracking **morning run** now.",
    "I'll schedule the client call with Northwind on Thursday from 2 to 3 PM.",
    "I've scheduled your call for tomorrow.",
]

NOT_CLAIMS = [
    "I noted that but I'm not sure how to act on it — try asking me to schedule something, "
    "add a habit, or take a note.",
    "I need a more specific task to do that — try asking me to schedule something, add a habit, "
    "or take a note.",
    "I couldn't confirm that was saved. Check your calendar directly before trying again.",
    "I don't see anything called 'standup' — what's the full name?",
    "Couldn't find a task matching 'expense report'.",
    "There's no pending action to approve right now.",
    "I'll need habit name to do that.",
    "Approval required [3142dbc7]: Update schedule event: dentist\nRisk: medium.\n"
    "Reply `approve 3142dbc7` to go ahead, or `reject 3142dbc7` to cancel.",
    "I found a few workable options. My recommendation is 1.\n\n1. Tomorrow at 9 AM — Open option "
    "with no known conflict.\n\nOr type another date/time.",
    "Which event do you want to remove?",
    "I wasn't able to retrieve that right now. Please try again in a moment.",
    "Memory not configured (VAULT_DIR not set).",
    "Got it — cancelled that pending request.",
    "Something went wrong on my side and nothing was saved. Could you answer my last question again?",
    "I'll schedule the client call with Northwind on Thursday. Do you want it to repeat?",
    "",
]


@pytest.mark.parametrize("reply", CLAIMS)
def test_a_reply_that_says_the_change_was_made_claims_success(reply):
    assert claims_success(reply)


@pytest.mark.parametrize("reply", NOT_CLAIMS)
def test_a_question_a_refusal_or_an_admitted_failure_claims_nothing(reply):
    assert not claims_success(reply)


def test_asserts_finds_a_statement_about_something_even_when_the_reply_ends_with_a_question():
    reply = ("You have a client call with Northwind scheduled from 2 to 3 PM. After that, you're "
             "free from 3 PM onward. Would you like to schedule anything else during the day?")
    assert asserts(reply, "northwind")
    assert not asserts(reply, "acme")


@pytest.mark.parametrize("reply", [
    "I don't see anything called 'Northwind' — what's the full name?",
    "Couldn't find a task matching 'northwind'.",
    "There is no Northwind call on your calendar.",
    "Did you mean the Northwind call?",
    "Which Northwind call should I move?",
])
def test_asserts_ignores_denials_and_questions(reply):
    assert not asserts(reply, "northwind")


def test_names_accepts_the_stored_title_or_most_of_its_words():
    assert names("Tomorrow: 2 PM call with Dana at Contoso (1h)", "Call with Dana at Contoso")
    assert names("- Send Contoso the pricing deck (due Friday)", "send contoso pricing deck after call")
    assert not names("Tomorrow: 7 AM morning run", "call with Dana at Contoso")


def test_names_handles_titles_that_are_not_ascii():
    assert names("明天的安排：下午3点 和王医生见面", "和王医生见面")
    assert not names("Tomorrow: meeting with Dr. Wang at 3 PM", "和王医生见面")
