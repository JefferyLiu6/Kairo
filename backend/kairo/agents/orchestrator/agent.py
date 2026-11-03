"""Orchestrator agent — main run loop and streaming entry point."""
from __future__ import annotations

import logging
import time
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from kairo.core.llm import ContractError, call_contract, to_payload
from kairo.core.llm_env import build_llm
from kairo.core.observability.tracing import mark_contract_error, mark_fallback, tag_turn, traced
from kairo.agents.personal_manager.agent import PMConfig, astream_pm
from kairo.core.domain.approval_response import has_pending_approval
from kairo.agents.personal_manager.application.approval_flow import approve_from_chat, reject_from_chat
from kairo.agents.personal_manager.application.pending_reply import OpenQuestion, approval_verb, load_open_question, pm_asked_a_question, route_message, still_open_after_error, still_open_reply
from kairo.core.domain.session import normalize_pm_session_id

from kairo.agents.orchestrator.contracts import HumanizeInput, PersonalizationSources, RouterInput, RouterOutput, TranslatorInput, TranslatorOutput
from kairo.core.observability.telemetry import emit, observed, span
from kairo.agents.orchestrator.memory import build_memory_context, get_working_memory, load_profile
from kairo.agents.orchestrator.prompts import HARNESS_SYSTEM as HARNESS_SYSTEM, HUMANIZER_SYSTEM, ORCHESTRATOR_SYSTEM, ROUTER_SYSTEM, TRANSLATOR_SYSTEM
from kairo.agents.orchestrator.router import is_likely_pm_needed
from kairo.agents.orchestrator.translator import StructuredAction, build_retry_prompt, is_safe_read_retry, unparsed_action
from kairo.agents.orchestrator.harness import HarnessVerdict, build_fallback_reply, describe_error, evaluate_harness, enforce_retry_policy, log_fallback

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorConfig:
    session_id: str           # chat thread identifier
    user_id: str = ""         # owner identity (from auth cookie)
    data_dir: str = "./data"
    vault_dir: Optional[str] = None
    # Orchestrator model (reasoning + humanization)
    provider: str = "openai"
    model: str = "gpt-4o"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    # PM agent model (fast, cheap execution)
    pm_provider: str = "openai"
    pm_model: str = "gpt-4o-mini"
    pm_api_key: Optional[str] = None
    pm_base_url: Optional[str] = None

    def pm_config(self) -> PMConfig:
        return PMConfig(
            user_id=self.user_id,
            provider=self.pm_provider,
            model=self.pm_model,
            api_key=self.pm_api_key,
            base_url=self.pm_base_url,
            data_dir=self.data_dir,
            vault_dir=self.vault_dir,
            session_id=self.session_id,
        )


def _llm(config: OrchestratorConfig, *, fast: bool = False):
    if fast:
        return build_llm(config.pm_provider, config.pm_model, config.pm_api_key, config.pm_base_url)
    return build_llm(config.provider, config.model, config.api_key, config.base_url)


# Not traced as its own run: LangChain already records the model call under its stage.
@observed("llm_invoke", trace=False)
def _invoke(llm: Any, system: str, user: str) -> str:
    response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    usage = getattr(response, "usage_metadata", None) or {}
    emit("llm_usage", input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))
    return str(response.content).strip()


def _invoker(llm: Any):
    """Bind a model to the `invoke(system, user)` shape the contract gateway expects."""
    return lambda system, user: _invoke(llm, system, user)


def _contract_failed(stage: str, exc: ContractError) -> None:
    """A stage reply stayed invalid after its repair attempt: record it, then fall back."""
    logger.warning("%s reply failed its contract: %s", stage, exc)
    emit("contract_error", source=stage)
    mark_contract_error(stage)


# ── Router ────────────────────────────────────────────────────────────────────

@observed("route")
def _route(message: str, memory_ctx: str, config: OrchestratorConfig) -> bool:
    """Return True if PM agent is needed. Uses heuristic first, LLM if ambiguous."""
    if is_likely_pm_needed(message):
        return True
    try:
        verdict = call_contract(
            _invoker(_llm(config, fast=True)),
            system=ROUTER_SYSTEM,
            data=RouterInput(message=message, context=memory_ctx),
            output=RouterOutput,
        )
    except ContractError as exc:
        _contract_failed("route", exc)
        return False  # no usable verdict: answer directly rather than touch data
    return verdict.needs_pm


# ── Translator ────────────────────────────────────────────────────────────────

@observed("translate")
def _translate(message: str, memory_ctx: str, config: OrchestratorConfig) -> StructuredAction:
    try:
        output = call_contract(
            _invoker(_llm(config, fast=True)),
            system=TRANSLATOR_SYSTEM,
            data=TranslatorInput(message=message, context=memory_ctx),
            output=TranslatorOutput,
        )
    except ContractError as exc:
        _contract_failed("translate", exc)
        return unparsed_action()
    return output.to_action()


# ── PM agent call (collects streamed tokens) ──────────────────────────────────

@observed("tool")
async def _call_pm(prompt: str, config: OrchestratorConfig) -> str:
    pm_cfg = config.pm_config()
    pm_cfg.session_id = config.session_id
    tokens: list[str] = []
    async for kind, value in astream_pm(prompt, pm_cfg):
        if kind == "token":
            tokens.append(value)
        elif kind == "done":
            return value
    return "".join(tokens)


# ── Harness ───────────────────────────────────────────────────────────────────

@observed("judge")
def _judge(
    message: str,
    action: StructuredAction,
    pm_output: str,
    profile: str,
    config: OrchestratorConfig,
) -> HarnessVerdict:
    def invoke(system, payload):
        llm = build_llm(
            os.getenv("JUDGE_PROVIDER", config.pm_provider),
            os.getenv("JUDGE_MODEL", config.pm_model),
            os.getenv("JUDGE_API_KEY") or config.pm_api_key,
            os.getenv("JUDGE_BASE_URL") or config.pm_base_url,
        )
        return _invoke(llm, system, payload)

    result, source = evaluate_harness(message, action, pm_output, profile, invoke)
    emit("judge_result", source=source, verdict=result.verdict)
    return result


# ── Humanizer ─────────────────────────────────────────────────────────────────

def _strip_json_blocks(text: str) -> str:
    """Remove all JSON objects/arrays from PM output using a brace-depth scanner."""
    import json as _json
    stripped = text.strip()
    # Entire output is valid JSON — discard completely
    try:
        _json.loads(stripped)
        return ""
    except (_json.JSONDecodeError, ValueError):
        pass

    # Scan character by character; skip any { ... } or [ ... ] block
    result: list[str] = []
    i = 0
    n = len(stripped)
    while i < n:
        ch = stripped[i]
        if ch in ('{', '['):
            close = '}' if ch == '{' else ']'
            depth = 0
            in_str = False
            j = i
            while j < n:
                c = stripped[j]
                if c == '\\' and in_str:
                    j += 2
                    continue
                if c == '"':
                    in_str = not in_str
                if not in_str:
                    if c == ch:
                        depth += 1
                    elif c == close:
                        depth -= 1
                        if depth == 0:
                            i = j + 1  # skip entire block
                            break
                j += 1
            else:
                # No matching close — keep character literally
                result.append(stripped[i])
                i += 1
        else:
            result.append(ch)
            i += 1

    return ''.join(result).strip()


@observed("humanize")
def _humanize(
    message: str,
    pm_output: str,
    memory_ctx: str,
    config: OrchestratorConfig,
) -> str:
    clean_output = _strip_json_blocks(pm_output)
    if not clean_output:
        # Existing orchestrator recovery handles this without replaying a write.
        raise ValueError("No readable PM result to humanize")
    from kairo.storage.store import _pm_dir
    profile = load_profile(_pm_dir(config.user_id, config.data_dir))
    wm = get_working_memory(config.user_id, config.session_id, config.data_dir)
    # Do not parse role prefixes from a flattened transcript or recycle assistant
    # personalization as evidence. Keep only actual user-role records.
    user_messages = [t["content"] for t in wm.turns if t.get("role") == "user"]
    payload = to_payload(HumanizeInput(
        user_message=message,
        pm_result=clean_output,
        personalization_sources=PersonalizationSources(profile=profile, user_messages=user_messages),
    ))
    return _invoke(_llm(config), HUMANIZER_SYSTEM, payload)


# ── Direct reply (no PM) ──────────────────────────────────────────────────────

@observed("direct_reply")
def _direct_reply(message: str, memory_ctx: str, config: OrchestratorConfig) -> str:
    llm = _llm(config)
    system = f"{ORCHESTRATOR_SYSTEM}\n\n## Current context\n{memory_ctx}"
    return _invoke(llm, system, message)


# ── Decision trace logger ─────────────────────────────────────────────────────

def _log_turn(
    config: OrchestratorConfig,
    message: str,
    route: str,
    route_reason: str,
    action: "StructuredAction | None",
    verdict: "HarnessVerdict | None",
    reply: str,
    started_at: float,
    retry_count: int = 0,
) -> None:
    emit("turn_result", outcome="fallback" if verdict and verdict.verdict != "pass" else "completed", retry_count=retry_count)
    try:
        from kairo.storage.decision_log import log_orchestrator_turn
        from kairo.core.domain.session import normalize_pm_session_id
        thread_id = normalize_pm_session_id(config.session_id)
        # Same thread/user keys as the PM's own decision row, so the trace view
        # (filtered by thread) shows the orchestrator row next to it.
        log_orchestrator_turn(
            thread_id,
            config.data_dir,
            user_id=config.user_id or thread_id,
            message=message,
            route=route,
            route_reason=route_reason,
            intent=action.intent if action else "direct",
            confidence=action.confidence if action else 1.0,
            pm_prompt=action.pm_prompt if action else "",
            is_write=action.is_write if action else False,
            harness_verdict=verdict.verdict if verdict else "n/a",
            harness_reason=verdict.reason if verdict else "",
            retry_count=retry_count,
            reply=reply,
            duration_ms=int((time.monotonic() - started_at) * 1000),
        )
    except Exception:
        pass


# ── Main streaming entry point ────────────────────────────────────────────────

def _open_question(config: OrchestratorConfig) -> OpenQuestion | None:
    """The question the PM is waiting on in this thread (pending_reply decides what it means)."""
    thread_id = normalize_pm_session_id(config.session_id)
    return load_open_question(thread_id, config.data_dir, user_id=config.user_id or thread_id)

async def _astream_orchestrator(
    message: str,
    config: OrchestratorConfig,
) -> AsyncIterator[tuple[str, str]]:
    """
    Yields (kind, value):
      ("progress", str) — status updates
      ("token",    str) — reply tokens
      ("done",     str) — full reply
    """
    _turn_start = time.monotonic()
    wm = get_working_memory(config.user_id, config.session_id, config.data_dir)
    # Per-user profile lives at data/users/<user_id>/PROFILE.md
    from kairo.storage.store import _pm_dir
    user_data_dir = _pm_dir(config.user_id, config.data_dir)
    memory_ctx = build_memory_context(config.user_id, config.session_id, user_data_dir, config.data_dir)
    profile = load_profile(user_data_dir)

    # ── Step 1: route ─────────────────────────────────────────────────────────
    yield ("progress", "Thinking…")
    question_before = _open_question(config)
    route = route_message(message, question_before)
    if route == "approval_command":
        # The approval service enforces ownership, thread scope and execute-once
        # semantics, and returns the persisted action result. Do not ask a model
        # to reinterpret that result (including failures and pending choices).
        verb = approval_verb(message)
        action = StructuredAction("approval", None, [], 1.0, message.strip().lower(), True)
        try:
            handler = approve_from_chat if verb == "approve" else reject_from_chat
            reply = handler(action.pm_prompt, config)
        except Exception:
            reply = "I couldn't verify the approval result. Check your calendar or task list before trying again."
        wm.invalidate_pm_cache()
        wm.add_turn("user", message)
        wm.add_turn("assistant", reply)
        _log_turn(config, message, "APPROVAL", "Approval service result", action, None, reply, _turn_start)
        yield ("token", reply)
        yield ("done", reply)
        return
    needs_pm = route == "answer_question" or _route(message, memory_ctx, config)

    if not needs_pm:
        # Direct reply
        yield ("progress", "Composing reply…")
        reply = _direct_reply(message, memory_ctx, config)
        wm.add_turn("user", message)
        wm.add_turn("assistant", reply)
        _log_turn(config, message, "DIRECT", "no PM data needed", None, None, reply, _turn_start)
        yield ("token", reply)
        yield ("done", reply)
        return

    # ── Step 2: translate ─────────────────────────────────────────────────────
    yield ("progress", "Working out what you need…")
    action = (
        StructuredAction("continue_request", None, [], 1.0, message, True)
        if route == "answer_question" else _translate(message, memory_ctx, config)
    )

    if action.needs_clarification():
        reply = _direct_reply(message, memory_ctx, config)
        wm.add_turn("user", message)
        wm.add_turn("assistant", reply)
        _log_turn(
            config,
            message,
            "DIRECT",
            "low confidence — asking for clarification",
            action,
            None,
            reply,
            _turn_start,
        )
        yield ("token", reply)
        yield ("done", reply)
        return

    # ── Step 3: call PM agent with retry loop ─────────────────────────────────
    MAX_RETRIES = 2
    current_action = action
    pm_output = ""
    final_verdict: HarnessVerdict | None = None
    retry_count = 0
    awaiting_details = False

    for attempt in range(MAX_RETRIES + 1):
        status = "Checking your data…" if attempt == 0 else f"Retrying… (attempt {attempt + 1})"
        yield ("progress", status)

        try:
            pm_output = await _call_pm(current_action.pm_prompt, config)
        except Exception as exc:
            pm_output = f"Error: {exc}"

        awaiting_details = pm_asked_a_question(pm_output, lambda: _open_question(config))

        # Pending approval is a valid workflow result, not a failed write.
        # Preserve the control instructions without another model interpreting them.
        verdict = (
            HarnessVerdict("pass", 1.0, "Awaiting user input; action not completed", "", "null")
            if has_pending_approval(pm_output) or awaiting_details
            else _judge(message, current_action, pm_output, profile, config)
        )
        verdict = enforce_retry_policy(current_action, verdict)
        emit("tool_verdict", verdict=verdict.verdict, retry_count=retry_count)
        final_verdict = verdict

        if verdict.verdict == "pass":
            # Cache successful reads
            if not awaiting_details and not current_action.is_write and current_action.cache_key():
                wm.cache_pm(current_action.cache_key(), pm_output)
            break

        if verdict.verdict == "retry" and attempt < MAX_RETRIES and verdict.suggested_fix:
            candidate = build_retry_prompt(current_action, verdict.suggested_fix)
            if not is_safe_read_retry(candidate):
                final_verdict = HarnessVerdict(
                    "fallback", 0.0, "Retry prompt is not a supported read; retry blocked", "", "read_failed",
                )
                emit("recovery", outcome="unsafe_read_retry_blocked")
                break
            current_action = candidate
            retry_count += 1
            continue

        # Use safe fallback for any non-pass verdict once retry options are exhausted.
        break

    # ── Step 4: invalidate cache on writes ────────────────────────────────────
    if action.is_write:
        wm.invalidate_pm_cache()

    # ── Step 5: build reply ───────────────────────────────────────────────────
    if final_verdict and final_verdict.verdict == "pass":
        yield ("progress", "Putting it together…")
        try:
            reply = pm_output if has_pending_approval(pm_output) or awaiting_details else _humanize(message, pm_output, memory_ctx, config)
            if not isinstance(reply, str) or not reply.strip():
                raise ValueError("Empty response")
        except Exception as exc:
            # Execution may already have committed. Formatting failure must never
            # replay the action or escape without an honest completion response.
            final_verdict = HarnessVerdict(
                "fallback", 0.0,
                f"Response generation unavailable ({describe_error(exc)})",
                "", "null",
            )
            emit("recovery", outcome="response_generation_failed")

    if final_verdict and final_verdict.verdict != "pass":
        cached = wm.get_cached_pm(action.cache_key()) if not action.is_write else None
        reply = build_fallback_reply(action, final_verdict, cached)
        log_fallback(
            config.data_dir,
            config.user_id or config.session_id,
            user_message=message,
            action=action,
            pm_output=pm_output,
            verdict=final_verdict,
            retry_count=retry_count,
            fallback_reply=reply,
        )
        still_open = still_open_after_error(question_before, pm_output, lambda: _open_question(config))
        pending_kept = still_open is not None
        if still_open is not None:
            reply = still_open_reply(still_open)
            final_verdict = HarnessVerdict(
                final_verdict.verdict, final_verdict.confidence,
                f"{final_verdict.reason}; pending choice kept, nothing executed",
                final_verdict.suggested_fix, final_verdict.failure_type,
            )
            emit("recovery", outcome="pending_kept_after_error")
        mark_fallback(
            failure_type=final_verdict.failure_type,
            intent=action.intent,
            retry_count=retry_count,
            pm_error=pm_output.strip().lower().startswith("error:"),
            pending_kept=pending_kept,
        )

    wm.add_turn("user", message)
    wm.add_turn("assistant", reply)
    _log_turn(
        config,
        message,
        "CLARIFICATION" if awaiting_details else "DELEGATE",
        f"intent={action.intent}",
        action,
        final_verdict,
        reply,
        _turn_start,
        retry_count,
    )
    yield ("token", reply)
    yield ("done", reply)


@traced("chat_turn")
async def _traced_turn(message: str, config: OrchestratorConfig):
    tag_turn(config)
    async for event in _astream_orchestrator(message, config):
        yield event


async def astream_orchestrator(message: str, config: OrchestratorConfig):
    with span("turn"):
        async for event in _traced_turn(message, config):
            yield event


def run_orchestrator(message: str, config: OrchestratorConfig) -> str:
    """Synchronous wrapper for non-streaming use (tests, scripts)."""
    import asyncio
    reply = ""

    async def _run():
        nonlocal reply
        async for kind, value in astream_orchestrator(message, config):
            if kind == "done":
                reply = value

    asyncio.run(_run())
    return reply
