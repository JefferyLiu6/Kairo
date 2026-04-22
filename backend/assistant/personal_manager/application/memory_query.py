"""Typed answers for simple PM memory/profile questions."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from ..parsing.text import _norm
from ..persistence.personalization import list_user_preferences
from ..persistence.recent_context import list_recent_context
from ..persistence.semantic_memory import list_semantic_memory


def _is_know_about_me_query(text: str) -> bool:
    return bool(
        re.search(r"\bwhat\s+do\s+you\s+know\s+about\s+me\b", text)
        or re.search(r"\bwhat\s+(?:have\s+you\s+)?learned\s+about\s+me\b", text)
        or re.search(r"\btell\s+me\s+what\s+you\s+know\s+about\s+me\b", text)
        or re.search(r"\bwhat\s+do\s+you\s+remember\s+about\s+me\b", text)
    )


@dataclass(frozen=True)
class _LikeItem:
    key: str
    label: str
    category: str = ""
    style: str = ""


def is_memory_query(message: str) -> bool:
    text = _norm(message)
    return bool(
        re.search(r"\bwhat\s+do\s+i\s+like\b", text)
        or re.search(r"\bwhat\s+else\b", text)
        or re.search(r"\bwhat\s+(?:are\s+)?my\s+hobbies\b", text)
        or re.search(r"\bwhat\s+do\s+you\s+remember\s+about\s+me\b", text)
        or re.search(r"\bwhat\s+do\s+you\s+know\s+about\s+my\s+preferences\b", text)
        or _is_know_about_me_query(text)
    )


def build_memory_query_reply(message: str, config: Any, session_id: str, data_dir: str) -> str | None:
    if not is_memory_query(message):
        return None
    text = _norm(message)
    if _is_know_about_me_query(text):
        return _build_full_context_reply(config, session_id, data_dir)
    items = _known_likes(config, session_id, data_dir)
    if not items:
        return "I don't have any likes or hobbies saved for you yet."
    lines = ["You've told me you like:"]
    for idx, item in enumerate(items, start=1):
        detail = _item_detail(item)
        lines.append(f"{idx}. {item.label}{detail}")
    return "\n".join(lines)


def _build_full_context_reply(config: Any, session_id: str, data_dir: str) -> str:
    from ..persistence.store import format_schedule_for_context
    from ..persistence.habits import format_habits_for_context
    from ..persistence.control_store import pm_db_path as _pm_db_path

    sections: list[str] = []

    schedule = format_schedule_for_context(session_id, data_dir)
    if schedule and "empty" not in schedule.lower():
        # Strip internal [id] prefixes — they are for agent use, not user display.
        import re as _re
        schedule = _re.sub(r"\[[\w-]+\]\s*", "", schedule)
        sections.append(schedule)

    try:
        habits = format_habits_for_context(_pm_db_path(session_id, data_dir))
        if habits and "none" not in habits.lower() and "no habits" not in habits.lower() and habits.strip():
            sections.append(habits)
    except Exception:
        pass

    facts = _all_profile_facts(getattr(config, "vault_dir", None))
    if facts:
        lines = ["## Preferences & notes"]
        for fact in facts:
            lines.append(f"- {fact}")
        sections.append("\n".join(lines))

    if not sections:
        return "I don't have much saved about you yet — tell me about your schedule, habits, or preferences and I'll remember them."
    return "Here's what I know about you:\n\n" + "\n\n".join(sections)


def _known_likes(config: Any, session_id: str, data_dir: str) -> list[_LikeItem]:
    by_key: dict[str, _LikeItem] = {}
    styles = _activity_styles(session_id, data_dir)
    for pref in list_user_preferences(session_id, data_dir):
        if pref.rule_type != "preferred_activity":
            continue
        activity = str(pref.value.get("activity") or pref.scope_key or "").strip().lower()
        label = str(pref.value.get("label") or activity.title()).strip()
        if not activity or not label:
            continue
        by_key[activity] = _LikeItem(
            key=activity,
            label=label,
            category=str(pref.value.get("category") or ""),
            style=styles.get(activity, ""),
        )

    for memory in list_semantic_memory(session_id, data_dir, polarity="positive"):
        if not _semantic_memory_is_like(memory.memory_type, memory.predicate):
            continue
        key = _item_key(memory.object)
        if not key:
            continue
        by_key.setdefault(
            key,
            _LikeItem(
                key=key,
                label=memory.object.title(),
                category=str(memory.qualifiers.get("category") or ""),
                style=styles.get(memory.object.lower(), ""),
            ),
        )

    for label in _profile_likes(getattr(config, "vault_dir", None)):
        key = _item_key(label)
        by_key.setdefault(key, _LikeItem(key=key, label=label))

    return sorted(by_key.values(), key=lambda item: item.label.lower())


def _activity_styles(session_id: str, data_dir: str) -> dict[str, str]:
    styles: dict[str, str] = {}
    for context in list_recent_context(session_id, data_dir, context_type="activity_topic"):
        activity = str(context.payload.get("activity") or "").strip().lower()
        style = str(context.payload.get("style") or "").strip().lower()
        if activity and style:
            styles[activity] = style
    return styles


def _profile_likes(vault_dir: str | None) -> list[str]:
    if not vault_dir:
        return []
    path = os.path.join(vault_dir, "PROFILE.md")
    try:
        raw = open(path, encoding="utf-8").read()
    except FileNotFoundError:
        return []
    labels: list[str] = []
    for line in raw.splitlines():
        text = line.strip().lstrip("-").strip()
        label = _label_from_profile_fact(text)
        if label:
            labels.append(label)
    return labels


def _label_from_profile_fact(text: str) -> str:
    lower = text.lower().strip(" .")
    if _is_negative_profile_fact(lower):
        return ""
    patterns = (
        r"^user likes playing (.+)$",
        r"^user likes (.+)$",
        r"^user enjoys (.+)$",
        r"^user is interested in (.+)$",
        r"^user is into (.+)$",
        r"^user prefers (.+)$",
        r"^i like playing (.+)$",
        r"^i like (.+)$",
        r"^i enjoy (.+)$",
        r"^i prefer (.+)$",
        r"^i am interested in (.+)$",
        r"^i'?m interested in (.+)$",
        r"^im interested in (.+)$",
        r"^i am also interested in (.+)$",
        r"^i'?m also interested in (.+)$",
        r"^im also interested in (.+)$",
        r"^interested in (.+)$",
        r"^also interested in (.+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, lower)
        if match:
            value = match.group(1).strip(" .")
            if value and not _is_excluded_profile_like_value(value):
                return value.title()
    return ""


def _all_profile_facts(vault_dir: str | None) -> list[str]:
    """Return every non-empty bullet from PROFILE.md verbatim (for the memory summary)."""
    if not vault_dir:
        return []
    path = os.path.join(vault_dir, "PROFILE.md")
    try:
        raw = open(path, encoding="utf-8").read()
    except FileNotFoundError:
        return []
    facts = []
    for line in raw.splitlines():
        text = line.strip().lstrip("-").strip()
        if text and not text.startswith("#"):
            facts.append(text)
    return facts


def _semantic_memory_is_like(memory_type: str, predicate: str) -> bool:
    if memory_type in {"generic_interest", "activity_preference", "creative_preference", "food_preference"}:
        return True
    return predicate in {"likes", "interested_in", "prefers"}


def _is_negative_profile_fact(text: str) -> bool:
    return bool(
        re.search(r"\b(?:do not|don't|dont|not)\s+(?:like|enjoy|prefer|care for|interested in)\b", text)
        or re.search(r"\b(?:dislike|hate|can't stand|cannot stand)\b", text)
        or re.search(r"\bnot\s+interested\s+in\b", text)
    )


def _is_excluded_profile_like_value(value: str) -> bool:
    return bool(
        value.startswith("watching ")
        or value.startswith("to watch ")
        or value.startswith("less ")
        or re.search(r"\b(?:rarely|not often|used to|because of my|injury|injured|pain)\b", value)
    )


def _item_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _item_detail(item: _LikeItem) -> str:
    if item.style:
        return f" — {item.style}."
    return "."
