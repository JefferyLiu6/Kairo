"""
Session history windowing and compression.

pack_history()   — hard trim: keep the last N turn pairs.
compress_history() — LLM-based: summarise old turns when history exceeds
                     MAX_HISTORY_TURNS (env, default 40).  The summary is
                     stored alongside the session and injected as a leading
                     SystemMessage so the model retains context cheaply.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _max_history_turns() -> int:
    try:
        return max(4, int(os.environ.get("MAX_HISTORY_TURNS", "40")))
    except ValueError:
        return 40


def pack_history(history: list[dict], max_turns: int | None = None) -> list[dict]:
    """
    Return at most `max_turns` user/assistant pairs from the end of `history`.

    Each "turn" is one user message + one assistant reply (2 entries).
    Older pairs are silently dropped; the rest are returned in order.

    Args:
        history:   List of {"role": ..., "content": ...} dicts.
        max_turns: Maximum number of pairs to keep (defaults to MAX_HISTORY_TURNS env var).
    """
    limit = max_turns if max_turns is not None else _max_history_turns()
    if limit <= 0:
        return []

    # Walk backwards and collect complete pairs
    pairs: list[list[dict]] = []
    i = len(history) - 1
    while i >= 0 and len(pairs) < limit:
        if history[i]["role"] == "assistant":
            if i > 0 and history[i - 1]["role"] == "user":
                pairs.append([history[i - 1], history[i]])
                i -= 2
                continue
        i -= 1

    # pairs is newest-first; reverse so history stays chronological
    pairs.reverse()
    return [msg for pair in pairs for msg in pair]


def compress_history(
    history: list[dict],
    existing_summary: str,
    llm,
    max_turns: int | None = None,
) -> tuple[list[dict], str]:
    """
    When history exceeds max_turns, summarise the oldest half with the LLM.

    Returns:
        (trimmed_history, new_summary)
        - trimmed_history: the recent turns to keep verbatim
        - new_summary:     updated rolling summary (old summary + digest of dropped turns)

    If the LLM call fails, falls back to pack_history() silently.
    """
    limit = max_turns if max_turns is not None else _max_history_turns()

    # Count complete turn pairs
    pairs: list[tuple[int, int]] = []  # (user_idx, assistant_idx)
    i = 0
    while i < len(history) - 1:
        if history[i]["role"] == "user" and history[i + 1]["role"] == "assistant":
            pairs.append((i, i + 1))
            i += 2
        else:
            i += 1

    if len(pairs) <= limit:
        return history, existing_summary

    # Split: oldest half gets summarised, newest half kept verbatim
    split = len(pairs) // 2
    to_compress = pairs[:split]
    to_keep_start = pairs[split][0]

    # Build text block for summarisation
    lines: list[str] = []
    if existing_summary:
        lines.append(f"[Previous summary]\n{existing_summary}\n")
    lines.append("[Conversation to summarise]")
    for u_i, a_i in to_compress:
        lines.append(f"User: {history[u_i]['content']}")
        lines.append(f"Assistant: {history[a_i]['content']}")

    prompt = (
        "Summarise the following conversation history in 3–6 concise bullet points. "
        "Preserve key facts, decisions, and context the assistant will need later. "
        "Be terse — omit pleasantries.\n\n" + "\n".join(lines)
    )

    try:
        from langchain_core.messages import HumanMessage
        result = llm.invoke([HumanMessage(content=prompt)])
        new_summary = str(getattr(result, "content", result)).strip()
    except Exception as exc:
        logger.warning("History compression failed, falling back to hard trim: %s", exc)
        return pack_history(history, limit), existing_summary

    trimmed = history[to_keep_start:]
    return trimmed, new_summary
