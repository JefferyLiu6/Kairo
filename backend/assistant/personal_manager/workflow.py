"""Personal-manager workflow coordinator."""
from __future__ import annotations

from typing import Any, Optional

from .application import approval_flow as _approval_flow
from .application import extraction as _extraction
from .application.approval_flow import _format_approval_prompt
from .application.approval_policy import apply_approval_policy
from .application.clarification import (
    _apply_clarification_to_plan,
    _apply_pending_confirmation_response,
    _apply_pending_field_choice_response,
    _clear_pending,
    _find_plan_blocker,
    _is_clarification_response,
    _is_more_options_reply,
    _is_pending_cancel_reply,
    _is_pending_clarification_response,
    _is_something_else_reply,
    _load_pending,
    _match_pending_disambiguation_candidate,
    _pending_payload_to_plan,
    _pending_is_stale,
    _pending_message_is_dialogue_reply,
    _reconstruct_with_pending,
    _save_pending_confirmation,
    _save_pending_disambiguation,
    _save_pending_field_choices,
    _save_pending_plan,
)
from .application.contextual_scheduling import (
    apply_context_to_explicit_schedule_plan,
    build_contextual_plan_from_candidate,
    build_contextual_schedule_proposal,
)
from .application.field_completion import build_field_completion_proposal
from .application.memory_query import build_memory_query_reply
from .application.planner import plan_pm_actions
from .application.self_disclosure import (
    activity_disclosure_reply,
    analyze_activity_disclosures,
    apply_activity_disclosure,
    apply_style_to_latest_activity_context,
)
from .application.semantic_memory import (
    interpret_semantic_memory_candidates,
    save_semantic_memory_candidates,
    semantic_profile_fact,
)
from .application.time_slot_recommendation import (
    build_time_slot_recommendation_proposal,
    is_time_slot_recommendation_request,
    replay_time_slot_recommendation,
)
from .domain.session import normalize_pm_session_id
from .domain.types import PMAction, PMExtraction, PMGraphState, PMIntent, PMPlanExtraction, PMTaskExtraction
from .executors.dispatcher import execute_pm_action
from .extractors.intent import _looks_like_injection, _safe_for_react_fallback
from .persistence.control_store import create_approval_request, pm_db_path, record_audit_event
from .persistence.decision_log import TurnDecision
from .persistence.recent_context import list_recent_context

# Re-export public surface so callers that import from workflow still work
__all__ = [
    "run_typed_pm_turn",
    "extract_pm_plan",
    "extract_structured_pm_request",
    "approve_pm_request",
    "reject_pm_request",
    "approve_from_chat",
    "reject_from_chat",
    "normalize_pm_session_id",
    "execute_pm_action",
    "pm_db_path",
    "PMAction",
    "PMExtraction",
    "PMIntent",
    "PMPlanExtraction",
    "PMTaskExtraction",
    "_extract_pm_plan_with_model",
    "_extract_pm_request_with_model",
]


_extract_pm_plan_with_model = _extraction._extract_pm_plan_with_model
_extract_pm_request_with_model = _extraction._extract_pm_request_with_model


def _sync_extraction_hooks() -> None:
    # Tests and older integrations monkeypatch these names on workflow.py.
    _extraction._extract_pm_plan_with_model = _extract_pm_plan_with_model
    _extraction._extract_pm_request_with_model = _extract_pm_request_with_model


def _sync_approval_hooks() -> None:
    # Keep approval execution monkeypatch-compatible after moving approval flow.
    _approval_flow.execute_pm_action = execute_pm_action


def extract_pm_plan(message: str, config: Optional[Any] = None) -> PMPlanExtraction:
    _sync_extraction_hooks()
    return _extraction.extract_pm_plan(message, config)


def extract_structured_pm_request(message: str, config: Optional[Any] = None) -> PMExtraction:
    _sync_extraction_hooks()
    return _extraction.extract_structured_pm_request(message, config)


def approve_pm_request(
    approval_id: str,
    data_dir: str,
    *,
    vault_dir: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> str:
    _sync_approval_hooks()
    effective_sid = user_id or session_id
    return _approval_flow.approve_pm_request(
        approval_id,
        data_dir,
        vault_dir=vault_dir,
        session_id=effective_sid,
    )


def reject_pm_request(
    approval_id: str,
    data_dir: str,
    *,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> str:
    effective_sid = user_id or session_id
    return _approval_flow.reject_pm_request(approval_id, data_dir, session_id=effective_sid)


def approve_from_chat(message: str, config: Any) -> str:
    _sync_approval_hooks()
    return _approval_flow.approve_from_chat(message, config)


def reject_from_chat(message: str, config: Any) -> str:
    return _approval_flow.reject_from_chat(message, config)


def run_typed_pm_turn(message: str, config: Any) -> Optional[str]:
    """Handle deterministic PM workflows. Return None only for safe fallback turns."""
    thread_id = normalize_pm_session_id(config.session_id)
    # uid (user_id) owns user-level data; thread_id scopes pending/working-memory state.
    uid = getattr(config, "user_id", "") or thread_id
    d = TurnDecision(session_id=thread_id, message_preview=message)

    try:
        return _run_typed_pm_turn_inner(message, config, uid, thread_id, d)
    finally:
        d.persist(config.data_dir, user_id=uid)


def _run_typed_pm_turn_inner(message: str, config: Any, sid: str, thread_id: str, d: TurnDecision) -> Optional[str]:
    if _looks_like_injection(message):
        d.route("unsafe_injection", "message contains prompt-injection markers")
        d.wm_after = "none"
        reply = (
            "I can't take instructions embedded inside a user message. "
            "Please rephrase in your own words."
        )
        d.set_reply(reply)
        return reply

    pending = _load_pending(thread_id, config.data_dir, user_id=sid)
    if pending is not None:
        d.set_working_memory(pending)

    if pending and pending.get("type") == "field_choices" and is_time_slot_recommendation_request(message):
        _clear_pending(thread_id, config.data_dir, user_id=sid, status="replaced")
        d.wm_outcome = "replaced_new_request"
        pending = None

    plan: PMPlanExtraction | None = None
    effective_message = message
    extracted_for_guard: PMPlanExtraction | None = None
    _pending_was_active = pending is not None

    if pending and _is_pending_cancel_reply(message):
        _clear_pending(thread_id, config.data_dir, user_id=sid, status="cancelled")
        record_audit_event(
            thread_id,
            config.data_dir,
            user_id=sid,
            event_type="working_memory_cancelled",
            intent="DIALOGUE",
            payload_summary={"message": message},
            result_summary="Cancelled pending request",
        )
        d.wm_outcome = "cancelled"
        d.route("cancel", "user cancelled pending request")
        d.wm_after = "cancelled"
        reply = "Got it — cancelled that pending request."
        d.set_reply(reply)
        return reply

    if pending and pending.get("type") == "disambiguation":
        selected = _match_pending_disambiguation_candidate(pending, message)
        if selected is not None:
            parsed_date = str(pending.get("parsed_date") or "")
            parsed_start = str(pending.get("parsed_start") or "")
            built = build_contextual_plan_from_candidate(
                activity=str(selected.get("activity") or ""),
                label=str(selected.get("label") or ""),
                parsed_date=parsed_date,
                parsed_start=parsed_start,
            )
            if built is not None:
                new_plan, confirmation_summary, provenance = built
                _clear_pending(thread_id, config.data_dir, user_id=sid, status="resolved")
                _save_pending_confirmation(
                    thread_id,
                    config.data_dir,
                    new_plan,
                    user_id=sid,
                    blocking_task_id=new_plan.tasks[0].task_id,
                    confirmation_summary=confirmation_summary,
                    original_message=str(pending.get("original_message") or message),
                    provenance=provenance,
                )
                record_audit_event(
                    thread_id,
                    config.data_dir,
                    user_id=sid,
                    event_type="disambiguation_resolved",
                    intent=PMIntent.CREATE_SCHEDULE_EVENT.value,
                    payload_summary={"message": message, "selected": selected},
                    result_summary=confirmation_summary,
                )
                d.wm_outcome = "resolved"
                d.route("disambiguation_resolved", "user picked one of the disambiguation candidates")
                d.wm_after = "active:awaiting_confirmation"
                d.memory_written.append("working_memory:awaiting_confirmation")
                d.set_reply(confirmation_summary)
                return confirmation_summary

    # Check for a high-confidence new request *before* running the confirmation
    # editor. Otherwise "Schedule dentist appointment tomorrow at 9am" arriving
    # while a basketball draft is pending gets parsed as corrections to the
    # draft (retitle + retime) instead of a replacement.
    if pending:
        extracted_for_guard = extract_pm_plan(message, config)
        if _is_high_confidence_new_request(extracted_for_guard, message, pending):
            _clear_pending(thread_id, config.data_dir, user_id=sid, status="replaced")
            d.wm_outcome = "replaced_new_request"
            pending = None
            plan = extracted_for_guard
        elif _pending_is_stale(pending) and not _pending_message_is_dialogue_reply(message, pending):
            _clear_pending(thread_id, config.data_dir, user_id=sid, status="replaced")
            d.wm_outcome = "replaced_stale"
            pending = None

    if pending and pending.get("type") == "confirmation":
        confirmation = _apply_pending_confirmation_response(pending, message, config)
        if confirmation.status == "execute" and confirmation.plan is not None:
            plan = confirmation.plan
            pending = None
            d.wm_outcome = "resolved"
        elif confirmation.status == "refresh":
            d.wm_outcome = "kept"
            d.route("confirmation", "user corrected pending confirmation")
            d.wm_after = "active:awaiting_confirmation"
            d.memory_written.append("working_memory:awaiting_confirmation")
            d.set_reply(confirmation.reply)
            return confirmation.reply

    if (
        pending
        and pending.get("type") == "field_choices"
        and _is_something_else_reply(message)
    ):
        replay = replay_time_slot_recommendation(pending, config)
        if replay is not None:
            _save_pending_field_choices(
                thread_id,
                config.data_dir,
                replay.plan,
                user_id=sid,
                blocking_task_id=replay.blocking_task_id,
                missing=replay.missing,
                choices=replay.choices,
            )
            record_audit_event(
                thread_id,
                config.data_dir,
                user_id=sid,
                event_type="time_slot_recommendation_replay",
                intent=PMIntent.GENERAL_COACHING.value,
                payload_summary={"message": message},
                result_summary=replay.prompt,
            )
            d.wm_outcome = "replay"
            d.route("time_slot_replay", "user asked for a different recommendation")
            d.wm_after = "active:awaiting_choice"
            d.memory_written.append("working_memory:awaiting_choice")
            d.set_reply(replay.prompt)
            return replay.prompt

    if pending and _is_more_options_reply(message):
        _clear_pending(thread_id, config.data_dir, user_id=sid, status="replaced")
        d.wm_outcome = "more_options"
        d.route("more_options", "user asked for more options on pending choices")
        d.wm_after = "cancelled"
        reply = "Ask me again with the time and I'll generate a fresh set of options."
        d.set_reply(reply)
        return reply

    if pending and pending.get("type") != "confirmation" and _is_pending_clarification_response(message):
        pending_plan = _pending_payload_to_plan(pending)
        if pending_plan is not None:
            if pending.get("type") == "field_choices":
                plan = _apply_pending_field_choice_response(pending_plan, pending, message, config)
            else:
                plan = _apply_clarification_to_plan(
                    pending_plan,
                    str(pending.get("blocking_task_id", "")),
                    [str(item) for item in pending.get("missing", [])],
                    message,
                )
            d.wm_outcome = "resolved"
        elif _is_clarification_response(message):
            reconstructed = _reconstruct_with_pending(pending, message)
            if reconstructed:
                effective_message = reconstructed
            d.wm_outcome = "resolved"

    if _pending_was_active and d.wm_outcome == "none":
        d.wm_outcome = "kept"

    if plan is None:
        plan = extracted_for_guard if extracted_for_guard is not None and effective_message == message else extract_pm_plan(effective_message, config)

    plan = apply_context_to_explicit_schedule_plan(message, plan, sid, config.data_dir)

    d.set_plan(plan)

    state = PMGraphState(
        user_message=effective_message,
        session_id=sid,
    )
    state.plan_tasks = list(plan.tasks)
    if plan.tasks:
        first = plan.tasks[0]
        state.intent = first.intent
        state.entities = first.entities
        state.extraction_confidence = first.confidence
        state.extraction_source = plan.source
        state.missing_fields = list(first.missing_fields)
    record_audit_event(
        thread_id,
        config.data_dir,
        user_id=sid,
        event_type="classified",
        intent="PLAN" if len(plan.tasks) > 1 else state.intent.value,
        payload_summary={
            "message": message,
            "confidence": plan.confidence,
            "source": plan.source,
            "tasks": [
                {
                    "taskId": task.task_id,
                    "intent": task.intent.value,
                    "confidence": task.confidence,
                    "missing": task.missing_fields,
                }
                for task in plan.tasks
            ],
        },
    )

    self_disclosure_reply = _handle_self_disclosure_turn(message, plan, config, sid, d)
    if self_disclosure_reply is not None:
        return self_disclosure_reply

    style_reply = _handle_activity_style_followup(message, plan, config, sid, d)
    if style_reply is not None:
        return style_reply

    partial_activity_reply = _handle_activity_partial_schedule_followup(message, plan, config, sid, d)
    if partial_activity_reply is not None:
        return partial_activity_reply

    memory_query_reply = build_memory_query_reply(message, config, sid, str(config.data_dir))
    if memory_query_reply is not None:
        d.route("memory_query", "typed memory query")
        d.wm_after = "none"
        d.memory_read.extend(["semantic_memory", "preferences", "recent_context", "profile_md"])
        d.set_reply(memory_query_reply)
        return memory_query_reply

    semantic_memory_reply = _handle_semantic_memory_turn(message, plan, config, sid, d, thread_id=thread_id)
    if semantic_memory_reply is not None:
        return semantic_memory_reply

    contextual_proposal = (
        None
        if is_time_slot_recommendation_request(message)
        else build_contextual_schedule_proposal(message, plan, sid, config.data_dir)
    )
    if contextual_proposal is not None:
        if contextual_proposal.status == "confirm" and contextual_proposal.plan is not None:
            _save_pending_confirmation(
                thread_id,
                config.data_dir,
                contextual_proposal.plan,
                user_id=sid,
                blocking_task_id=contextual_proposal.blocking_task_id,
                confirmation_summary=contextual_proposal.reply,
                original_message=contextual_proposal.original_message or message,
                provenance=contextual_proposal.provenance or {},
                relevance=contextual_proposal.relevance,
            )
            record_audit_event(
                thread_id,
                config.data_dir,
                user_id=sid,
                event_type="contextual_schedule_confirmation",
                intent=PMIntent.CREATE_SCHEDULE_EVENT.value,
                payload_summary={
                    "message": message,
                    "provenance": contextual_proposal.provenance or {},
                    "relevance": contextual_proposal.relevance or {},
                },
                result_summary=contextual_proposal.reply,
            )
            d.route("confirmation", "contextual fragment produced schedule confirmation")
            d.wm_after = "active:awaiting_confirmation"
            d.memory_read.append("recent_context")
            d.memory_written.append("working_memory:awaiting_confirmation")
            d.set_reply(contextual_proposal.reply)
            return contextual_proposal.reply
        if (
            contextual_proposal.status == "disambiguate"
            and contextual_proposal.candidates
            and contextual_proposal.parsed_date
            and contextual_proposal.parsed_start
        ):
            _save_pending_disambiguation(
                thread_id,
                config.data_dir,
                user_id=sid,
                candidates=contextual_proposal.candidates,
                parsed_date=contextual_proposal.parsed_date,
                parsed_start=contextual_proposal.parsed_start,
                reply=contextual_proposal.reply,
                original_message=contextual_proposal.original_message or message,
            )
            d.route("disambiguate", "contextual fragment produced disambiguation prompt")
            d.wm_after = "active:awaiting_disambiguation"
            d.memory_read.append("recent_context")
            d.memory_written.append("working_memory:awaiting_disambiguation")
            d.set_reply(contextual_proposal.reply)
            return contextual_proposal.reply
        d.route("context_repair", "contextual fragment was ambiguous or non-committal")
        d.wm_after = "none"
        d.memory_read.append("recent_context")
        d.set_reply(contextual_proposal.reply)
        return contextual_proposal.reply

    # Approval/rejection ack requires pending state. This also covers composite
    # plans like "yes and move it later" (APPROVE + UPDATE_SCHEDULE) — without
    # pending state the trailing instruction has no referent, so we collapse
    # the whole turn to a single "no pending" reply rather than running the
    # generic clarifier on the second task.
    if plan.tasks and plan.tasks[0].intent in {PMIntent.APPROVE_ACTION, PMIntent.REJECT_ACTION}:
        from .parsing.text import _extract_id as _extract_approval_id
        from .persistence.control_store import list_approval_requests as _list_approval_requests
        has_explicit_id = bool(_extract_approval_id(message))
        _all_pending = _list_approval_requests(sid, config.data_dir, status="pending", limit=10)
        has_control_store_approval = any(
            a.payload.get("_thread_id") == thread_id for a in _all_pending
        )
        if not _pending_was_active and not has_control_store_approval and not has_explicit_id:
            d.route("clarify_no_pending_approval", f"{plan.tasks[0].intent.value} without pending confirmation or approval")
            d.wm_after = "none"
            verb = "approve" if plan.tasks[0].intent == PMIntent.APPROVE_ACTION else "decline"
            reply = f"There's no pending action to {verb} right now."
            d.set_reply(reply)
            return reply

    if len(plan.tasks) == 1 and state.intent == PMIntent.APPROVE_ACTION:
        d.route("approve", "intent is APPROVE_ACTION")
        d.wm_after = "none"
        reply = approve_from_chat(message, config)
        d.set_reply(reply)
        return reply

    if len(plan.tasks) == 1 and state.intent == PMIntent.REJECT_ACTION:
        d.route("reject", "intent is REJECT_ACTION")
        d.wm_after = "none"
        reply = reject_from_chat(message, config)
        d.set_reply(reply)
        return reply

    if len(plan.tasks) == 1 and state.intent in {PMIntent.UNKNOWN, PMIntent.GENERAL_COACHING}:
        recommendation = build_time_slot_recommendation_proposal(message, config)
        if recommendation is not None:
            _save_pending_field_choices(
                thread_id,
                config.data_dir,
                recommendation.plan,
                user_id=sid,
                blocking_task_id=recommendation.blocking_task_id,
                missing=recommendation.missing,
                choices=recommendation.choices,
            )
            record_audit_event(
                thread_id,
                config.data_dir,
                user_id=sid,
                event_type="time_slot_recommendation",
                intent=state.intent.value,
                payload_summary={"message": message},
                result_summary=recommendation.prompt,
            )
            d.route("time_slot", "time-slot recommendation for coaching/unknown intent")
            d.wm_after = "active:awaiting_choice"
            d.memory_written.append("working_memory:awaiting_choice")
            d.set_reply(recommendation.prompt)
            return recommendation.prompt
        if _safe_for_react_fallback(message, state.intent):
            d.route("fallback", "unknown/coaching intent, safe for react fallback")
            return None

    if not plan.tasks:
        recommendation = build_time_slot_recommendation_proposal(message, config)
        if recommendation is not None:
            _save_pending_field_choices(
                thread_id,
                config.data_dir,
                recommendation.plan,
                user_id=sid,
                blocking_task_id=recommendation.blocking_task_id,
                missing=recommendation.missing,
                choices=recommendation.choices,
            )
            record_audit_event(
                thread_id,
                config.data_dir,
                user_id=sid,
                event_type="time_slot_recommendation",
                intent=PMIntent.GENERAL_COACHING.value,
                payload_summary={"message": message},
                result_summary=recommendation.prompt,
            )
            d.route("time_slot", "time-slot recommendation, no tasks extracted")
            d.wm_after = "active:awaiting_choice"
            d.memory_written.append("working_memory:awaiting_choice")
            d.set_reply(recommendation.prompt)
            return recommendation.prompt
        if _safe_for_react_fallback(message, PMIntent.UNKNOWN):
            d.route("fallback", "no tasks extracted, safe for react fallback")
            return None
        d.route("clarification", "no tasks extracted, not safe for fallback")
        d.wm_after = "none"
        reply = (
            "I need a more specific task to do that — try asking me to schedule "
            "something, add a habit, or take a note."
        )
        d.set_reply(reply)
        return reply

    if plan.global_missing_fields:
        first_task = plan.tasks[0] if plan.tasks else None
        proposal = (
            build_field_completion_proposal(plan, first_task, list(plan.global_missing_fields), config)
            if first_task is not None
            else None
        )
        d.blocker_type = "missing_fields"
        d.blocker_missing = list(plan.global_missing_fields)
        if proposal is not None:
            d.set_fc_candidates(proposal.choices)
            state.final_reply = proposal.prompt
            _save_pending_field_choices(
                thread_id,
                config.data_dir,
                plan,
                user_id=sid,
                blocking_task_id=first_task.task_id,
                missing=list(plan.global_missing_fields),
                choices=proposal.choices,
            )
            d.route("field_choices", "global missing fields, choices generated")
            d.wm_after = "active:awaiting_choice"
            d.memory_written.append("working_memory:awaiting_choice")
        else:
            missing_str = " and ".join(plan.global_missing_fields) if len(plan.global_missing_fields) <= 2 else ", ".join(plan.global_missing_fields[:-1]) + f", and {plan.global_missing_fields[-1]}"
            state.final_reply = f"I'll need {missing_str} to do that."
            _save_pending_plan(
                thread_id,
                config.data_dir,
                plan,
                user_id=sid,
                blocking_task_id=plan.tasks[0].task_id if plan.tasks else "",
                missing=list(plan.global_missing_fields),
            )
            d.route("clarification", "global missing fields, no choices generated")
            d.wm_after = "active:awaiting_clarification"
            d.memory_written.append("working_memory:awaiting_clarification")
        record_audit_event(
            thread_id,
            config.data_dir,
            user_id=sid,
            event_type="field_choices" if proposal is not None else "clarification",
            intent="PLAN",
            payload_summary=plan.global_missing_fields,
            result_summary=state.final_reply,
        )
        d.set_reply(state.final_reply)
        return state.final_reply

    blocker = _find_plan_blocker(plan, config)
    if blocker is not None:
        d.blocker_missing = blocker.missing
        if blocker.is_lookup_failure:
            state.final_reply = blocker.question
            # Event/item not found — clear any stale pending state so the next message
            # starts fresh rather than looping through the same failed lookup.
            _clear_pending(thread_id, config.data_dir, user_id=sid)
            d.blocker_type = "lookup_failure"
            d.route("lookup_error", "event/item not found in lookup")
            d.wm_after = "none"
        else:
            proposal = build_field_completion_proposal(plan, blocker.task, blocker.missing, config)
            d.blocker_type = "missing_fields"
            if proposal is not None:
                d.set_fc_candidates(proposal.choices)
                state.final_reply = proposal.prompt
                _save_pending_field_choices(
                    thread_id,
                    config.data_dir,
                    plan,
                    user_id=sid,
                    blocking_task_id=blocker.task.task_id,
                    missing=blocker.missing,
                    choices=proposal.choices,
                )
                d.route("field_choices", "task-level missing fields, choices generated")
                d.wm_after = "active:awaiting_choice"
                d.memory_written.append("working_memory:awaiting_choice")
            else:
                state.final_reply = blocker.question
                _save_pending_plan(
                    thread_id,
                    config.data_dir,
                    plan,
                    user_id=sid,
                    blocking_task_id=blocker.task.task_id,
                    missing=blocker.missing,
                )
                d.route("clarification", "task-level missing fields, no choices generated")
                d.wm_after = "active:awaiting_clarification"
                d.memory_written.append("working_memory:awaiting_clarification")
        record_audit_event(
            thread_id,
            config.data_dir,
            user_id=sid,
            event_type="lookup_clarification" if blocker.is_lookup_failure else (
                "field_choices" if state.final_reply != blocker.question else "clarification"
            ),
            intent=blocker.task.intent.value,
            payload_summary={
                "message": message,
                "confidence": blocker.task.confidence,
                "entities": blocker.task.entities,
                "missing": blocker.missing,
            },
            result_summary=state.final_reply,
        )
        d.set_reply(state.final_reply)
        return state.final_reply

    _clear_pending(thread_id, config.data_dir, user_id=sid)
    d.route("executed", "no blockers, plan executing")
    d.wm_after = "none"
    state.final_reply = _execute_pm_plan(plan, config, sid, thread_id=thread_id)
    d.set_reply(state.final_reply)
    return state.final_reply


def _is_high_confidence_new_request(
    plan: PMPlanExtraction,
    message: str,
    pending: dict[str, Any],
) -> bool:
    if plan.confidence < 0.75 or not plan.tasks:
        return False
    first = plan.tasks[0]
    if first.intent in {PMIntent.UNKNOWN, PMIntent.GENERAL_COACHING}:
        return False
    if _pending_message_is_dialogue_reply(message, pending):
        return False
    return True


def _handle_self_disclosure_turn(
    message: str,
    plan: PMPlanExtraction,
    config: Any,
    session_id: str,
    d: TurnDecision,
) -> str | None:
    if _is_explicit_memory_request(message):
        return None
    disclosures = analyze_activity_disclosures(message)
    if not disclosures:
        return None
    if plan.tasks:
        allowed_intents = {PMIntent.SAVE_MEMORY, PMIntent.UNKNOWN, PMIntent.GENERAL_COACHING}
        if any(task.intent not in allowed_intents for task in plan.tasks):
            return None
    written: list[str] = []
    for disclosure in disclosures:
        written.extend(apply_activity_disclosure(disclosure, config, session_id, str(config.data_dir)))
    d.route("self_disclosure", "passive activity disclosure")
    d.wm_after = "none"
    d.memory_written.extend(written)
    reply = activity_disclosure_reply(disclosures)
    d.set_reply(reply)
    return reply


def _handle_semantic_memory_turn(
    message: str,
    plan: PMPlanExtraction,
    config: Any,
    session_id: str,
    d: TurnDecision,
    *,
    thread_id: str = "",
) -> str | None:
    """Persist model/deterministic semantic memories for non-action turns."""
    if _is_explicit_memory_request(message):
        return None
    if plan.tasks:
        allowed_intents = {PMIntent.UNKNOWN, PMIntent.GENERAL_COACHING}
        if any(task.intent not in allowed_intents for task in plan.tasks):
            return None
    candidates = interpret_semantic_memory_candidates(message, config)
    if not candidates:
        return None
    saved = save_semantic_memory_candidates(session_id, str(config.data_dir), candidates)
    if not saved:
        return None
    fact = semantic_profile_fact(candidates, message)
    if getattr(config, "vault_dir", None):
        result = execute_pm_action(PMAction("remember", {"fact": fact}), config)
        if result.get("ok"):
            d.memory_written.append("profile_md")
    d.route("semantic_memory", "semantic memory interpreter saved low-risk memory")
    d.wm_after = "none"
    d.memory_written.append("semantic_memory")
    record_audit_event(
        thread_id or session_id,
        config.data_dir,
        user_id=session_id if thread_id else "",
        event_type="semantic_memory_saved",
        intent=PMIntent.SAVE_MEMORY.value,
        payload_summary={
            "message": message,
            "candidates": [
                {
                    "memory_type": candidate.memory_type,
                    "predicate": candidate.predicate,
                    "object": candidate.object,
                    "confidence": candidate.confidence,
                    "source": candidate.source,
                }
                for candidate in candidates
            ],
        },
        result_summary=f"Saved {len(saved)} semantic memories",
    )
    reply = _semantic_memory_saved_reply(saved)
    d.set_reply(reply)
    return reply


def _semantic_memory_saved_reply(saved: list[Any]) -> str:
    if (
        len(saved) == 1
        and getattr(saved[0], "source", "") == "deterministic_memory_interpreter"
        and str(getattr(saved[0], "evidence", "")).strip()
    ):
        return f"Remembered: {str(saved[0].evidence).strip()}"
    labels = [str(record.object).title() for record in saved if str(record.object).strip()]
    if not labels:
        return "Remembered."
    if len(labels) == 1:
        return f"Remembered: {labels[0]}."
    if len(labels) == 2:
        return f"Remembered: {labels[0]} and {labels[1]}."
    return f"Remembered: {', '.join(labels[:-1])}, and {labels[-1]}."


def _handle_activity_style_followup(
    message: str,
    plan: PMPlanExtraction,
    config: Any,
    session_id: str,
    d: TurnDecision,
) -> str | None:
    if _is_explicit_memory_request(message):
        return None
    if not _plan_allows_activity_context_reply(plan):
        return None
    contexts = list_recent_context(session_id, str(config.data_dir), context_type="activity_topic")
    if not contexts:
        return None
    reply = apply_style_to_latest_activity_context(message, contexts, config, session_id, str(config.data_dir))
    if reply is None:
        return None
    d.route("activity_context", "style follow-up updated recent activity context")
    d.wm_after = "none"
    d.memory_read.append("recent_context")
    d.memory_written.append("recent_context:activity_topic")
    d.set_reply(reply)
    return reply


def _handle_activity_partial_schedule_followup(
    message: str,
    plan: PMPlanExtraction,
    config: Any,
    session_id: str,
    d: TurnDecision,
) -> str | None:
    if not _plan_allows_activity_context_reply(plan):
        return None
    lower = message.strip().lower()
    if not any(marker in lower for marker in ("next week", "this week", "weekend", "tomorrow", "today")):
        return None
    contexts = list_recent_context(session_id, str(config.data_dir), context_type="activity_topic")
    if not contexts:
        return None
    label = str(contexts[0].payload.get("activity_label") or contexts[0].payload.get("activity") or "that")
    reply = f"Sure — what day and time should I use for {label.lower()}?"
    d.route("activity_context", "partial schedule follow-up needs day/time")
    d.wm_after = "none"
    d.memory_read.append("recent_context")
    d.set_reply(reply)
    return reply


def _plan_allows_activity_context_reply(plan: PMPlanExtraction) -> bool:
    if not plan.tasks:
        return True
    if len(plan.tasks) != 1:
        return False
    return plan.tasks[0].intent in {PMIntent.SAVE_MEMORY, PMIntent.UNKNOWN, PMIntent.GENERAL_COACHING}


def _is_explicit_memory_request(message: str) -> bool:
    lower = message.strip().lower()
    return lower.startswith("remember ") or lower.startswith("remember that ") or "export" in lower


def _execute_pm_plan(plan: PMPlanExtraction, config: Any, session_id: str, *, thread_id: str = "") -> str:
    results: list[tuple[str, str]] = []
    single_action_plan = len(plan.tasks) == 1
    _tid = thread_id or session_id
    _uid = session_id if thread_id else ""
    for step_idx, task in enumerate(plan.tasks, start=1):
        entities = {**task.entities, "_confidence": task.confidence}
        actions = plan_pm_actions(task.intent, entities, config)
        for action in actions:
            guarded = apply_approval_policy(action)
            summary = guarded.summary or action.summary or guarded.action_type
            if guarded.requires_approval:
                approval_payload = {**guarded.payload, "_thread_id": _tid}
                approval = create_approval_request(
                    session_id,
                    config.data_dir,
                    action_type=guarded.action_type,
                    payload=approval_payload,
                    summary=summary,
                    risk_level=guarded.risk_level,
                )
                record_audit_event(
                    _tid,
                    config.data_dir,
                    user_id=_uid,
                    event_type="approval_created",
                    intent=task.intent.value,
                    action_type=guarded.action_type,
                    payload_summary=summary,
                    approval_id=approval.id,
                )
                results.append((summary, _format_approval_prompt(approval)))
                continue

            result = execute_pm_action(guarded, config)
            record_audit_event(
                _tid,
                config.data_dir,
                user_id=_uid,
                event_type="action_executed" if result["ok"] else "action_failed",
                intent=task.intent.value,
                action_type=guarded.action_type,
                payload_summary=summary or guarded.payload,
                result_summary=result["message"],
            )
            results.append((summary, result["message"]))
            if not result["ok"]:
                if single_action_plan and len(results) == 1:
                    return result["message"]
                return _format_plan_results(results, failure_step=step_idx)

    if single_action_plan and len(results) == 1:
        return results[0][1]
    return _format_plan_results(results)


def _format_plan_results(results: list[tuple[str, str]], *, failure_step: Optional[int] = None) -> str:
    heading = "I hit a problem partway through:" if failure_step is not None else "All done:"
    lines = [heading]
    for idx, (summary, reply) in enumerate(results, start=1):
        lines.append(f"{idx}. {summary}: {reply}")
    if failure_step is not None:
        lines.append(f"Stopped at step {failure_step} — later steps were not run.")
    return "\n".join(lines)
