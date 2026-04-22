"""
Kairo LangGraph agent — chief-of-staff assistant.

State-changing work is handled by the typed workflow in workflow.py. The
LangGraph fallback is intentionally tool-free and is reserved for low-risk
coaching/general conversation.

Session persistence
-------------------
Sync  (run_pm):      SqliteSaver checkpointer  — used by master agent tool + HTTP endpoint
Async (astream_pm):  AsyncSqliteSaver          — used for direct token-streaming over HTTP/SSE

Both target the same checkpoints.db; WAL mode lets them coexist safely.
thread_id == session_id so each session is an isolated conversation thread.

Observability
-------------
LangSmith tracing is auto-enabled via LANGCHAIN_TRACING_V2 + LANGCHAIN_API_KEY
(set by configure_langsmith() at app startup). run_name + tags on every
RunnableConfig give clean trace names in the LangSmith UI.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.prebuilt import create_react_agent

from assistant.shared.agent_trace import log_react_trace
from assistant.shared.llm_env import build_llm, has_api_key, with_retry
from assistant.shared.usage import UsageTracker
from .agent_tools import _build_tools as _build_tools
from .application.validators import _contains_sensitive_terms
from .executors.memory import execute_memory_action
from .extractors.intent import _is_self_disclosure
from .domain.types import PMAction
from .parsing.text import _norm
from .calendar.service import format_google_calendar_for_context
from .calendar.service import CalendarService
from .persistence.habits import format_habits_for_context
from .persistence.profile import read_profile
from .persistence.store import (
    _pm_dir,
    format_schedule_for_context,
    format_todos_for_context,
    load_private,
)
from .domain.session import normalize_pm_session_id
from .persistence.decision_log import TurnDecision
from .workflow import run_typed_pm_turn

_SYSTEM_PROMPT_VERSION = "pm-v2"

_SYSTEM_PROMPT = """\
You are a personal chief-of-staff assistant — a trusted EA and life-management partner.

Your responsibilities: scheduling, personal preferences, habits, goals, active plans,
private notes, journal, and supportive conversation.

## Confidentiality (highest priority)
- Sensitive information → store with manager_private (default when unsure)
- Non-sensitive preferences (e.g., "prefers dark mode") → use remember (shared with default chat)
- Working notes not sensitive → manager_note
- Journal entries (reflections, feelings, private thoughts) → manager_journal
- NEVER include raw private details in a web_search query. Minimise what you leak externally.

## Calendar rule
For ANY time-based request (add event, reschedule, remove event) you MUST call
manager_schedule — never return a markdown-only reply.

## Todo rule
For ANY task/todo request (add task, mark done, remove task, show tasks) you MUST call
manager_todo — never return a markdown-only reply.

## Habit rule
For ANY habit/routine request (add habit, check in, show streak, list habits) you MUST call
manager_habit — never return a markdown-only reply.

## Journal rule
For ANY journal request (write entry, log reflection, read journal) you MUST call
manager_journal — never return a markdown-only reply.

## State classification
| Sensitive (manager_private) | Non-sensitive (remember)  | Journal (manager_journal)  |
|-----------------------------|---------------------------|---------------------------|
| Medical, financial, legal   | Tool/app preferences      | Daily reflections          |
| Relationship details        | Communication style prefs | Mood / feelings            |
| Private goals/fears         | Public interests/hobbies  | Private thoughts           |
| Daily routines              | Language/format prefs     | End-of-day reviews         |

## Emotional support
You are a supportive coach and EA — not a therapist. Validate feelings and offer
concrete next steps. For crisis situations, refer to professional resources.

## Response style
Concise and action-oriented. When you update state, confirm what changed.
"""

_FALLBACK_SYSTEM_PROMPT = """\
You are a personal chief-of-staff assistant in conversation-only mode.

You can help the user think through priorities, emotions, tradeoffs, routines,
and next steps. You cannot call tools, change saved state, search the web, export
private data, or write to memory from this mode.

## Self-disclosure rule (highest priority)
When the user shares a personal preference, interest, opinion, or fact about
themselves without asking a question — give a brief, natural acknowledgement
(one short sentence at most, e.g. "Nice." / "Got it." / "Duly noted.") and
nothing more. Do NOT ask follow-up questions, probe for more details, or invite
elaboration. The user is stating something, not opening a dialogue about it.

## Action requests
If the user asks to add, edit, remove, remember, search, export, schedule, track,
or save something: do NOT ask clarifying follow-up questions about missing details.
Instead, in one message tell the user to rephrase with all required fields and give
a concrete example. For schedule creation the required fields are event name + date
+ time — e.g. "schedule dentist appointment on Friday at 4pm".

Use the provided context only to support the answer. Do not dump raw private
context unless the user specifically asks for it. Keep replies concise.
"""


@dataclasses.dataclass
class PMConfig:
    provider: str = "openai"
    model: str = "gpt-oss:120b"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    data_dir: str = "./data"
    vault_dir: Optional[str] = None
    session_id: str = "default"
    on_progress: Optional[Callable[[str], None]] = None


# ── Path helpers ───────────────────────────────────────────────────────────────

def _pm_db_path(session_id: str, data_dir: str) -> str:
    return os.path.join(_pm_dir(session_id, data_dir), "pm.db")


def _checkpoints_db_path(data_dir: str) -> str:
    path = os.path.join(data_dir, "personal-manager", "checkpoints.db")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


# ── Sync checkpointer singleton (SqliteSaver) ──────────────────────────────────

_sync_checkpointer: Optional[SqliteSaver] = None
_sync_checkpointer_lock = threading.Lock()


def _get_checkpointer(data_dir: str) -> SqliteSaver:
    global _sync_checkpointer
    with _sync_checkpointer_lock:
        if _sync_checkpointer is None:
            conn = sqlite3.connect(_checkpoints_db_path(data_dir), check_same_thread=False)
            _sync_checkpointer = SqliteSaver(conn)
        return _sync_checkpointer


# ── Async checkpointer singleton (AsyncSqliteSaver) ───────────────────────────

_async_checkpointer: Optional[Any] = None  # AsyncSqliteSaver — imported lazily


async def _get_async_checkpointer(data_dir: str) -> Any:
    """
    Lazy singleton for AsyncSqliteSaver.
    Shares the same checkpoints.db as the sync checkpointer; WAL mode allows both.
    """
    global _async_checkpointer
    if _async_checkpointer is None:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        conn = await aiosqlite.connect(_checkpoints_db_path(data_dir))
        await conn.execute("PRAGMA journal_mode=WAL")
        checkpointer = AsyncSqliteSaver(conn)
        await checkpointer.setup()
        _async_checkpointer = checkpointer
    return _async_checkpointer


# ── Context block (shared by run_pm and astream_pm) ───────────────────────────

def _build_context_block(config: PMConfig) -> str:
    now = datetime.now(timezone.utc).astimezone()
    date_ctx = (
        f"## Current date/time\n"
        f"Today: {now.strftime('%A, %B %d, %Y')}  "
        f"Time: {now.strftime('%H:%M %Z')}  "
        f"ISO: {now.isoformat()}"
    )
    google_primary = False
    try:
        google_primary = CalendarService(config.session_id, config.data_dir).has_google_account()
    except Exception:
        google_primary = False
    schedule_ctx = (
        format_google_calendar_for_context(config.session_id, config.data_dir)
        if google_primary
        else format_schedule_for_context(config.session_id, config.data_dir)
    )
    google_schedule_ctx = "" if google_primary else format_google_calendar_for_context(config.session_id, config.data_dir)
    todos_ctx = format_todos_for_context(config.session_id, config.data_dir)
    private_meta = load_private(config.session_id, config.data_dir)
    habits_ctx = format_habits_for_context(_pm_db_path(config.session_id, config.data_dir))
    profile_ctx = ""
    if config.vault_dir:
        raw = read_profile(config.vault_dir).strip()
        if raw:
            profile_ctx = f"## User profile (long-term memory)\n{raw}"
    return (
        f"{date_ctx}\n\n"
        f"{profile_ctx}\n\n"
        f"{schedule_ctx}\n\n"
        f"{google_schedule_ctx}\n\n"
        f"{todos_ctx}\n\n"
        f"{habits_ctx}\n\n"
        f"## Private context\n{private_meta.model_dump_json()}"
    )


# ── Multi-task splitter ────────────────────────────────────────────────────────

_TASK_PREFIXES = (
    "add ", "create ", "schedule ", "remind me", "remind ",
    "delete ", "remove ", "cancel ", "move ", "reschedule ", "update ",
    "complete ", "mark ", "log ", "journal", "remember ", "search ",
    "i need to ", "i want to ",
)


def _split_multi_tasks(message: str) -> list[str]:
    """Split newline-separated multi-task messages into individual tasks.

    Only splits when every non-empty line starts with a recognised action verb,
    so a single long message with embedded newlines is left intact.
    """
    lines = [ln.strip() for ln in message.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return [message]
    if all(any(ln.lower().startswith(p) for p in _TASK_PREFIXES) for ln in lines):
        return lines
    return [message]


# ── Sync entry point ───────────────────────────────────────────────────────────

def run_pm(message: str, config: PMConfig) -> str:
    """
    Run the Kairo agent for one turn (synchronous).
    History is owned by the SqliteSaver checkpointer — no history parameter needed.
    Used by: master agent personal_manager tool (via SYNC_WORKERS executor), HTTP endpoint.
    """
    _pm_progress(config, "Personal manager · started")

    sub_tasks = _split_multi_tasks(message)
    if len(sub_tasks) > 1:
        replies: list[str] = []
        for task in sub_tasks:
            r = run_typed_pm_turn(task, config)
            if r is None:
                r = f"Skipped: '{task}' (not a recognised action)"
            replies.append(r)
        return "\n".join(replies)

    typed_reply = run_typed_pm_turn(message, config)
    if typed_reply is not None:
        return typed_reply

    return _run_pm_react(message, config)


def _run_pm_react(message: str, config: PMConfig) -> str:
    """Tool-free fallback for low-risk coaching and general conversation."""
    sid = normalize_pm_session_id(config.session_id)
    d = TurnDecision(session_id=sid, message_preview=message)
    d.route("fallback", "typed path returned None, falling back to react LLM")
    d.wm_after = "none"

    if not has_api_key(config.provider, config.api_key):
        reply = (
            "I noted that but I'm not sure how to act on it — try asking me to "
            "schedule something, add a habit, or take a note."
        )
        d.route("fallback_no_key", "react fallback skipped — no LLM credentials")
        d.set_reply(reply)
        return reply

    try:
        checkpointer = _get_checkpointer(config.data_dir)
        tools: list[Any] = []
        llm = build_llm(config.provider, config.model, config.api_key, config.base_url)

        system = SystemMessage(content=f"{_FALLBACK_SYSTEM_PROMPT}\n\n{_build_context_block(config)}")
        agent = create_react_agent(llm, tools, prompt=system, checkpointer=checkpointer)

        rc = RunnableConfig(
            recursion_limit=100,
            configurable={"thread_id": config.session_id},
            run_name=f"personal_manager/{config.session_id}",
            tags=["personal_manager"],
            metadata={"agent": "personal_manager", "session_id": config.session_id},
            callbacks=[UsageTracker(config.session_id, config.data_dir)],
        )

        msgs: list[BaseMessage] = []
        try:
            baseline: int | None = None
            for state in with_retry(
                lambda: agent.stream({"messages": [HumanMessage(content=message)]}, rc, stream_mode="values")
            ):
                if not isinstance(state, dict) or "messages" not in state:
                    continue
                cur = state["messages"]
                msgs = cur
                if baseline is None:
                    baseline = len(cur)
                    continue
                for i in range(baseline, len(cur)):
                    _describe_pm_stream_message(config, cur[i])
                baseline = len(cur)
        except Exception:
            result = with_retry(
                lambda: agent.invoke({"messages": [HumanMessage(content=message)]}, rc)
            )
            msgs = result["messages"]

        if not msgs:
            result = agent.invoke({"messages": [HumanMessage(content=message)]}, rc)
            msgs = result["messages"]

        log_react_trace("personal_manager", msgs, session_id=config.session_id)
        final = msgs[-1]
        reply = str(getattr(final, "content", ""))

        saved = _try_passive_fact_save(message, config)
        if saved:
            sensitive = _contains_sensitive_terms(saved)
            d.memory_written.append("private_notes" if sensitive else "profile_md")
            reply = reply.rstrip() + f"\n\n_(Noted: {saved})_"

        d.set_reply(reply)
        return reply
    finally:
        d.persist(config.data_dir)


_PASSIVE_EXTRACTION_PROMPT = """\
Analyze this message and decide whether it contains a personal fact worth saving to long-term memory.

A fact worth saving: a genuine preference, dislike, interest, hobby, or biographical detail the speaker
is stating about themselves — something useful to remember in future conversations.
NOT worth saving: one-time actions, questions, scheduling requests, speculative statements ("I might try X").

Message: "{message}"

Respond with a single JSON object and no other text:
{{
  "should_save": true or false,
  "confidence": 0.0 to 1.0,
  "fact": "third-person restatement (e.g. 'User dislikes spicy food'), or empty string",
  "storage": "profile" or "private",
  "reasoning": "one sentence explaining your decision"
}}

Storage rules — use "private" for: health, medical, finance, daily routines, relationships.
Use "profile" for: hobbies, interests, food, sports, general likes/dislikes."""

_MIN_PASSIVE_CONFIDENCE = 0.72


def _try_passive_fact_save(message: str, config: PMConfig) -> str | None:
    """LLM-reasoned fact extraction from fallback-path messages.

    Returns the saved fact string (for reply acknowledgement), or None if
    nothing was saved. Confidence < 0.72 is silently skipped.
    """
    if not config.vault_dir:
        return None
    if not _is_self_disclosure(_norm(message)):
        return None
    try:
        import json as _json
        llm = build_llm(config.provider, config.model, config.api_key, config.base_url)
        result = llm.invoke(_PASSIVE_EXTRACTION_PROMPT.format(message=message))
        raw = str(getattr(result, "content", result)).strip()
        # Strip markdown code fences if present
        raw = raw.strip("`").removeprefix("json").strip()
        assessment = _json.loads(raw)
        if not assessment.get("should_save"):
            return None
        confidence = float(assessment.get("confidence", 0))
        if confidence < _MIN_PASSIVE_CONFIDENCE:
            return None
        fact = str(assessment.get("fact", "")).strip()
        if not fact or len(fact) < 5:
            return None
        storage = assessment.get("storage", "profile")
        sensitive = storage == "private" or _contains_sensitive_terms(fact)
        execute_memory_action(
            PMAction(
                "private_note_append" if sensitive else "remember",
                {"fact": fact, "note": fact},
            ),
            config,
            str(config.session_id),
            str(config.data_dir),
        )
        return fact
    except Exception:
        return None


# ── Async streaming entry point ────────────────────────────────────────────────

async def astream_pm(
    message: str,
    config: PMConfig,
) -> AsyncIterator[tuple[str, str]]:
    """
    Async token-streaming version of run_pm.

    Yields (kind, value) tuples:
      ("progress", str)  — UI status line (tool start, model thinking, etc.)
      ("token",    str)  — individual LLM output token
      ("done",     str)  — full concatenated reply (end-of-stream signal)

    Uses AsyncSqliteSaver so it never blocks the event loop. The fallback path
    is conversation-only; state-changing requests should be handled by the typed
    workflow above.
    """
    _pm_progress(config, "Personal manager · started")
    typed_reply = run_typed_pm_turn(message, config)
    if typed_reply is not None:
        yield ("progress", "Personal manager · controlled workflow")
        yield ("token", typed_reply)
        yield ("done", typed_reply)
        return

    checkpointer = await _get_async_checkpointer(config.data_dir)
    tools: list[Any] = []
    llm = build_llm(config.provider, config.model, config.api_key, config.base_url)

    system = SystemMessage(content=f"{_FALLBACK_SYSTEM_PROMPT}\n\n{_build_context_block(config)}")
    agent = create_react_agent(llm, tools, prompt=system, checkpointer=checkpointer)

    rc = RunnableConfig(
        recursion_limit=100,
        configurable={"thread_id": config.session_id},
        run_name=f"personal_manager/{config.session_id}",
        tags=["personal_manager"],
        metadata={"agent": "personal_manager", "session_id": config.session_id},
        callbacks=[UsageTracker(config.session_id, config.data_dir)],
    )

    reply_parts: list[str] = []
    trace_msgs: list[BaseMessage] = []

    async for event in agent.astream_events(
        {"messages": [HumanMessage(content=message)]}, rc, version="v2"
    ):
        evt = event.get("event", "")
        if evt == "on_chat_model_stream":
            chunk = event.get("data", {}).get("chunk")
            if chunk and hasattr(chunk, "content") and isinstance(chunk.content, str) and chunk.content:
                reply_parts.append(chunk.content)
                yield ("token", chunk.content)
        elif evt == "on_chain_end" and event.get("name") == "LangGraph":
            output = event.get("data", {}).get("output") or {}
            trace_msgs = output.get("messages", [])

    if trace_msgs:
        log_react_trace("personal_manager", trace_msgs, session_id=config.session_id)

    yield ("done", "".join(reply_parts))


# ── Progress helpers ───────────────────────────────────────────────────────────

def _preview_progress_payload(obj: Any, max_len: int = 280) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        s = str(obj)
    s = " ".join(s.split())
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


def _describe_pm_stream_message(config: PMConfig, m: BaseMessage) -> None:
    if isinstance(m, (SystemMessage, HumanMessage)):
        return
    if isinstance(m, AIMessage):
        tcs = getattr(m, "tool_calls", None) or []
        if tcs:
            parts: list[str] = []
            for tc in tcs:
                if isinstance(tc, dict):
                    name = tc.get("name") or "?"
                    args = tc.get("args") or {}
                else:
                    name = getattr(tc, "name", None) or "?"
                    args = getattr(tc, "args", None) or {}
                parts.append(f"{name}({_preview_progress_payload(args, 220)})")
            _pm_progress(config, "Model → calling: " + " · ".join(parts))
        else:
            text = (str(m.content or "")).strip()
            if text:
                _pm_progress(config, f"Model → text ({len(text)} chars): {_preview_progress_payload(text, 200)}")
            else:
                _pm_progress(config, "Model → empty reply (waiting for next graph step)")
        return
    if isinstance(m, ToolMessage):
        name = m.name or "tool"
        body = _preview_progress_payload(m.content, 450)
        _pm_progress(config, f"Tool `{name}` result: {body}")
        return
    _pm_progress(config, f"Step: {type(m).__name__}")


def _pm_progress(config: PMConfig, msg: str) -> None:
    if config.on_progress:
        config.on_progress(msg)
