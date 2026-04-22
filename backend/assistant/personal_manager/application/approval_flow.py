"""Approval flow: approve/reject pending actions from chat or API."""
from __future__ import annotations

from typing import Any, Optional

from ..domain.session import normalize_pm_session_id
from ..domain.types import PMAction
from ..executors.dispatcher import execute_pm_action
from ..parsing.text import _extract_id
from ..persistence.control_store import (
    ApprovalRecord,
    claim_approval_request,
    find_approval_request,
    list_approval_requests,
    record_audit_event,
    reject_approval_request,
    update_approval_status,
)


class _ApprovalConfig:
    def __init__(self, *, session_id: str, data_dir: str, vault_dir: Optional[str] = None) -> None:
        self.session_id = session_id
        self.data_dir = data_dir
        self.vault_dir = vault_dir


def approve_pm_request(
    approval_id: str,
    data_dir: str,
    *,
    vault_dir: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    record = claim_approval_request(data_dir, approval_id, session_id=session_id)
    if record is None:
        existing = find_approval_request(data_dir, approval_id, session_id=session_id)
        return _approval_not_claimed_message(approval_id, existing)

    config = _ApprovalConfig(session_id=record.session_id, data_dir=data_dir, vault_dir=vault_dir)
    action = PMAction(
        action_type=record.action_type,
        payload=record.payload,
        risk_level=record.risk_level,
        requires_approval=True,
        summary=record.summary,
    )
    result = execute_pm_action(action, config)
    final_status = "executed" if result["ok"] else "failed"
    update_approval_status(record.session_id, data_dir, approval_id, final_status, result=result)
    record_audit_event(
        record.session_id,
        data_dir,
        event_type="approval_executed" if result["ok"] else "approval_failed",
        action_type=record.action_type,
        payload_summary=record.summary,
        result_summary=result["message"],
        approval_id=approval_id,
    )
    return result["message"]


def reject_pm_request(
    approval_id: str,
    data_dir: str,
    *,
    session_id: Optional[str] = None,
) -> str:
    record = reject_approval_request(data_dir, approval_id, session_id=session_id)
    if record is None:
        existing = find_approval_request(data_dir, approval_id, session_id=session_id)
        return _approval_not_claimed_message(approval_id, existing)
    record_audit_event(
        record.session_id,
        data_dir,
        event_type="approval_rejected",
        action_type=record.action_type,
        payload_summary=record.summary,
        approval_id=approval_id,
    )
    return "Got it — cancelled that action."


def approve_from_chat(message: str, config: Any) -> str:
    sid = normalize_pm_session_id(config.session_id)
    approval_id = _extract_id(message)
    if not approval_id:
        pending = list_approval_requests(sid, config.data_dir, status="pending", limit=10)
        if not pending:
            return "You don't have any pending approvals right now."
        if len(pending) > 1:
            return _format_pending_choices("approve", pending)
        approval_id = pending[0].id
    return approve_pm_request(
        approval_id,
        config.data_dir,
        vault_dir=getattr(config, "vault_dir", None),
        session_id=sid,
    )


def reject_from_chat(message: str, config: Any) -> str:
    sid = normalize_pm_session_id(config.session_id)
    approval_id = _extract_id(message)
    if not approval_id:
        pending = list_approval_requests(sid, config.data_dir, status="pending", limit=10)
        if not pending:
            return "You don't have any pending approvals right now."
        if len(pending) > 1:
            return _format_pending_choices("reject", pending)
        approval_id = pending[0].id
    return reject_pm_request(approval_id, config.data_dir, session_id=sid)


def _format_approval_prompt(approval: ApprovalRecord) -> str:
    return (
        f"Approval required [{approval.id}]: {approval.summary}\n"
        f"Risk: {approval.risk_level}.\n"
        f"Reply `approve {approval.id}` to go ahead, or `reject {approval.id}` to cancel."
    )


def _format_pending_choices(verb: str, pending: list[ApprovalRecord]) -> str:
    lines = [f"Multiple pending approvals. Tell me which one to {verb}:"]
    for item in pending:
        lines.append(f"- [{item.id}] {item.summary}")
    return "\n".join(lines)


def _approval_not_claimed_message(approval_id: str, record: Optional[ApprovalRecord]) -> str:
    if record is None:
        return f"No approval request found for id {approval_id}."
    if record.status == "executed":
        result = record.result or {}
        return str(result.get("message") or f"Approval {approval_id} was already executed.")
    if record.status == "rejected":
        return f"Approval {approval_id} was already rejected."
    if record.status == "failed":
        result = record.result or {}
        return str(result.get("message") or f"Approval {approval_id} already failed.")
    if record.status == "executing":
        return f"Approval {approval_id} is already being executed."
    if record.status == "approved":
        return f"Approval {approval_id} was already approved."
    return f"Approval {approval_id} could not be claimed."
