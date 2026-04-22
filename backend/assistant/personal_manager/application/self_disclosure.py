"""Deterministic self-disclosure handling for PM personalization."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..domain.types import PMAction
from ..executors.memory import execute_memory_action
from ..parsing.text import _norm
from ..persistence.personalization import upsert_user_preference
from ..persistence.recent_context import RecentContextRecord, upsert_activity_context
from ..persistence.semantic_memory import upsert_semantic_memory
from .validators import _contains_sensitive_terms


@dataclass(frozen=True)
class ActivityDisclosure:
    activity: str
    label: str
    category: str
    profile_fact: str
    reply: str
    confidence: float
    create_preference: bool
    create_topic: bool
    save_profile: bool


_ACTIVITY_ALIASES: dict[str, tuple[str, str, str]] = {
    "basketball": ("basketball", "Basketball", "exercise"),
    "tennis": ("tennis", "Tennis", "exercise"),
    "soccer": ("soccer", "Soccer", "exercise"),
    "football": ("football", "Football", "exercise"),
    "gym": ("gym", "Gym", "exercise"),
    "workout": ("workout", "Workout", "exercise"),
    "working out": ("workout", "Workout", "exercise"),
    "run": ("run", "Running", "exercise"),
    "running": ("run", "Running", "exercise"),
    "jog": ("run", "Running", "exercise"),
    "jogging": ("run", "Running", "exercise"),
    "yoga": ("yoga", "Yoga", "exercise"),
    "swim": ("swimming", "Swimming", "exercise"),
    "swimming": ("swimming", "Swimming", "exercise"),
    "walking": ("walk", "Walking", "exercise"),
    "walk": ("walk", "Walking", "exercise"),
    "reading": ("reading", "Reading", "personal"),
    "read": ("reading", "Reading", "personal"),
    "piano": ("piano", "Piano", "creative"),
    "guitar": ("guitar", "Guitar", "creative"),
    "drawing": ("drawing", "Drawing", "creative"),
    "painting": ("painting", "Painting", "creative"),
}

_NEGATIVE_OR_CONSTRAINED = (
    r"\btrying\s+to\s+(?:play|do|go|run|work\s*out)?\s*less\b",
    r"\b(play|do|go|run|work\s*out)\s+less\b",
    r"\b(can't|cannot|cant|shouldn't|should not|avoid|stopped|quit)\b",
    r"\b(knee|injury|injured|pain|doctor|medical|health)\b",
    # Explicit negative sentiment — "but I hate it", "I dislike it".
    r"\b(hate|hated|hates|detest|loathe|dislike|disliked)\b",
    r"\bdon'?t\s+(?:really\s+|actually\s+)?(?:like|enjoy)\b",
)
_LOW_FREQUENCY = (
    r"\brarely\b",
    r"\bnot\s+often\b",
    r"\bonce\s+in\s+a\s+while\b",
)
_QUALIFIED = (
    r"\bsometimes\b",
    r"\boccasionally\b",
)


def analyze_activity_disclosure(message: str) -> ActivityDisclosure | None:
    """Return the first conservative activity disclosure, or None if unrelated."""
    disclosures = analyze_activity_disclosures(message)
    return disclosures[0] if disclosures else None


def analyze_activity_disclosures(message: str) -> list[ActivityDisclosure]:
    """Return conservative activity disclosures from one user message."""
    text = _norm(message)
    if not text:
        return []
    activities = _extract_activities(text)
    if not activities:
        return []
    activity, label, category = activities[0]

    if any(re.search(pattern, text) for pattern in _NEGATIVE_OR_CONSTRAINED):
        return [
            ActivityDisclosure(
                activity=activity,
                label=label,
                category=category,
                profile_fact=f"User mentioned a constraint around {label.lower()}",
                reply=f"Got it — I won't treat {label.lower()} as a scheduling preference.",
                confidence=0.0,
                create_preference=False,
                create_topic=False,
                save_profile=False,
            )
        ]

    comparative = re.search(r"\bi\s+(?:also\s+)?like\s+(.+?)\s+more\s+than\s+(.+)$", text)
    if comparative and activity in _extract_activity_names(comparative.group(1)):
        return [_preferred_disclosure(activity, label, category, confidence=0.60, message=text)]

    disclosures: list[ActivityDisclosure] = []
    for activity, label, category in activities:
        if _activity_has_past_or_watch_only(text, activity):
            fact = _profile_only_fact(text, label)
            # Past-tense ("I used to play X") isn't a current preference, so it
            # shouldn't land in profile.md as a scheduling signal. Watch-only
            # ("I like watching X") is still valid user context — keep it.
            is_past = _activity_has_past_only(text, activity)
            disclosures.append(
                ActivityDisclosure(
                    activity=activity,
                    label=label,
                    category=category,
                    profile_fact=fact,
                    reply=_profile_only_reply(label),
                    confidence=0.0,
                    create_preference=False,
                    create_topic=False,
                    save_profile=not is_past,
                )
            )
            continue

        if _activity_has_low_frequency(text, activity) or (
            len(activities) == 1 and any(re.search(pattern, text) for pattern in _LOW_FREQUENCY)
        ):
            disclosures.append(
                ActivityDisclosure(
                    activity=activity,
                    label=label,
                    category=category,
                    profile_fact=f"User likes {label.lower()} but rarely does it",
                    reply=_profile_only_reply(label),
                    confidence=0.0,
                    create_preference=False,
                    create_topic=False,
                    save_profile=True,
                )
            )
            continue

        if _is_direct_current_activity_statement(text, activity) or _activity_has_current_interest_context(text, activity):
            confidence = 0.55 if any(re.search(pattern, text) for pattern in _QUALIFIED) else 0.75
            disclosures.append(_preferred_disclosure(activity, label, category, confidence=confidence, message=text))
            continue

        if _activity_is_fun_statement(text, activity):
            disclosures.append(
                ActivityDisclosure(
                    activity=activity,
                    label=label,
                    category=category,
                    profile_fact=f"User thinks {label.lower()} is fun",
                    reply=_profile_only_reply(label),
                    confidence=0.0,
                    create_preference=False,
                    create_topic=False,
                    save_profile=True,
                )
            )
    return disclosures


def activity_disclosure_reply(disclosures: list[ActivityDisclosure]) -> str:
    if not disclosures:
        return ""
    if len(disclosures) == 1:
        return disclosures[0].reply
    preferred = [item.label.lower() for item in disclosures if item.create_preference]
    if preferred:
        return f"Got it — I'll remember that you like {_format_label_list(preferred)}. If you want to schedule one, tell me a day/time."
    return "Got it — I'll remember those notes."


def apply_activity_disclosure(
    disclosure: ActivityDisclosure,
    config: Any,
    session_id: str,
    data_dir: str,
) -> list[str]:
    """Persist a disclosure and return memory tags written for decision logging."""
    written: list[str] = []
    if disclosure.save_profile and not _contains_sensitive_terms(disclosure.profile_fact):
        result = execute_memory_action(
            PMAction("remember", {"fact": disclosure.profile_fact}),
            config,
            session_id,
            data_dir,
        )
        if result and result.get("ok"):
            written.append("profile_md")

    if disclosure.create_preference:
        upsert_user_preference(
            session_id,
            data_dir,
            scope_type="event_type",
            scope_key=disclosure.activity,
            rule_type="preferred_activity",
            value={
                "activity": disclosure.activity,
                "label": disclosure.label,
                "category": disclosure.category,
            },
            confidence=disclosure.confidence,
            source="self_disclosure",
        )
        written.append("preferences")
        upsert_semantic_memory(
            session_id,
            data_dir,
            memory_type="activity_preference" if disclosure.category == "exercise" else "generic_interest",
            subject="user",
            predicate="likes",
            object_value=disclosure.activity,
            qualifiers={
                "label": disclosure.label,
                "category": disclosure.category,
            },
            polarity="positive",
            confidence=disclosure.confidence,
            stability="stable" if disclosure.confidence >= 0.7 else "tentative",
            scheduling_relevance="strong" if disclosure.category == "exercise" else "weak",
            sensitivity="low",
            source="self_disclosure",
            evidence=disclosure.profile_fact,
        )
        written.append("semantic_memory")

    if disclosure.create_topic:
        upsert_activity_context(
            session_id,
            data_dir,
            activity=disclosure.activity,
            payload={
                "activity": disclosure.activity,
                "activity_label": disclosure.label,
                "category": disclosure.category,
                "title_seed": disclosure.label,
                "source_message": disclosure.profile_fact,
                "assistant_invited_schedule": True,
                "last_agent_action": "invited_activity_schedule",
            },
        )
        written.append("recent_context:activity_topic")
    return written


def style_from_activity_followup(message: str) -> str | None:
    text = _norm(message)
    if re.search(r"\b(casual|causal|for fun|just play|just playing)\b", text):
        return "casual"
    if re.search(r"\b(training|practice|skill|drill)\b", text):
        return "training"
    if re.search(r"\b(competitive|league|serious|tournament)\b", text):
        return "competitive"
    return None


def apply_style_to_latest_activity_context(
    message: str,
    contexts: list[RecentContextRecord],
    config: Any,
    session_id: str,
    data_dir: str,
) -> str | None:
    style = style_from_activity_followup(message)
    if not style or not contexts:
        return None
    latest = contexts[0]
    activity = str(latest.payload.get("activity") or "")
    label = str(latest.payload.get("activity_label") or activity.title())
    if not activity:
        return None
    upsert_activity_context(
        session_id,
        data_dir,
        activity=activity,
        payload={
            **latest.payload,
            "style": style,
            "assistant_invited_schedule": True,
            "last_agent_action": "invited_activity_schedule",
        },
    )
    action = "practice" if str(latest.payload.get("category") or "") == "creative" else "play"
    return f"Got it — {style} {label.lower()}. Give me a day/time when you want to {action} and I'll help set it up."


def _preferred_disclosure(
    activity: str,
    label: str,
    category: str,
    *,
    confidence: float,
    message: str,
) -> ActivityDisclosure:
    return ActivityDisclosure(
        activity=activity,
        label=label,
        category=category,
        profile_fact=_preference_fact(message, label),
        reply=_preference_reply(label, category),
        confidence=confidence,
        create_preference=True,
        create_topic=True,
        save_profile=True,
    )


def _extract_activity(text: str) -> tuple[str, str, str] | None:
    activities = _extract_activities(text)
    return activities[0] if activities else None


def _extract_activities(text: str) -> list[tuple[str, str, str]]:
    matches: list[tuple[int, int, tuple[str, str, str]]] = []
    for alias, info in _ACTIVITY_ALIASES.items():
        for match in re.finditer(rf"\b{re.escape(alias)}\b", text):
            matches.append((match.start(), -len(alias), info))
    matches.sort()
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for _, _, info in matches:
        activity = info[0]
        if activity in seen:
            continue
        seen.add(activity)
        found.append(info)
    return found


def _extract_activity_names(text: str) -> set[str]:
    return {activity for activity, _, _ in _extract_activities(text)}


def _is_direct_current_activity_statement(text: str, activity: str) -> bool:
    activity_pattern = _activity_pattern(activity)
    return bool(
        re.search(rf"\bi\s+(?:also\s+)?(?:like|love|enjoy|prefer)\s+(?:playing\s+|doing\s+|going\s+to\s+)?(?:{activity_pattern})\b", text)
        or re.search(rf"\bi\s+(?:also\s+)?(?:play|do)\s+(?:{activity_pattern})\b", text)
        or re.search(rf"\bi'?m\s+(?:really\s+)?into\s+(?:{activity_pattern})\b", text)
    )


def _activity_has_current_interest_context(text: str, activity: str) -> bool:
    activity_pattern = _activity_pattern(activity)
    return bool(
        re.search(rf"\bi\s+(?:also\s+)?(?:like|love|enjoy|prefer)\b.+\b(?:playing\s+|doing\s+|going\s+to\s+)?(?:{activity_pattern})\b", text)
        or re.search(rf"\bi\s+(?:also\s+)?(?:play|do)\b.+\b(?:{activity_pattern})\b", text)
        or re.search(rf"\bi'?m\s+(?:really\s+)?into\b.+\b(?:{activity_pattern})\b", text)
        or re.search(rf"\bplaying\s+(?:{activity_pattern})\b", text)
    )


def _activity_has_past_or_watch_only(text: str, activity: str) -> bool:
    activity_pattern = _activity_pattern(activity)
    return bool(
        re.search(rf"\b(?:watch|watching)\s+(?:{activity_pattern})\b", text)
        or re.search(rf"\bi\s+used\s+to\s+(?:play|do|practice)?\s*(?:{activity_pattern})\b", text)
        or re.search(rf"\bi\s+liked\s+(?:playing\s+|doing\s+|practicing\s+)?(?:{activity_pattern})\b", text)
    )


def _activity_has_past_only(text: str, activity: str) -> bool:
    """Specifically past-tense ('used to', 'I liked') — not watch-only."""
    activity_pattern = _activity_pattern(activity)
    return bool(
        re.search(rf"\bi\s+used\s+to\s+(?:play|do|practice)?\s*(?:{activity_pattern})\b", text)
        or re.search(rf"\bi\s+liked\s+(?:playing\s+|doing\s+|practicing\s+)?(?:{activity_pattern})\b", text)
    )


def _activity_has_low_frequency(text: str, activity: str) -> bool:
    activity_pattern = _activity_pattern(activity)
    return bool(
        re.search(rf"\b(?:rarely|not\s+often)\s+(?:play|do|practice)?\s*(?:{activity_pattern})\b", text)
        or re.search(rf"\b(?:{activity_pattern})\b[^,.]*\b(?:rarely|not\s+often|once\s+in\s+a\s+while)\b", text)
    )


def _activity_is_fun_statement(text: str, activity: str) -> bool:
    activity_pattern = _activity_pattern(activity)
    return bool(re.search(rf"\b(?:{activity_pattern})\s+is\s+fun\b", text))


def _activity_pattern(activity: str) -> str:
    aliases = [
        alias
        for alias, (canonical, _, _) in _ACTIVITY_ALIASES.items()
        if canonical == activity
    ]
    return "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))


def _format_label_list(labels: list[str]) -> str:
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"


def _preference_fact(message: str, label: str) -> str:
    if "more than" in message:
        return f"User likes {label.lower()} more than the compared activity"
    if "sometimes" in message or "occasionally" in message:
        return f"User sometimes likes {label.lower()}"
    return f"User likes playing {label.lower()}"


def _preference_reply(label: str, category: str) -> str:
    if category == "creative":
        return f"Got it — I'll remember that you like {label.lower()}. For fun, practice, or performance?"
    if category == "exercise":
        return f"Got it — I'll remember that you like {label.lower()}. Casual, training, or competitive?"
    return f"Got it — I'll remember that you like {label.lower()}."


def _profile_only_fact(text: str, label: str) -> str:
    if "watching" in text or re.search(r"\bi\s+watch\b", text):
        return f"User likes watching {label.lower()}"
    if "used to" in text:
        return f"User used to play {label.lower()}"
    return f"User mentioned {label.lower()}"


def _profile_only_reply(label: str) -> str:
    return f"Got it — I'll remember that note about {label.lower()}."
