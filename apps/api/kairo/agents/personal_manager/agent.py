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
LangSmith tracing is opt-in via LANGSMITH_TRACING=true (kairo.core.observability.tracing).
run_pm / astream_pm are recorded as "pm_turn" runs; run_name + tags on every
RunnableConfig give clean names to the LangGraph steps inside them.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import threading
from kairo.utils.clock import local_now
from kairo.core.observability.tracing import tag_turn, traced
from typing import Any, AsyncIterator, Callable, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.prebuilt import create_react_agent

from kairo.core.observability.agent_trace import log_react_trace
from kairo.core.llm_env import build_llm, has_api_key, with_retry
from kairo.core.observability.usage import UsageTracker
from kairo.tools.agent_tools import _build_tools as _build_tools
from kairo.utils.sensitivity import _contains_sensitive_terms
from kairo.tools.executors.memory import execute_memory_action
from kairo.agents.personal_manager.contracts import PassiveFactOut
from kairo.agents.personal_manager.extractors.intent import _is_self_disclosure
from kairo.agents.personal_manager.extractors.model import call_pm_model
from kairo.core.domain.types import PMAction
from kairo.utils.parsing.text import _norm
from kairo.tools.calendar.service import format_google_calendar_for_context
from kairo.tools.calendar.service import CalendarService
from kairo.storage.habits import format_habits_for_context
from kairo.storage.profile import read_profile
from kairo.storage.store import _pm_dir, format_schedule_for_context, format_todos_for_context, load_private
from kairo.core.domain.session import normalize_pm_session_id
from kairo.storage.control_store import append_conversation_turn, list_conversation_turns
from kairo.storage.decision_log import TurnDecision
from kairo.agents.personal_manager.workflow import run_typed_pm_turn
from kairo.core.prompt import prompt, record_prompt


_FALLBACK_SYSTEM_PROMPT = prompt("pm_fallback", """\
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
""")


@dataclasses.dataclass
class PMConfig:
    user_id: str = ""             # owner identity (UUID from users table)
    provider: str = "openai"
    model: str = "gpt-oss:120b"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    data_dir: str = "./data"
    vault_dir: Optional[str] = None
    session_id: str = "default"   # chat thread identifier
    on_progress: Optional[Callable[[str], None]] = None


# ── Path helpers ───────────────────────────────────────────────────────────────

def _pm_db_path(user_id: str, data_dir: str) -> str:
    return os.path.join(_pm_dir(user_id, data_dir), "pm.db")


def _checkpoints_db_path(user_id: str, data_dir: str) -> str:
    path = os.path.join(_pm_dir(user_id, data_dir), "checkpoints.db")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


# ── Per-user checkpointer caches ──────────────────────────────────────────────

_sync_checkpointers: dict[str, SqliteSaver] = {}
_sync_checkpointer_lock = threading.Lock()


def _get_checkpointer(user_id: str, data_dir: str) -> SqliteSaver:
    with _sync_checkpointer_lock:
        if user_id not in _sync_checkpointers:
            conn = sqlite3.connect(_checkpoints_db_path(user_id, data_dir), check_same_thread=False)
            _sync_checkpointers[user_id] = SqliteSaver(conn)
        return _sync_checkpointers[user_id]


_async_checkpointers: dict[str, Any] = {}
_async_checkpointer_lock = threading.Lock()


async def _get_async_checkpointer(user_id: str, data_dir: str) -> Any:
    """Lazy per-user AsyncSqliteSaver. WAL mode allows sync and async to coexist."""
    if user_id in _async_checkpointers:
        return _async_checkpointers[user_id]
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    conn = await aiosqlite.connect(_checkpoints_db_path(user_id, data_dir))
    await conn.execute("PRAGMA journal_mode=WAL")
    checkpointer = AsyncSqliteSaver(conn)
    await checkpointer.setup()
    with _async_checkpointer_lock:
        _async_checkpointers[user_id] = checkpointer
    return checkpointer


def get_thread_messages(user_id: str, thread_id: str, data_dir: str) -> list[dict]:
    """Return the human/assistant message history for a thread from the conversation log."""
    return list_conversation_turns(user_id, thread_id, data_dir)


# ── Context block (shared by run_pm and astream_pm) ───────────────────────────

def _build_context_block(config: PMConfig) -> str:
    now = local_now()
    date_ctx = (
        f"## Current date/time\n"
        f"Today: {now.strftime('%A, %B %d, %Y')}  "
        f"Time: {now.strftime('%H:%M %Z')}  "
        f"ISO: {now.isoformat()}"
    )
    uid = config.user_id or config.session_id
    google_primary = False
    try:
        google_primary = CalendarService(uid, config.data_dir).has_google_account()
    except Exception:
        google_primary = False
    schedule_ctx = (
        format_google_calendar_for_context(uid, config.data_dir)
        if google_primary
        else format_schedule_for_context(uid, config.data_dir)
    )
    google_schedule_ctx = "" if google_primary else format_google_calendar_for_context(uid, config.data_dir)
    todos_ctx = format_todos_for_context(uid, config.data_dir)
    private_meta = load_private(uid, config.data_dir)
    habits_ctx = format_habits_for_context(_pm_db_path(uid, config.data_dir))
    profile_ctx = ""
    # Per-user profile stored at data/users/<user_id>/PROFILE.md
    user_data_dir = _pm_dir(uid, config.data_dir)
    raw_profile = read_profile(user_data_dir).strip()
    if not raw_profile and config.vault_dir:
        raw_profile = read_profile(config.vault_dir).strip()
    if raw_profile:
        profile_ctx = f"## User profile (long-term memory)\n{raw_profile}"
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

@traced("pm_turn")
def run_pm(message: str, config: PMConfig) -> str:
    """
    Run the Kairo agent for one turn (synchronous).
    History is owned by the SqliteSaver checkpointer — no history parameter needed.
    Used by: master agent personal_manager tool (via SYNC_WORKERS executor), HTTP endpoint.
    """
    tag_turn(config)
    _pm_progress(config, "Personal manager · started")
    uid = config.user_id or normalize_pm_session_id(config.session_id)
    thread_id = normalize_pm_session_id(config.session_id)

    sub_tasks = _split_multi_tasks(message)
    if len(sub_tasks) > 1:
        replies: list[str] = []
        for task in sub_tasks:
            r = run_typed_pm_turn(task, config)
            if r is None:
                r = f"Skipped: '{task}' (not a recognised action)"
            replies.append(r)
        combined = "\n".join(replies)
        append_conversation_turn(uid, thread_id, config.data_dir, message, combined)
        return combined

    typed_reply = run_typed_pm_turn(message, config)
    if typed_reply is not None:
        append_conversation_turn(uid, thread_id, config.data_dir, message, typed_reply)
        return typed_reply

    reply = _run_pm_react(message, config)
    append_conversation_turn(uid, thread_id, config.data_dir, message, reply)
    return reply


def _run_pm_react(message: str, config: PMConfig) -> str:
    """Tool-free fallback for low-risk coaching and general conversation."""
    sid = config.user_id or normalize_pm_session_id(config.session_id)
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
        uid = config.user_id or config.session_id
        checkpointer = _get_checkpointer(uid, config.data_dir)
        tools: list[Any] = []
        llm = build_llm(config.provider, config.model, config.api_key, config.base_url)

        record_prompt(_FALLBACK_SYSTEM_PROMPT)
        system = SystemMessage(content=f"{_FALLBACK_SYSTEM_PROMPT}\n\n{_build_context_block(config)}")
        agent = create_react_agent(llm, tools, prompt=system, checkpointer=checkpointer)

        rc = RunnableConfig(
            recursion_limit=100,
            configurable={"thread_id": config.session_id},
            run_name=f"personal_manager/{config.session_id}",
            tags=["personal_manager"],
            metadata={"agent": "personal_manager", "session_id": config.session_id, "user_id": uid},
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


_PASSIVE_EXTRACTION_PROMPT = prompt("passive_fact", """\
Analyze this message and decide whether it contains a personal fact worth saving to long-term memory.

A fact worth saving: a genuine preference, dislike, interest, hobby, or biographical detail the speaker
is stating about themselves — something useful to remember in future conversations.
NOT worth saving: one-time actions, questions, scheduling requests, speculative statements ("I might try X").

The user's message arrives as JSON: {"message": "..."}

Respond with a single JSON object and no other text:
{
  "should_save": true or false,
  "confidence": 0.0 to 1.0,
  "fact": "third-person restatement (e.g. 'User dislikes spicy food'), or empty string",
  "storage": "profile" or "private",
  "reasoning": "one sentence explaining your decision"
}

Storage rules — use "private" for: health, medical, finance, daily routines, relationships.
Use "profile" for: hobbies, interests, food, sports, general likes/dislikes.""")

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
    assessment = call_pm_model("passive_fact", _PASSIVE_EXTRACTION_PROMPT, message, PassiveFactOut, config)
    if assessment is None or not assessment.should_save or assessment.confidence < _MIN_PASSIVE_CONFIDENCE:
        return None
    fact = assessment.fact.strip()
    if len(fact) < 5:
        return None
    sensitive = assessment.storage == "private" or _contains_sensitive_terms(fact)
    try:
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

@traced("pm_turn")
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
    tag_turn(config)
    _pm_progress(config, "Personal manager · started")
    uid = config.user_id or config.session_id
    thread_id = normalize_pm_session_id(config.session_id)
    typed_reply = run_typed_pm_turn(message, config)
    if typed_reply is not None:
        append_conversation_turn(uid, thread_id, config.data_dir, message, typed_reply)
        yield ("progress", "Personal manager · controlled workflow")
        yield ("token", typed_reply)
        yield ("done", typed_reply)
        return
    checkpointer = await _get_async_checkpointer(uid, config.data_dir)
    tools: list[Any] = []
    llm = build_llm(config.provider, config.model, config.api_key, config.base_url)

    record_prompt(_FALLBACK_SYSTEM_PROMPT)
    system = SystemMessage(content=f"{_FALLBACK_SYSTEM_PROMPT}\n\n{_build_context_block(config)}")
    agent = create_react_agent(llm, tools, prompt=system, checkpointer=checkpointer)

    rc = RunnableConfig(
        recursion_limit=100,
        configurable={"thread_id": config.session_id},
        run_name=f"personal_manager/{config.session_id}",
        tags=["personal_manager"],
        metadata={"agent": "personal_manager", "session_id": config.session_id, "user_id": uid},
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

    full_reply = "".join(reply_parts)
    append_conversation_turn(uid, thread_id, config.data_dir, message, full_reply)
    yield ("done", full_reply)


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
