"""
LangSmith tracing — one trace per chat turn, one child run per pipeline stage.

Off unless LANGSMITH_TRACING=true. When off, the decorators cost a dict lookup
and nothing is sent anywhere.

Env vars (read by the langsmith SDK):
  LANGSMITH_TRACING       true to send traces
  LANGSMITH_API_KEY       a "Tracing Only" key is enough
  LANGSMITH_PROJECT       kairo-dev locally, kairo-prod on Render
  LANGSMITH_HIDE_INPUTS   true to drop message text from traces (use in production)
  LANGSMITH_HIDE_OUTPUTS  true to drop reply text from traces (use in production)

Config objects and LLM clients are never recorded: they carry API keys.
"""
from __future__ import annotations

import functools
import hashlib
import inspect
import os
from typing import Any, Callable, Sequence

from langsmith import get_current_run_tree, traceable

# Argument names that hold credentials or clients and must never leave the process.
_UNTRACED_ARGS = {"config", "cfg", "llm", "api_key", "pm_api_key"}


def _safe_inputs(inputs: dict) -> dict:
    return {key: value for key, value in inputs.items() if key not in _UNTRACED_ARGS}


def _final_reply(events: Sequence[Any]) -> dict:
    """Collapse a streamed turn's (kind, value) events into its final reply."""
    for event in reversed(events):
        if isinstance(event, tuple) and len(event) == 2 and event[0] == "done":
            return {"reply": event[1]}
    return {"reply": None}


def traced(name: str) -> Callable:
    """Record the decorated function as a LangSmith run named `name`.

    langsmith treats a keyword argument called `config` as a LangChain
    RunnableConfig, and Kairo's config objects are not one. Arguments are bound
    positionally before the call so `fn(msg, config=cfg)` stays safe.
    """
    def decorate(fn: Callable) -> Callable:
        options: dict[str, Any] = {"name": name, "run_type": "chain", "process_inputs": _safe_inputs}
        if inspect.isasyncgenfunction(fn):
            options["reduce_fn"] = _final_reply
        run = traceable(**options)(fn)
        signature = inspect.signature(fn)

        def positional(args: tuple, kwargs: dict) -> tuple[tuple, dict]:
            bound = signature.bind(*args, **kwargs)
            return bound.args, bound.kwargs

        if inspect.isasyncgenfunction(fn):
            @functools.wraps(fn)
            async def agen_wrapper(*args, **kwargs):
                args, kwargs = positional(args, kwargs)
                async for item in run(*args, **kwargs):
                    yield item
            return agen_wrapper

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                args, kwargs = positional(args, kwargs)
                return await run(*args, **kwargs)
            return async_wrapper

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            args, kwargs = positional(args, kwargs)
            return run(*args, **kwargs)
        return wrapper
    return decorate


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def tag_turn(config: Any) -> None:
    """Attach searchable, non-identifying metadata to the current trace."""
    run = get_current_run_tree()
    if run is None:
        return
    user_id = getattr(config, "user_id", "") or ""
    session_id = getattr(config, "session_id", "") or ""
    metadata = {
        "user": _hash(user_id) if user_id else None,
        "session": _hash(f"{user_id}:{session_id}") if session_id else None,
        "provider": getattr(config, "provider", None),
        "model": getattr(config, "model", None),
        "pm_model": getattr(config, "pm_model", None),
        "commit": (os.environ.get("RENDER_GIT_COMMIT") or "")[:7] or None,
    }
    run.add_metadata({key: value for key, value in metadata.items() if value})
