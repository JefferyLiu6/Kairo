"""Approval status changes follow APPROVAL_TRANSITIONS and nothing else (Phase 4a)."""
from __future__ import annotations

import pytest

import assistant.personal_manager.workflow as pm_workflow
from assistant.personal_manager.persistence.control_store import (
    APPROVAL_STATUSES,
    APPROVAL_TRANSITIONS,
    ApprovalTransitionError,
    claim_approval_request,
    create_approval_request,
    finish_approval,
    get_approval_request,
    reject_approval_request,
)

SESSION = "pm-transitions"


@pytest.fixture
def approval(tmp_path):
    record = create_approval_request(
        SESSION, str(tmp_path), action_type="schedule_remove", payload={"ids": ["a1"]},
        summary="Remove event", risk_level="medium",
    )
    return str(tmp_path), record.id


def _status(data_dir, approval_id):
    return get_approval_request(SESSION, data_dir, approval_id).status


def test_transition_table_only_uses_known_statuses():
    for source, targets in APPROVAL_TRANSITIONS.items():
        assert source in APPROVAL_STATUSES and targets <= APPROVAL_STATUSES
    assert not any("approved" in targets for targets in APPROVAL_TRANSITIONS.values())


def test_happy_path_pending_executing_executed(approval):
    data_dir, approval_id = approval
    assert claim_approval_request(data_dir, approval_id, session_id=SESSION).status == "executing"
    record = finish_approval(SESSION, data_dir, approval_id, ok=True, result={"ok": True, "message": "done"})
    assert record.status == "executed" and record.result == {"ok": True, "message": "done"}


def test_failed_outcome_is_recorded(approval):
    data_dir, approval_id = approval
    claim_approval_request(data_dir, approval_id, session_id=SESSION)
    assert finish_approval(SESSION, data_dir, approval_id, ok=False, result={"ok": False}).status == "failed"


def test_cannot_finish_an_approval_that_was_never_claimed(approval):
    data_dir, approval_id = approval
    with pytest.raises(ApprovalTransitionError, match="'pending'"):
        finish_approval(SESSION, data_dir, approval_id, ok=True, result={})
    assert _status(data_dir, approval_id) == "pending"


def test_cannot_finish_twice_or_overwrite_an_outcome(approval):
    data_dir, approval_id = approval
    claim_approval_request(data_dir, approval_id, session_id=SESSION)
    finish_approval(SESSION, data_dir, approval_id, ok=True, result={"ok": True})
    with pytest.raises(ApprovalTransitionError, match="'executed'"):
        finish_approval(SESSION, data_dir, approval_id, ok=False, result={"ok": False})
    assert _status(data_dir, approval_id) == "executed"


def test_a_rejected_approval_cannot_be_claimed_or_finished(approval):
    data_dir, approval_id = approval
    reject_approval_request(data_dir, approval_id, session_id=SESSION)
    assert claim_approval_request(data_dir, approval_id, session_id=SESSION) is None
    with pytest.raises(ApprovalTransitionError):
        finish_approval(SESSION, data_dir, approval_id, ok=True, result={})
    assert _status(data_dir, approval_id) == "rejected"


def test_an_executing_approval_cannot_be_rejected_or_claimed_again(approval):
    data_dir, approval_id = approval
    claim_approval_request(data_dir, approval_id, session_id=SESSION)
    assert reject_approval_request(data_dir, approval_id, session_id=SESSION) is None
    assert claim_approval_request(data_dir, approval_id, session_id=SESSION) is None
    assert _status(data_dir, approval_id) == "executing"


def test_claim_is_scoped_to_its_session(approval):
    data_dir, approval_id = approval
    assert claim_approval_request(data_dir, approval_id, session_id="pm-someone-else") is None
    assert _status(data_dir, approval_id) == "pending"


def test_approvals_are_always_looked_up_in_their_owners_store(approval):
    # The old session-less path scanned data/personal-manager/, where no store lives.
    data_dir, approval_id = approval
    with pytest.raises(TypeError):
        claim_approval_request(data_dir, approval_id)  # session_id is required
    with pytest.raises(ValueError, match="owner's session"):
        pm_workflow.approve_pm_request(approval_id, data_dir)
    assert _status(data_dir, approval_id) == "pending"
