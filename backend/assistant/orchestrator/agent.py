"""Orchestrator agent — main run loop and streaming entry point."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from assistant.shared.llm_env import build_llm
from assistant.personal_manager.agent import PMConfig, astream_pm

from .memory import build_memory_context, get_working_memory, load_profile
from .prompts import (
    HARNESS_SYSTEM,
    HUMANIZER_SYSTEM,
    ORCHESTRATOR_SYSTEM,
    ROUTER_SYSTEM,
    TRANSLATOR_SYSTEM,
)
from .router import is_likely_pm_needed, parse_router_verdict
from .translator import StructuredAction, build_retry_prompt, parse_translator_response
from .harness import (
    HarnessVerdict,
    build_fallback_reply,
    fast_precheck,
    log_fallback,
    parse_harness_verdict,
)


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


def _invoke(llm: Any, system: str, user: str) -> str:
    response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    return str(response.content).strip()


# ── Router ────────────────────────────────────────────────────────────────────

def _route(message: str, memory_ctx: str, config: OrchestratorConfig) -> bool:
    """Return True if PM agent is needed. Uses heuristic first, LLM if ambiguous."""
    if is_likely_pm_needed(message):
        return True
    llm = _llm(config, fast=True)
    user_prompt = f"User message: {message}\n\nContext:\n{memory_ctx}"
    raw = _invoke(llm, ROUTER_SYSTEM, user_prompt)
    verdict = parse_router_verdict(raw)
    return bool(verdict.get("needs_pm", False))


# ── Translator ────────────────────────────────────────────────────────────────

def _translate(message: str, memory_ctx: str, config: OrchestratorConfig) -> StructuredAction:
    llm = _llm(config, fast=True)
    user_prompt = f"User message: {message}\n\nContext:\n{memory_ctx}"
    raw = _invoke(llm, TRANSLATOR_SYSTEM, user_prompt)
    return parse_translator_response(raw)


# ── PM agent call (collects streamed tokens) ──────────────────────────────────

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

def _judge(
    message: str,
    action: StructuredAction,
    pm_output: str,
    profile: str,
    config: OrchestratorConfig,
) -> HarnessVerdict:
    # Fast deterministic pre-check first
    quick = fast_precheck(action, pm_output)
    if quick is not None:
        return quick

    llm = _llm(config, fast=True)
    user_prompt = (
        f"User message: {message}\n"
        f"Intent: {action.intent}\n"
        f"PM output:\n{pm_output}\n\n"
        f"User profile:\n{profile or '(none)'}"
    )
    raw = _invoke(llm, HARNESS_SYSTEM, user_prompt)
    return parse_harness_verdict(raw)


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


def _humanize(
    message: str,
    pm_output: str,
    memory_ctx: str,
    config: OrchestratorConfig,
) -> str:
    llm = _llm(config)
    clean_output = _strip_json_blocks(pm_output)
    if not clean_output:
        result_section = "The PM agent processed the request but returned no readable output. Reply to the user's message based on context alone."
    else:
        result_section = clean_output
    user_prompt = (
        f"User message: {message}\n\n"
        f"PM agent result:\n{result_section}\n\n"
        f"Context:\n{memory_ctx}"
    )
    return _invoke(llm, HUMANIZER_SYSTEM, user_prompt)


# ── Direct reply (no PM) ──────────────────────────────────────────────────────

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
    try:
        from assistant.personal_manager.persistence.decision_log import log_orchestrator_turn
        log_orchestrator_turn(
            config.user_id or config.session_id,
            config.data_dir,
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

async def astream_orchestrator(
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
    wm = get_working_memory(config.user_id, config.session_id)
    # Per-user profile lives at data/users/<user_id>/PROFILE.md
    from assistant.personal_manager.persistence.store import _pm_dir
    user_data_dir = _pm_dir(config.user_id, config.data_dir)
    memory_ctx = build_memory_context(config.user_id, config.session_id, user_data_dir)
    profile = load_profile(user_data_dir)

    # ── Step 1: route ─────────────────────────────────────────────────────────
    yield ("progress", "Thinking…")
    needs_pm = _route(message, memory_ctx, config)

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
    action = _translate(message, memory_ctx, config)

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

    for attempt in range(MAX_RETRIES + 1):
        status = "Checking your data…" if attempt == 0 else f"Retrying… (attempt {attempt + 1})"
        yield ("progress", status)

        try:
            pm_output = await _call_pm(current_action.pm_prompt, config)
        except Exception as exc:
            pm_output = f"Error: {exc}"

        verdict = _judge(message, current_action, pm_output, profile, config)
        final_verdict = verdict

        if verdict.verdict == "pass":
            # Cache successful reads
            if not current_action.is_write and current_action.cache_key():
                wm.cache_pm(current_action.cache_key(), pm_output)
            break

        if verdict.verdict == "retry" and attempt < MAX_RETRIES and verdict.suggested_fix:
            current_action = build_retry_prompt(current_action, verdict.suggested_fix)
            retry_count += 1
            continue

        # Use safe fallback for any non-pass verdict once retry options are exhausted.
        break

    # ── Step 4: invalidate cache on writes ────────────────────────────────────
    if action.is_write:
        wm.invalidate_pm_cache()

    # ── Step 5: build reply ───────────────────────────────────────────────────
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
    else:
        yield ("progress", "Putting it together…")
        reply = _humanize(message, pm_output, memory_ctx, config)

    wm.add_turn("user", message)
    wm.add_turn("assistant", reply)
    _log_turn(
        config,
        message,
        "DELEGATE",
        f"intent={action.intent}",
        action,
        final_verdict,
        reply,
        _turn_start,
        retry_count,
    )
    yield ("token", reply)
    yield ("done", reply)


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
