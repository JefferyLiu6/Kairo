"""Background LLM reconciliation for PROFILE.md.

Reads the current profile, runs a single LLM pass to resolve contradictions
and remove duplicates, then atomically rewrites the file.

Called from executors/memory.py in a daemon thread — never blocks the turn.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from assistant.shared.llm_env import build_llm, has_api_key

from ..persistence.profile import (
    mark_reconciled,
    read_profile,
    write_profile_atomic,
)

logger = logging.getLogger(__name__)

_recon_lock = threading.Lock()

_RECONCILE_PROMPT = """\
You are a personal profile editor. The profile below may contain contradictions, \
duplicates, or inconsistently formatted facts about the same person.

Your task: produce a clean, consistent, deduplicated version of the profile.

Rules (follow strictly):
1. If two facts contradict each other (e.g. "likes badminton" and "dislikes badminton"), \
keep only the LATER one — it is the more recent and accurate belief.
2. Remove exact and near-duplicate facts (same meaning, different wording).
3. Merge closely related facts into one concise line when it reads naturally.
4. Standardise phrasing to third-person ("User likes X", "User dislikes Y", "User plays Z").
5. Do NOT invent, infer, or add any new facts that are not already present.
6. Preserve the "# User Profile" heading.
7. Return ONLY the cleaned profile — no commentary, no explanation, no diff markers.

Current profile:
{profile_text}"""


def reconcile_profile(
    vault_dir: str,
    data_dir: str,
    session_id: str,
    config: Any,
) -> bool:
    """Run LLM reconciliation. Returns True if the profile was rewritten."""
    if not _recon_lock.acquire(blocking=False):
        return False  # another thread is already reconciling
    try:
        return _do_reconcile(vault_dir, data_dir, session_id, config)
    finally:
        _recon_lock.release()


def _do_reconcile(
    vault_dir: str,
    data_dir: str,
    session_id: str,
    config: Any,
) -> bool:
    profile_text = read_profile(vault_dir)
    if not profile_text.strip():
        mark_reconciled(data_dir, session_id)
        return False

    if not has_api_key(getattr(config, "provider", "openai"), getattr(config, "api_key", None)):
        logger.debug("Skipping profile reconciliation — no API key configured")
        mark_reconciled(data_dir, session_id)
        return False

    try:
        llm = build_llm(
            getattr(config, "provider", "openai"),
            getattr(config, "model", ""),
            getattr(config, "api_key", None),
            getattr(config, "base_url", None),
        )
        result = llm.invoke(_RECONCILE_PROMPT.format(profile_text=profile_text))
        cleaned = str(getattr(result, "content", result)).strip()
    except Exception as exc:
        logger.warning("Profile reconciliation LLM call failed: %s", exc)
        mark_reconciled(data_dir, session_id)  # reset counter so we don't hammer the API
        return False

    if not cleaned or len(cleaned) < 10:
        logger.warning("Profile reconciliation returned empty/short result — keeping original")
        mark_reconciled(data_dir, session_id)
        return False

    if not cleaned.startswith("#"):
        cleaned = "# User Profile\n\n" + cleaned

    try:
        write_profile_atomic(vault_dir, cleaned)
        mark_reconciled(data_dir, session_id)
        logger.info("Profile reconciled for session %s", session_id)
        return True
    except Exception as exc:
        logger.warning("Profile reconciliation write failed: %s", exc)
        return False


def maybe_reconcile_async(
    vault_dir: str,
    data_dir: str,
    session_id: str,
    config: Any,
    *,
    immediate: bool = False,
) -> None:
    """Spawn a daemon thread to reconcile if the dirty threshold is met.

    Pass immediate=True to bypass the threshold — used when a negation/
    contradiction is detected so the profile is cleaned on the next read.
    """
    from ..persistence.profile import should_reconcile
    if not immediate and not should_reconcile(data_dir, session_id):
        return
    threading.Thread(
        target=reconcile_profile,
        args=(vault_dir, data_dir, session_id, config),
        daemon=True,
        name=f"profile-recon-{session_id[-8:]}",
    ).start()
