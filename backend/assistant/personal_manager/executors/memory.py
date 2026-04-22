"""Private/shared memory action executor."""
from __future__ import annotations

from typing import Any

from ..persistence.store import load_private, private_export, save_private
from ..persistence.profile import append_profile_fact
from ..domain.types import PMAction
from ..application.reconciliation import maybe_reconcile_async
from ..application.semantic_memory import interpret_semantic_memory_candidates, save_semantic_memory_candidates
from .common import _result

_NEGATION_MARKERS = ("dislike", "don't like", "do not like", "hate", "not into",
                     "can't stand", "cannot stand", "no longer like", "stopped liking")


def _likely_contradicts_existing(fact: str) -> bool:
    lower = fact.lower()
    return any(m in lower for m in _NEGATION_MARKERS)


def execute_memory_action(action: PMAction, config: Any, session_id: str, data_dir: str) -> dict[str, Any] | None:
    if action.action_type == "private_export":
        return _result(private_export(session_id, data_dir))
    if action.action_type == "private_note_append":
        meta = load_private(session_id, data_dir)
        meta.notes_private.append(action.payload["note"])
        meta.notes_private = meta.notes_private[-50:]
        save_private(meta, session_id, data_dir)
        return _result("Noted.")
    if action.action_type == "remember":
        vault = getattr(config, "vault_dir", None)
        if not vault:
            return _result("Memory not configured (VAULT_DIR not set).", ok=False)
        fact = str(action.payload["fact"])
        append_profile_fact(vault, data_dir, session_id, fact)
        candidates = interpret_semantic_memory_candidates(fact, None)
        save_semantic_memory_candidates(session_id, data_dir, candidates)
        immediate = _likely_contradicts_existing(fact)
        maybe_reconcile_async(vault, data_dir, session_id, config, immediate=immediate)
        return _result(f"Remembered: {fact}")
    if action.action_type == "web_search_blocked":
        return _result("Skipped that search — it looked like it might expose private info.", ok=False)
    return None
