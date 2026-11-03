"""
What a message means when Kairo is already waiting for an answer.

This module is the one owner of that question, for both layers:

- the orchestrator asks `route_message` (approval command, answer to the open
  question, or a new request), `pm_asked_a_question` after a PM call, and
  `still_open_after_error` when the PM failed;
- the PM workflow asks `resolve_pending_reply`, which runs the rules in `PENDING_RULES`, in order, against the
thread's pending question. A rule either answers the turn (returns the reply),
changes the turn and lets later rules and the normal pipeline continue, or does
nothing. The order is the policy: e.g. a high-confidence new request must be
recognised before the confirmation editor can mistake it for a correction.

All pending writes go through `PendingTurn.clear` / `PendingTurn.reply`, which
also keep the decision log (TurnDecision) in step.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

from kairo.core.domain.pending import ConfirmationPending, DisambiguationPending, FieldChoicesPending, Pending, TodoListPending, TodoSelectionPending, is_question, parse_pending
from kairo.core.domain.types import PMIntent, PMPlanExtraction
from kairo.storage.control_store import record_audit_event
from kairo.storage.decision_log import TurnDecision
from kairo.agents.personal_manager.application.clarification import _apply_clarification_to_plan, _apply_pending_confirmation_response, _apply_pending_field_choice_response, _clear_pending, _is_clarification_response, _is_more_options_reply, _is_pending_cancel_reply, _is_pending_clarification_response, _is_something_else_reply, _load_pending, _match_pending_disambiguation_candidate, _pending_is_stale, _pending_message_is_dialogue_reply, _pending_payload_to_plan, _reconstruct_with_pending, _save_pending_confirmation, _save_pending_field_choices
from kairo.agents.personal_manager.application.contextual_scheduling import build_contextual_plan_from_candidate
from kairo.agents.personal_manager.application.time_slot_recommendation import is_time_slot_recommendation_request, replay_time_slot_recommendation
from kairo.agents.personal_manager.application.todo_selection import resume_todo_selection

Extract = Callable[[str, Any], PMPlanExtraction]


@dataclass
class PendingTurn:
    """One turn's view of the pending question, and what the rules decided so far."""

    message: str
    config: Any
    sid: str          # user id: owns user-level data
    thread_id: str    # scopes pending state
    d: TurnDecision
    extract: Extract  # workflow.extract_pm_plan, so test hooks on workflow apply
    payload: Optional[dict[str, Any]] = None  # dict view, for helpers not yet typed
    state: Optional[Pending] = None
    plan: Optional[PMPlanExtraction] = None
    effective_message: str = ""
    extracted_for_guard: Optional[PMPlanExtraction] = None
    was_active: bool = False  # a question was open for this message (see workflow's approval guard)
    _done: bool = field(default=False, repr=False)

    @classmethod
    def load(cls, message: str, config: Any, sid: str, thread_id: str, d: TurnDecision, extract: Extract) -> "PendingTurn":
        payload = _load_pending(thread_id, config.data_dir, user_id=sid)
        turn = cls(message, config, sid, thread_id, d, extract, effective_message=message)
        if payload is not None:
            d.set_working_memory(payload)
            turn.payload, turn.state, turn.was_active = payload, parse_pending(payload), True
        return turn

    def clear(self, status: str, outcome: str) -> None:
        """End the pending question with `status`; the turn continues without it."""
        _clear_pending(self.thread_id, self.config.data_dir, user_id=self.sid, status=status)
        self.d.wm_outcome = outcome
        self.payload = self.state = None

    def reply(self, text: str, *, route: str, reason: str, wm_after: str,
              outcome: Optional[str] = None, written: Optional[str] = None) -> str:
        """Answer the turn now."""
        if outcome:
            self.d.wm_outcome = outcome
        self.d.route(route, reason)
        self.d.wm_after = wm_after
        if written:
            self.d.memory_written.append(written)
        self.d.set_reply(text)
        return text

    def audit(self, event_type: str, intent: str, payload_summary: dict[str, Any], result_summary: Any) -> None:
        record_audit_event(
            self.thread_id, self.config.data_dir, user_id=self.sid, event_type=event_type,
            intent=intent, payload_summary=payload_summary, result_summary=result_summary,
        )


# ── Rules, in order ───────────────────────────────────────────────────────────

def _new_time_slot_request_replaces_choices(t: PendingTurn) -> Optional[str]:
    if isinstance(t.state, FieldChoicesPending) and is_time_slot_recommendation_request(t.message):
        t.clear("replaced", "replaced_new_request")
        t.was_active = False  # a fresh request, not a reply to the question it replaced
    return None


def _cancel(t: PendingTurn) -> Optional[str]:
    if not _is_pending_cancel_reply(t.message):
        return None
    t.clear("cancelled", "cancelled")
    t.audit("working_memory_cancelled", "DIALOGUE", {"message": t.message}, "Cancelled pending request")
    return t.reply("Got it — cancelled that pending request.",
                   route="cancel", reason="user cancelled pending request", wm_after="cancelled")


def _todo_selection(t: PendingTurn) -> Optional[str]:
    if not isinstance(t.state, (TodoSelectionPending, TodoListPending)):
        return None
    selected_plan, selection_reply = resume_todo_selection(t.payload, t.message, t.config, t.sid)
    if selection_reply is not None:
        return t.reply(selection_reply, route="todo_selection", reason="task selection needs clarification",
                       wm_after="active:awaiting_disambiguation", outcome="kept")
    t.clear("resolved" if selected_plan else "replaced", "resolved" if selected_plan else "replaced_new_request")
    t.plan = selected_plan
    return None


def _disambiguation(t: PendingTurn) -> Optional[str]:
    if not isinstance(t.state, DisambiguationPending):
        return None
    pending = t.state
    selected = _match_pending_disambiguation_candidate(t.payload, t.message)
    if selected is None:
        return None
    built = build_contextual_plan_from_candidate(
        activity=str(selected.get("activity") or ""),
        label=str(selected.get("label") or ""),
        parsed_date=pending.parsed_date,
        parsed_start=pending.parsed_start,
    )
    if built is None:
        return None
    new_plan, confirmation_summary, provenance = built
    t.clear("resolved", "resolved")
    _save_pending_confirmation(
        t.thread_id, t.config.data_dir, new_plan, user_id=t.sid,
        blocking_task_id=new_plan.tasks[0].task_id, confirmation_summary=confirmation_summary,
        original_message=pending.original_message or t.message, provenance=provenance,
    )
    t.audit("disambiguation_resolved", PMIntent.CREATE_SCHEDULE_EVENT.value,
            {"message": t.message, "selected": selected}, confirmation_summary)
    return t.reply(confirmation_summary, route="disambiguation_resolved",
                   reason="user picked one of the disambiguation candidates",
                   wm_after="active:awaiting_confirmation", written="working_memory:awaiting_confirmation")


def _new_request_or_stale(t: PendingTurn) -> Optional[str]:
    # Before the confirmation editor: otherwise "Schedule dentist tomorrow at 9am"
    # arriving while a basketball draft is pending is read as edits to the draft.
    t.extracted_for_guard = t.extract(t.message, t.config)
    if _is_high_confidence_new_request(t.extracted_for_guard, t.message, t.payload):
        t.clear("replaced", "replaced_new_request")
        t.plan = t.extracted_for_guard
    elif _pending_is_stale(t.payload) and not _pending_message_is_dialogue_reply(t.message, t.payload):
        t.clear("replaced", "replaced_stale")
    return None


def _confirmation(t: PendingTurn) -> Optional[str]:
    if not isinstance(t.state, ConfirmationPending):
        return None
    confirmation = _apply_pending_confirmation_response(t.payload, t.message, t.config)
    if confirmation.status == "execute" and confirmation.plan is not None:
        t.plan = confirmation.plan
        t.payload = t.state = None
        t.d.wm_outcome = "resolved"
    elif confirmation.status == "refresh":
        return t.reply(confirmation.reply, route="confirmation", reason="user corrected pending confirmation",
                       wm_after="active:awaiting_confirmation", outcome="kept",
                       written="working_memory:awaiting_confirmation")
    return None


def _something_else(t: PendingTurn) -> Optional[str]:
    if not (isinstance(t.state, FieldChoicesPending) and _is_something_else_reply(t.message)):
        return None
    replay = replay_time_slot_recommendation(t.payload, t.config)
    if replay is None:
        return None
    _save_pending_field_choices(
        t.thread_id, t.config.data_dir, replay.plan, user_id=t.sid,
        blocking_task_id=replay.blocking_task_id, missing=replay.missing, choices=replay.choices,
    )
    t.audit("time_slot_recommendation_replay", PMIntent.GENERAL_COACHING.value, {"message": t.message}, replay.prompt)
    return t.reply(replay.prompt, route="time_slot_replay", reason="user asked for a different recommendation",
                   wm_after="active:awaiting_choice", outcome="replay", written="working_memory:awaiting_choice")


def _more_options(t: PendingTurn) -> Optional[str]:
    if not _is_more_options_reply(t.message):
        return None
    t.clear("replaced", "more_options")
    return t.reply("Ask me again with the time and I'll generate a fresh set of options.",
                   route="more_options", reason="user asked for more options on pending choices", wm_after="cancelled")


def _answer_to_question(t: PendingTurn) -> Optional[str]:
    if isinstance(t.state, ConfirmationPending) or not _is_pending_clarification_response(t.message):
        return None
    pending_plan = _pending_payload_to_plan(t.payload)
    if pending_plan is not None:
        if isinstance(t.state, FieldChoicesPending):
            t.plan = _apply_pending_field_choice_response(pending_plan, t.payload, t.message, t.config)
        else:
            t.plan = _apply_clarification_to_plan(
                pending_plan,
                str(t.payload.get("blocking_task_id", "")),
                [str(item) for item in t.payload.get("missing", [])],
                t.message,
            )
        t.d.wm_outcome = "resolved"
    elif _is_clarification_response(t.message):
        reconstructed = _reconstruct_with_pending(t.payload, t.message)
        if reconstructed:
            t.effective_message = reconstructed
        t.d.wm_outcome = "resolved"
    return None


PENDING_RULES: tuple[Callable[[PendingTurn], Optional[str]], ...] = (
    _new_time_slot_request_replaces_choices,
    _cancel,
    _todo_selection,
    _disambiguation,
    _new_request_or_stale,
    _confirmation,
    _something_else,
    _more_options,
    _answer_to_question,
)


def resolve_pending_reply(t: PendingTurn) -> Optional[str]:
    """Run the rules while a question is pending; return a reply if one answers the turn."""
    for rule in PENDING_RULES:
        if t.state is None:
            break
        reply = rule(t)
        if reply is not None:
            return reply
    if t.was_active and t.d.wm_outcome == "none":
        t.d.wm_outcome = "kept"
    return None


def _is_high_confidence_new_request(plan: PMPlanExtraction, message: str, pending: dict[str, Any]) -> bool:
    if plan.confidence < 0.75 or not plan.tasks:
        return False
    if plan.tasks[0].intent in {PMIntent.UNKNOWN, PMIntent.GENERAL_COACHING}:
        return False
    return not _pending_message_is_dialogue_reply(message, pending)


# ── Orchestrator decisions ────────────────────────────────────────────────────

@dataclass(frozen=True)
class OpenQuestion:
    """A question the user still owes an answer to."""

    id: str  # working-memory record id: the same id later means the same, unanswered question
    state: Pending


Route = Literal["approval_command", "answer_question", "new_request"]
_APPROVAL_COMMAND = re.compile(r"(approve|reject)(?:\s+[0-9a-f]{8})?", re.I)
_STILL_OPEN = "Something went wrong on my side and nothing was saved."


def load_open_question(thread_id: str, data_dir: str, *, user_id: str) -> Optional[OpenQuestion]:
    """The thread's open question, if any. A shown task list is context, not a question."""
    payload = _load_pending(thread_id, data_dir, user_id=user_id)
    if payload is None:
        return None
    state = parse_pending(payload)
    if not is_question(state):
        return None
    return OpenQuestion(id=payload["_working_memory"]["id"], state=state)


def route_message(message: str, question: Optional[OpenQuestion]) -> Route:
    """How the orchestrator should treat a message, given the open question.

    A bare "approve"/"reject" while a question is open is an answer to that
    question ("approve" the suggested time), not a command for the approval queue;
    naming an approval id always makes it a command.
    """
    text = message.strip()
    if _APPROVAL_COMMAND.fullmatch(text) and (question is None or " " in text):
        return "approval_command"
    return "answer_question" if question is not None else "new_request"


def approval_verb(message: str) -> str:
    """"approve" or "reject" for a message `route_message` called an approval command."""
    return _APPROVAL_COMMAND.fullmatch(message.strip()).group(1).lower()


def _is_pm_error(pm_output: str) -> bool:
    return pm_output.strip().lower().startswith("error:")


def pm_asked_a_question(pm_output: str, question_now: Callable[[], Optional[OpenQuestion]]) -> bool:
    """The PM answered by asking the user something (a choice, a missing field)."""
    return bool(pm_output.strip()) and not _is_pm_error(pm_output) and question_now() is not None


def still_open_after_error(
    before: Optional[OpenQuestion],
    pm_output: str,
    question_now: Callable[[], Optional[OpenQuestion]],
) -> Optional[OpenQuestion]:
    """The question, if the PM failed while answering it and left it untouched.

    The PM clears a question before acting on the answer, so the same id after an
    error means nothing ran and the user's options are still valid.
    """
    if before is None or not _is_pm_error(pm_output):
        return None
    after = question_now()
    return after if after is not None and after.id == before.id else None


def still_open_reply(question: OpenQuestion) -> str:
    choices = question.state.choices if isinstance(question.state, FieldChoicesPending) else []
    if not choices:
        return f"{_STILL_OPEN} Could you answer my last question again?"
    lines = "\n".join(f"{c.id}. {c.label}" for c in choices)
    return f"{_STILL_OPEN} Your options are still open:\n\n{lines}\n\nReply with a number, or type another date/time."
