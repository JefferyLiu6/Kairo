"""
Structured logging of ReAct / LangGraph agent steps (tool calls and results).

Logs to the ``assistant.agent`` logger at INFO so uvicorn / backend terminals
show what each agent did without changing API return types.

Set ``AGENT_TRACE=0`` to disable local file logging.

LangSmith
---------
Set ``LANGCHAIN_API_KEY`` to enable LangSmith cloud tracing.
LangChain/LangGraph picks it up automatically on every invocation — no explicit
callback needed. Call ``configure_langsmith()`` once at app startup.
Set ``LANGCHAIN_PROJECT`` to group traces (default: "kairo").
Set ``LANGSMITH_HIDE_INPUTS=true`` to redact sensitive tool inputs from traces.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

logger = logging.getLogger("assistant.agent")


# ── LangSmith ─────────────────────────────────────────────────────────────────

def configure_langsmith() -> None:
    """
    Enable LangSmith tracing when LANGCHAIN_API_KEY is present in env.

    Safe to call multiple times — idempotent. Call once at app startup
    (e.g. in the FastAPI @app.on_event("startup") handler).

    LangChain/LangGraph auto-injects the tracing callback on every chain/graph
    run once LANGCHAIN_TRACING_V2=true is set — no manual callback wiring needed.
    """
    api_key = os.environ.get("LANGCHAIN_API_KEY", "").strip()
    if not api_key:
        logger.info("LangSmith: disabled (LANGCHAIN_API_KEY not set)")
        return

    # LangChain reads these env vars on every invocation
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    project = os.environ.setdefault("LANGCHAIN_PROJECT", "kairo")
    logger.info("LangSmith: tracing enabled — project=%s", project)


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
    Configures local agent logger and enables LangSmith if API key is present.
    """
    lg = logging.getLogger("assistant.agent")
    if not lg.handlers:
        lg.setLevel(logging.INFO)
    lg.propagate = True
    configure_langsmith()
