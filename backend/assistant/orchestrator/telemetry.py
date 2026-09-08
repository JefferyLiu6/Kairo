"""Content-free JSON telemetry for local logs and Azure Container Apps console logs."""
from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

logger = logging.getLogger("kairo.telemetry")
_context: ContextVar[tuple[str, str] | None] = ContextVar("kairo_trace", default=None)
_FIELDS = {"outcome", "verdict", "source", "retry_count", "input_tokens", "output_tokens"}


def configure_telemetry() -> None:
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def emit(event: str, **fields) -> None:
    if os.getenv("KAIRO_TELEMETRY", "1") == "0":
        return
    context = _context.get()
    if context is None:
        return
    record = {
        "schema_version": 1, "service": "kairo", "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trace_id": context[0], "span_id": context[1],
    }
    record.update({key: value for key, value in fields.items() if key in _FIELDS})
    try:
        logger.info(json.dumps(record, allow_nan=False))
    except Exception:
        pass  # Telemetry is best effort, never authority for application state.


@contextmanager
def span(stage: str):
    parent = _context.get()
    trace_id = parent[0] if parent else uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    token = _context.set((trace_id, span_id))
    started = time.perf_counter()
    outcome = "ok"
    try:
        yield
    except GeneratorExit:
        outcome = "cancelled"
        raise
    except BaseException:
        outcome = "error"
        raise
    finally:
        if os.getenv("KAIRO_TELEMETRY", "1") != "0":
            record = {
                "schema_version": 1, "service": "kairo", "event": "span",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trace_id": trace_id, "span_id": span_id,
                "parent_span_id": parent[1] if parent else None,
                "stage": stage, "outcome": outcome,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            }
            try:
                logger.info(json.dumps(record))
            except Exception:
                pass
        _context.reset(token)


def observed(stage: str):
    def decorate(fn):
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                with span(stage):
                    return await fn(*args, **kwargs)
            return async_wrapper

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with span(stage):
                return fn(*args, **kwargs)
        return wrapper
    return decorate
