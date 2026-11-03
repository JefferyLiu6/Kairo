"""How a reply to a pending question is resolved (Phase 4c)."""
from __future__ import annotations


def test_pending_rules_run_in_policy_order():
    # The order is the policy. In particular a high-confidence new request must be
    # recognised before the confirmation editor reads it as a correction, and an
    # answer to the question is the last thing tried. Reordering = update this list.
    from assistant.personal_manager.application.pending_reply import PENDING_RULES

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
