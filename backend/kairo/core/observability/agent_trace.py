"""
Structured logging of ReAct / LangGraph agent steps (tool calls and results).

Logs to the ``kairo.agent`` logger at INFO so uvicorn / backend terminals
show what each agent did without changing API return types.

Set ``AGENT_TRACE=0`` to disable local file logging.

LangSmith
---------
Tracing is switched on only by ``LANGSMITH_TRACING=true``; see
``kairo.core.observability.tracing``. ``configure_langsmith()`` just reports the state.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

logger = logging.getLogger("kairo.agent")


# ── LangSmith ─────────────────────────────────────────────────────────────────

def configure_langsmith() -> None:
    """Log whether LangSmith tracing is on. Call once at app startup.

    Tracing used to switch itself on whenever LANGCHAIN_API_KEY was set. It is now
    opt-in through LANGSMITH_TRACING alone, so a stray key never starts sending
    user data to LangSmith.
    """
    from langsmith.utils import tracing_is_enabled

    if not tracing_is_enabled():
        logger.info("LangSmith: disabled (set LANGSMITH_TRACING=true to enable)")
        return
    project = os.environ.get("LANGSMITH_PROJECT") or os.environ.get("LANGCHAIN_PROJECT") or "default"
    hidden = os.environ.get("LANGSMITH_HIDE_INPUTS") == "true"
    logger.info("LangSmith: tracing enabled — project=%s hide_inputs=%s", project, hidden)


# ── Local trace logging ────────────────────────────────────────────────────────

def _trunc(text: str, max_len: int = 600) -> str:
    t = " ".join(text.split())
    if len(t) <= max_len:
        return t
    return t[: max_len - 3] + "..."


def _safe_tool_args(args: Any, max_len: int = 1200) -> str:
    try:
        if isinstance(args, str):
            return _trunc(args, max_len)
        s = json.dumps(args, ensure_ascii=False, default=str)
        return _trunc(s, max_len)
    except Exception:
        return _trunc(repr(args), max_len)


def log_react_trace(
    agent: str,
    messages: list[BaseMessage],
    *,
    session_id: Optional[str] = None,
) -> None:
    """
    Emit one INFO line per tool call and tool result found in the message list.
    Skips system prompts and only summarizes human turns briefly.

    This is the local fallback logger. LangSmith (when configured) captures the
    full structured trace automatically via LangChain's callback mechanism.
    """
    if os.environ.get("AGENT_TRACE", "1").lower() in ("0", "false", "no", "off"):
        return

    prefix = f"[{agent}]"
    if session_id:
        prefix += f" session={session_id}"

    for m in messages:
        if isinstance(m, SystemMessage):
            continue
        if isinstance(m, HumanMessage):
            logger.info("%s user_message %s", prefix, _trunc(str(m.content), 240))
            continue
        if isinstance(m, AIMessage):
            tcs = getattr(m, "tool_calls", None) or []
            for tc in tcs:
                name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "?")
                args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
                logger.info("%s tool_call %s %s", prefix, name, _safe_tool_args(args))
            if not tcs and (m.content or "").strip():
                logger.info("%s assistant_text %s", prefix, _trunc(str(m.content), 400))
            continue
        if isinstance(m, ToolMessage):
            name = m.name or "tool"
            logger.info("%s tool_result %s -> %s", prefix, name, _trunc(str(m.content), 800))
            continue

        logger.info("%s message %s", prefix, _trunc(str(getattr(m, "content", m)), 200))


def ensure_agent_trace_logging() -> None:
    """
    Call once at app startup.
    Configures the local agent logger and reports LangSmith status.
    """
    lg = logging.getLogger("kairo.agent")
    if not lg.handlers:
        lg.setLevel(logging.INFO)
    lg.propagate = True
    configure_langsmith()
