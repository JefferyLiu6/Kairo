"""Approval flow: approve/reject pending actions from chat or API.

Several changes proposed together that each need approval are asked for once, as a batch: one
approval that lists every change. Approving it runs each change in turn, through the same
executors and with a receipt each, so nothing in a batch is less checked than a change on its own.
"""
from __future__ import annotations

from typing import Any, Optional

from kairo.core.domain.session import normalize_pm_session_id
from kairo.core.domain.types import PMAction
from kairo.agents.personal_manager.application.approval_policy import apply_approval_policy
from kairo.tools.executors.dispatcher import execute_pm_action
from kairo.tools.receipts import collecting, read_back_reply, record as record_receipt
from kairo.utils.parsing.text import _extract_id
from kairo.storage.control_store import ApprovalRecord, claim_approval_request, create_approval_request, find_approval_request, finish_approval, list_approval_requests, record_audit_event, reject_approval_request


class _ApprovalConfig:
    def __init__(self, *, session_id: str, data_dir: str, vault_dir: Optional[str] = None) -> None:
        self.user_id = session_id  # already the effective uid stored at approval creation time
        self.session_id = session_id
        self.data_dir = data_dir
        self.vault_dir = vault_dir


def submit_action(
    action: PMAction,
    config: Any,
    session_id: str,
    *,
    thread_id: str = "",
    intent: str = "",
) -> tuple[str, dict[str, Any]]:
    """Apply the approval policy to one typed action, then queue it for approval or run it.

    Returns (summary, result). `result` has "ok" and "message"; a queued action also has
    "pending": True and its "approval_id", and its message is the approval prompt.
    """
    tid = thread_id or session_id
    uid = session_id if thread_id else ""
    guarded = apply_approval_policy(action)
    summary = guarded.summary or action.summary or guarded.action_type
    if guarded.requires_approval:
        approval = create_approval_request(
            session_id,
            config.data_dir,
            action_type=guarded.action_type,
            payload={**guarded.payload, "_thread_id": tid},
            summary=summary,
            risk_level=guarded.risk_level,
        )
        record_audit_event(
            tid,
            config.data_dir,
            user_id=uid,
            event_type="approval_created",
            intent=intent,
            action_type=guarded.action_type,
            payload_summary=summary,
            approval_id=approval.id,
        )
        return summary, {"ok": True, "pending": True, "approval_id": approval.id,
                         "message": _format_approval_prompt(approval)}

    result = execute_pm_action(guarded, config)
    record_audit_event(
        tid,
        config.data_dir,
        user_id=uid,
        event_type="action_executed" if result["ok"] else "action_failed",
        intent=intent,
        action_type=guarded.action_type,
        payload_summary=summary or guarded.payload,
        result_summary=result["message"],
    )
    return summary, result


BATCH = "batch"            # the action type of an approval that holds several changes
_RISK_ORDER = ("low", "medium", "high")


def submit_actions(
    actions: list[PMAction],
    config: Any,
    session_id: str,
    *,
    thread_id: str = "",
    intent: str = "",
) -> list[dict[str, Any]]:
    """Apply the approval policy to the actions of one proposal. Those that need no approval run
    now. Those that do are queued: one on its own as before, several as one batch approval.

    Returns one result per thing that happened, each like `submit_action`'s.
    """
    run_now, waiting = split_by_approval(actions)
    results = [submit_action(action, config, session_id, thread_id=thread_id, intent=intent)[1] for action in run_now]
    return results + queue_for_approval(waiting, config, session_id, thread_id=thread_id, intent=intent)


def split_by_approval(actions: list[PMAction]) -> tuple[list[PMAction], list[PMAction]]:
    """(the actions that may run now, the ones that have to wait for approval), by the approval policy."""
    guarded = [apply_approval_policy(action) for action in actions]
    return ([action for action in guarded if not action.requires_approval],
            [action for action in guarded if action.requires_approval])


def queue_for_approval(
    waiting: list[PMAction],
    config: Any,
    session_id: str,
    *,
    thread_id: str = "",
    intent: str = "",
) -> list[dict[str, Any]]:
    """Ask for approval of actions that need it: one as an approval of its own, several as one batch."""
    if not waiting:
        return []
    if len(waiting) == 1:
        return [submit_action(waiting[0], config, session_id, thread_id=thread_id, intent=intent)[1]]
    tid = thread_id or session_id
    lines = [action.summary or action.action_type for action in waiting]
    summary = "\n".join([f"{len(waiting)} changes:", *(f"- {line}" for line in lines)])
    approval = create_approval_request(
        session_id,
        config.data_dir,
        action_type=BATCH,
        payload={"actions": [{"action_type": a.action_type, "payload": a.payload, "summary": a.summary,
                              "risk_level": a.risk_level} for a in waiting], "_thread_id": tid},
        summary=summary,
        risk_level=max((a.risk_level for a in waiting), key=_RISK_ORDER.index),
    )
    record_audit_event(tid, config.data_dir, user_id=session_id if thread_id else "", event_type="approval_created",
                       intent=intent, action_type=BATCH, payload_summary=summary, approval_id=approval.id)
    return [{"ok": True, "pending": True, "approval_id": approval.id, "message": _format_approval_prompt(approval)}]


def _run_approved(record: ApprovalRecord, config: Any) -> dict[str, Any]:
    """Run what an approval holds: its one action, or each action of a batch in turn. A change in a
    batch that fails does not stop the ones after it; the receipts say what happened to each."""
    if record.action_type != BATCH:
        return execute_pm_action(PMAction(action_type=record.action_type, payload=record.payload,
                                          risk_level=record.risk_level, requires_approval=True,
                                          summary=record.summary), config)
    outcomes = [execute_pm_action(PMAction(action_type=item["action_type"], payload=item["payload"],
                                           risk_level=item.get("risk_level", "medium"), requires_approval=True,
                                           summary=item.get("summary", "")), config)
                for item in record.payload.get("actions", [])]
    return {"ok": bool(outcomes) and all(outcome["ok"] for outcome in outcomes),
            "message": "\n".join(str(outcome["message"]) for outcome in outcomes)}


def approve_pm_request(
    approval_id: str,
    data_dir: str,
    *,
    vault_dir: Optional[str] = None,
    session_id: str,
) -> str:
    record = claim_approval_request(data_dir, approval_id, session_id=session_id)
    if record is None:
        existing = find_approval_request(data_dir, approval_id, session_id=session_id)
        return _approval_not_claimed_message(approval_id, existing)

    config = _ApprovalConfig(session_id=record.session_id, data_dir=data_dir, vault_dir=vault_dir)
    with collecting() as made:
        result = _run_approved(record, config)
    for receipt in made.receipts:
        record_receipt(receipt)  # pass them on to whoever is collecting for the whole turn
    # Keep what was read back from storage (before and after) with the approval, so a caller that
    # answers from stored state can say the same thing if the approval is sent again.
    read_back = read_back_reply(made.receipts)
    if read_back:
        result = {**result, "read_back": read_back}
    finish_approval(record.session_id, data_dir, approval_id, ok=bool(result["ok"]), result=result)
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


def stored_read_back(message: str, config: Any) -> Optional[str]:
    """What was read back when the approval named in `message` ran, if it already ran in this thread."""
    approval_id = _extract_id(message)
    if not approval_id:
        return None
    sid = getattr(config, "user_id", "") or normalize_pm_session_id(config.session_id)
    record = find_approval_request(config.data_dir, approval_id, session_id=sid)
    if record is None or record.status != "executed":
        return None
    if record.payload.get("_thread_id") != normalize_pm_session_id(config.session_id):
        return None
    return (record.result or {}).get("read_back")


def reject_pm_request(
    approval_id: str,
    data_dir: str,
    *,
    session_id: str,
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
    sid = getattr(config, "user_id", "") or normalize_pm_session_id(config.session_id)
    thread_id = normalize_pm_session_id(config.session_id)
    approval_id = _extract_id(message)
    if not approval_id:
        pending = list_approval_requests(sid, config.data_dir, status="pending", limit=10)
        # Scope generic approval to the current thread only.
        thread_pending = [a for a in pending if a.payload.get("_thread_id") == thread_id]
        if not thread_pending:
            return "You don't have any pending approvals right now."
        if len(thread_pending) > 1:
            return _format_pending_choices("approve", thread_pending)
        approval_id = thread_pending[0].id
    else:
        # Explicit ID — verify the approval belongs to this thread before executing.
        record = find_approval_request(config.data_dir, approval_id, session_id=sid)
        if record is None or record.payload.get("_thread_id") != thread_id:
            return f"No approval request found for id {approval_id}."
    return approve_pm_request(
        approval_id,
        config.data_dir,
        vault_dir=getattr(config, "vault_dir", None),
        session_id=sid,
    )


def reject_from_chat(message: str, config: Any) -> str:
    sid = getattr(config, "user_id", "") or normalize_pm_session_id(config.session_id)
    thread_id = normalize_pm_session_id(config.session_id)
    approval_id = _extract_id(message)
    if not approval_id:
        pending = list_approval_requests(sid, config.data_dir, status="pending", limit=10)
        # Scope generic rejection to the current thread only.
        thread_pending = [a for a in pending if a.payload.get("_thread_id") == thread_id]
        if not thread_pending:
            return "You don't have any pending approvals right now."
        if len(thread_pending) > 1:
            return _format_pending_choices("reject", thread_pending)
        approval_id = thread_pending[0].id
    else:
        # Explicit ID — verify the approval belongs to this thread before rejecting.
        record = find_approval_request(config.data_dir, approval_id, session_id=sid)
        if record is None or record.payload.get("_thread_id") != thread_id:
            return f"No approval request found for id {approval_id}."
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
