"""
Token usage tracking — mirrors history_version/src/storage/usage.ts.

Usage is accumulated per session in data/usage/<session_id>.json.
A LangChain callback handler extracts token counts from each LLM response.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

_lock = threading.Lock()


# ── Persistence ────────────────────────────────────────────────────────────────

def _usage_path(session_id: str, data_dir: str) -> str:
    return os.path.join(data_dir, "usage", f"{session_id}.json")


def load_usage(session_id: str, data_dir: str) -> dict:
    path = _usage_path(session_id, data_dir)
    if not os.path.exists(path):
        return {"session_id": session_id, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"session_id": session_id, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def record_usage(session_id: str, data_dir: str, prompt: int, completion: int) -> None:
    """Add prompt + completion token counts to the session's running total."""
    path = _usage_path(session_id, data_dir)
    with _lock:
        data = load_usage(session_id, data_dir)
        data["prompt_tokens"] += prompt
        data["completion_tokens"] += completion
        data["total_tokens"] = data["prompt_tokens"] + data["completion_tokens"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


def list_all_usage(data_dir: str) -> list[dict]:
    usage_dir = os.path.join(data_dir, "usage")
    if not os.path.isdir(usage_dir):
        return []
    results: list[dict] = []
    for fname in sorted(os.listdir(usage_dir)):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(usage_dir, fname), encoding="utf-8") as f:
                results.append(json.load(f))
        except Exception:
            pass
    return results


# ── LangChain callback ────────────────────────────────────────────────────────

class UsageTracker(BaseCallbackHandler):
    """
    Attach to an agent run to accumulate token counts from every LLM response.
    Usage is persisted to disk after each LLM call.
    """

    def __init__(self, session_id: str, data_dir: str) -> None:
        self.session_id = session_id
        self.data_dir = data_dir

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            # LangChain stores usage in response.llm_output or response.generations
            llm_output = getattr(response, "llm_output", None) or {}
            token_usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
            prompt = int(token_usage.get("prompt_tokens") or token_usage.get("input_tokens") or 0)
            completion = int(token_usage.get("completion_tokens") or token_usage.get("output_tokens") or 0)
            if prompt or completion:
                record_usage(self.session_id, self.data_dir, prompt, completion)
        except Exception:
            pass  # never crash the agent over usage tracking
