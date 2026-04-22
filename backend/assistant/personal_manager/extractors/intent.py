"""Intent classification helpers for the PM workflow."""
from __future__ import annotations

import re

from ..calendar.recurrence import _extract_recurring_weekdays
from ..domain.types import PMIntent
from ..parsing.datetime import _has_explicit_time_signal, _parse_date
from ..parsing.schedule_text import (
    _looks_like_ambiguous_schedulable_routine,
    _looks_like_bare_schedule_slot,
    _looks_like_schedule_creation_command,
    _looks_like_scheduled_life_event,
)
from ..parsing.text import _norm


def classify_pm_intent(message: str) -> PMIntent:
    text = _norm(message)
    if _looks_like_injection(text):
        return PMIntent.UNKNOWN
    # Leading ack + correction marker is ambiguous without pending state: "yes
    # and move it later" or "confirm the one from earlier" could be approval-
    # with-edit or a fresh request. Workflow owns the stateful decision. Plain
    # emphatic acks ("yes, do it", "approve it") still route as APPROVE_ACTION.
    if _LEADING_ACK_PATTERN.match(text) and _ACK_CORRECTION_MARKERS.search(text):
        return PMIntent.UNKNOWN
    if re.match(r"^\s*(approve|confirm|yes)\b", text):
        return PMIntent.APPROVE_ACTION
    if re.match(r"^\s*(reject|deny|no|cancel approval)\b", text):
        return PMIntent.REJECT_ACTION

    # Leading removal verb + generic schedule-ish noun — route to the remove
    # path before other rules get a chance. Prevents "clear my schedule" and
    # "remove everything" from misclassifying as CREATE/UPDATE because
    # "schedule"/"event" appears in the text. Specificity (id/title/window)
    # and confidence are handled downstream; this rule just anchors intent.
    # Series markers ("all future", "going forward") take precedence via the
    # dedicated CANCEL_SERIES_FROM branch further down.
    if (
        _REMOVE_VERB_LEADING.match(text)
        and _REMOVE_SCHEDULE_NOUN.search(text)
        and not _SERIES_REMOVAL_MARKER.search(text)
    ):
        return PMIntent.REMOVE_SCHEDULE_EVENT

    if "export" in text and ("private" in text or "memory" in text):
        return PMIntent.SAVE_MEMORY
    if text.startswith("remember ") or text.startswith("remember that "):
        return PMIntent.SAVE_MEMORY
    if _looks_like_web_search_request(text):
        return PMIntent.GENERAL_COACHING

    if text.startswith(("add task", "add a task", "create task", "create a task", "new task")):
        return PMIntent.CREATE_TODO
    if text.startswith("remind me"):
        return PMIntent.CREATE_TODO
    if "todo" in text and any(w in text for w in ("add", "create", "new")):
        return PMIntent.CREATE_TODO

    if text.startswith("log ") or "journal" in text or "reflection" in text or "daily log" in text:
        return PMIntent.JOURNAL_ACTION
    if _looks_like_pm_coaching_prompt(message):
        return PMIntent.GENERAL_COACHING
    if "habit" in text or "streak" in text or "check in" in text or "checkin" in text:
        return PMIntent.HABIT_ACTION

    if any(w in text for w in ("show", "list", "what are", "what's on", "what is", "what's in", "whats in", "what do i have", "tell me my", "read my", "when am i", "when do i have", "when is my", "when is the", "when's my", "when's the", "do i have any", "do i have a", "check my", "check the")):
        if any(w in text for w in ("todo", "task", "schedule", "calendar", "habit", "journal", "meeting", "appointment", "event")):
            return PMIntent.LIST_STATE

    # "what do I need to do this week / today / tomorrow?" → schedule overview
    if re.search(r"\bwhat\s+do\s+i\s+need\s+to\s+do\b|\bwhat\s+should\s+i\s+do\b|\bwhat(?:'s|s)?\s+coming\s+up\b", text):
        if any(w in text for w in ("today", "tomorrow", "this week", "next week", "this month", "week", "day")):
            return PMIntent.LIST_STATE

    # Series-scoped operations must be checked before generic remove/update.
    if re.search(r"\ball\s+future\b|\bfrom\s+(?:now|today)\s+on\b|\bgoing\s+forward\b", text):
        if any(w in text for w in ("cancel", "delete", "remove", "stop", "end")):
            return PMIntent.CANCEL_SERIES_FROM

    if re.search(r"\bskip\b", text) and not any(w in text for w in ("task", "todo")):
        return PMIntent.SKIP_OCCURRENCE

    if re.search(r"\bjust\s+this\b|\bonly\s+this\b|\bthis\s+(?:one|occurrence|instance)\b", text):
        if any(w in text for w in ("move", "reschedule", "change", "update", "shift")):
            return PMIntent.MODIFY_OCCURRENCE

    if any(w in text for w in ("delete", "remove", "cancel")):
        if any(w in text for w in ("task", "todo")):
            return PMIntent.REMOVE_TODO
        if (any(w in text for w in ("appointment", "event", "meeting", "calendar", "schedule"))
                or re.search(r"\bthing\s+with\b", text)
                or _parse_date(text) or _has_explicit_time_signal(text)):
            return PMIntent.REMOVE_SCHEDULE_EVENT

    if any(w in text for w in ("complete", "done", "finish", "finished", "mark")):
        if any(w in text for w in ("task", "todo")):
            return PMIntent.COMPLETE_TODO

    if any(w in text for w in ("move", "reschedule", "update", "change", "push", "shift")):
        if any(w in text for w in ("appointment", "event", "meeting", "calendar", "schedule", "dentist", "doctor", "thing")):
            return PMIntent.UPDATE_SCHEDULE_EVENT
        if _parse_date(text) or _has_explicit_time_signal(text):
            return PMIntent.UPDATE_SCHEDULE_EVENT
        if not any(w in text for w in ("task", "todo", "habit")):
            return PMIntent.UPDATE_SCHEDULE_EVENT

    if _extract_recurring_weekdays(text) and _has_explicit_time_signal(text):
        lower = _norm(text)
        if re.search(r"\b(add|create|book|schedule|put|protect|block|wanna|want\s+to|need\s+to|gonna|going\s+to|have\s+to|i'm\s+)\b", lower):
            return PMIntent.CREATE_SCHEDULE_EVENT
        if _looks_like_scheduled_life_event(text):
            return PMIntent.CREATE_SCHEDULE_EVENT

    if _looks_like_schedule_creation_command(text):
        return PMIntent.CREATE_SCHEDULE_EVENT

    if _looks_like_ambiguous_schedulable_routine(text):
        return PMIntent.CREATE_SCHEDULE_EVENT

    if _looks_like_scheduled_life_event(text):
        return PMIntent.CREATE_SCHEDULE_EVENT

    if _looks_like_bare_schedule_slot(text):
        return PMIntent.CREATE_SCHEDULE_EVENT

    if any(w in text for w in ("schedule", "calendar", "appointment", "meeting", "event")):
        if any(w in text for w in ("add", "create", "book", "put", "schedule")):
            return PMIntent.CREATE_SCHEDULE_EVENT

    if text.startswith(("add task", "add a task", "create task", "create a task", "new task")):
        return PMIntent.CREATE_TODO
    if text.startswith("remind me"):
        return PMIntent.CREATE_TODO
    if "todo" in text and any(w in text for w in ("add", "create", "new")):
        return PMIntent.CREATE_TODO

    if any(w in text for w in ("plan my day", "help me plan", "coach", "overwhelmed")):
        return PMIntent.GENERAL_COACHING

    # Bare-add fallback: "add <something>" with no schedule/habit/time context
    # defaults to a todo. Schedule-shaped adds ("add meeting with Alex 3pm")
    # and habit adds have already been captured earlier, so this only fires
    # for loose phrases like "add pick up dry cleaning" that would otherwise
    # fall off the cliff as UNKNOWN. A "word:" shape ("add tsk: ...") signals
    # a typo'd command rather than natural-language — keep those as UNKNOWN.
    if (
        _BARE_ADD_PREFIX.match(text)
        and not _BARE_ADD_COLON_FORM.match(text)
        and not any(
            w in text
            for w in (
                "schedule", "calendar", "appointment", "meeting", "event",
                "habit", "streak", "journal", "reflection",
            )
        )
    ):
        return PMIntent.CREATE_TODO

    return PMIntent.UNKNOWN


_LEADING_ACK_PATTERN = re.compile(
    r"^\s*(?:yes|yeah|yep|yup|no|nope|nah|approve|reject|confirm|sure|ok|okay)\b",
    re.IGNORECASE,
)

# A leading ack is ambiguous when the trailing clause carries a *correction*
# signal (time, date, duration, move/reschedule verbs, "instead", "earlier").
# Plain emphatic follow-ups like "yes, do it" or "approve it" carry no
# correction marker and fall through to APPROVE_ACTION as before.
_ACK_CORRECTION_MARKERS = re.compile(
    r"\b(?:later|earlier|tomorrow|today|tonight|instead|actually|"
    r"sunday|monday|tuesday|wednesday|thursday|friday|saturday|"
    r"move|reschedule|shift|push|postpone|change\s+to|"
    r"\d{1,2}\s*(?:am|pm|a\.m\.|p\.m\.)|"
    r"\d+\s*(?:min|mins|minute|minutes|hr|hrs|hour|hours))\b",
    re.IGNORECASE,
)


_REMOVE_VERB_LEADING = re.compile(
    r"^\s*(?:please\s+)?(?:delete|remove|cancel|clear|wipe|nuke)\b",
    re.IGNORECASE,
)
# Generic/vague schedule-ish nouns that appear after a leading removal verb.
# Specific targets (named events, ids, explicit times) are captured later in
# the flow; here we only need to *anchor* intent as removal.
_REMOVE_SCHEDULE_NOUN = re.compile(
    r"\b(?:schedule|calendar|event|events|meeting|meetings|appointment|appointments|"
    r"plan|plans|thing|things|stuff|agenda|day|week|everything)\b",
    re.IGNORECASE,
)
_SERIES_REMOVAL_MARKER = re.compile(
    r"\ball\s+future\b|\bfrom\s+(?:now|today)\s+on\b|\bgoing\s+forward\b",
    re.IGNORECASE,
)
_BARE_ADD_PREFIX = re.compile(
    r"^\s*(?:please\s+)?(?:add|create)\s+\S",
    re.IGNORECASE,
)
# "add <word>:" reads as a typo'd slash-command, not a natural-language ask.
_BARE_ADD_COLON_FORM = re.compile(
    r"^\s*(?:please\s+)?(?:add|create)\s+\S+\s*:",
    re.IGNORECASE,
)


# Web-search intent triggers. A match here routes GENERAL_COACHING; the
# planner only creates an approval when entities.sensitive=True, so non-
# sensitive lookups (coffee shops, recipes) pass through with action=none.
_WEB_SEARCH_FIND_ONLINE = re.compile(
    r"\bfind\s+\w.*?\b(?:online|on\s+google|via\s+google)\b",
    re.IGNORECASE,
)


def _looks_like_web_search_request(text: str) -> bool:
    lower = text.strip().lower()
    if (
        "web search" in lower
        or "search the web" in lower
        or "search the internet" in lower
        or "search online" in lower
        or " on google" in lower
        or " from google" in lower
    ):
        return True
    if (
        lower.startswith("search for ")
        or lower.startswith("google for ")
        or lower.startswith("google search ")
        or lower.startswith("look up ")
        or lower.startswith("lookup ")
    ):
        return True
    if _WEB_SEARCH_FIND_ONLINE.search(lower):
        return True
    return False


_INJECTION_PATTERNS = (
    r"\bignore\s+(?:previous|all|prior|any|the)\s+(?:instructions?|rules?|prompts?|directives?|context)\b",
    r"\bdisregard\s+(?:previous|all|prior|the)\s+(?:instructions?|rules?|context)\b",
    r"^\s*system\s*[:：]",
    r"^\s*assistant\s*[:：]",
    r"\bpretend\s+(?:you|i|we)\s+(?:said|are|did|were|had)\b",
    r"\bnew\s+instructions?\s*[:：\-]",
    r"</?(?:system|user|assistant)\s*>",
)


def _looks_like_injection(text: str) -> bool:
    lower = text.strip().lower()
    return any(re.search(p, lower) for p in _INJECTION_PATTERNS)


_SELF_DISCLOSURE_PATTERNS = (
    r"^my (favorite|favourite|preferred|usual|typical|go-to|default)\b",
    r"^i (prefer|love|like|enjoy|hate|dislike|can't stand|adore)\b",
    r"^i('m| am) (a |an )?(morning|night|early|late)\b",
    r"^i (always|usually|never|often|rarely|tend to)\b",
    r"^my (routine|habit|schedule|ritual|practice) (is|are)\b",
    r"^i (go|run|exercise|wake|sleep|eat|work out) (every|each|on)\b",
    r"^(?:i'?m|im|i am) (?:also )?(?:into|interested in|obsessed with|passionate about)\b",
)

_ACTION_REQUEST_MARKERS = (
    r"\b(schedule|book|add|create|remind|set up|block off|put on)\b",
    r"\b(tomorrow|next week|this [a-z]+day|on [a-z]+day)\b",
    r"\b(can you|could you|please|help me|i need you to)\b",
)


def _is_self_disclosure(text: str) -> bool:
    lower = text.strip().lower()
    return any(re.search(p, lower) for p in _SELF_DISCLOSURE_PATTERNS)


def _contains_action_request(text: str) -> bool:
    lower = text.strip().lower()
    return any(re.search(p, lower) for p in _ACTION_REQUEST_MARKERS)


def _safe_for_react_fallback(message: str, intent: PMIntent) -> bool:
    if intent in {PMIntent.UNKNOWN, PMIntent.GENERAL_COACHING}:
        lower = _norm(message)
        if intent == PMIntent.GENERAL_COACHING and _looks_like_pm_coaching_prompt(message):
            return True
        stateful_terms = (
            "add", "create", "delete", "remove", "cancel", "complete", "done",
            "schedule", "calendar", "todo", "task", "habit", "journal",
            "remember", "private", "export", "save", "write", "log", "record",
            "note", "notes", "track", "search the web", "web search", "search for",
            "look up", "lookup", "find online", "move", "reschedule", "update", "change",
            "google for", "google search", "on google", "from google",
            "search online", "search the internet",
        )
        return not any(term in lower for term in stateful_terms)
    return False


def _looks_like_pm_coaching_prompt(message: str) -> bool:
    return _looks_like_planning_coaching_prompt(message) or _looks_like_habit_coaching_prompt(message)


def _looks_like_planning_coaching_prompt(message: str) -> bool:
    lower = _norm(message)
    if not any(
        marker in lower
        for marker in (
            "coach me",
            "help me plan",
            "morning launch",
            "focus sprint",
            "calendar triage",
            "tiny win",
            "focused 60-minute plan",
            "next 60 minutes",
            "short day review",
            "day review",
            "daily check-in",
            "daily check in",
            "risk scan",
            "calendar risks",
            "likely blockers",
            "top priorities",
            "3 wins",
            "one useful action",
            "under 10 minutes",
            "do not change anything",
        )
    ):
        return False
    stateful_patterns = (
        r"\badd\b",
        r"\bcreate\b",
        r"\bbook\b",
        r"\bput\b",
        r"\bschedule\b",
        r"\bdelete\b",
        r"\bremove\b",
        r"\bcancel\b",
        r"\bmove\b",
        r"\breschedule\b",
        r"\bupdate\b",
        r"\bchange\b",
        r"\bremember\b",
        r"\bsave\b",
        r"\bwrite\b",
        r"\blog\b",
        r"\brecord\b",
        r"\btrack\b",
        r"\bsearch\b",
        r"\bexport\b",
    )
    if any(re.search(pattern, lower) for pattern in stateful_patterns):
        if "do not change anything" not in lower:
            return False
    return True


def _looks_like_habit_coaching_prompt(message: str) -> bool:
    lower = _norm(message)
    if not ("habit" in lower or "routine" in lower):
        return False
    if not any(
        marker in lower
        for marker in (
            "nudge me",
            "suggest",
            "recommend",
            "coach me",
            "help me choose",
            "give me one",
            "what habit",
            "what routine",
            "one tiny",
            "one useful",
        )
    ):
        return False
    stateful_patterns = (
        r"\badd\s+(?:a\s+)?habit\b",
        r"\bcreate\s+(?:a\s+)?habit\b",
        r"\bstart\s+(?:a\s+)?habit\b",
        r"\bdelete\s+(?:my\s+|the\s+)?habit\b",
        r"\bremove\s+(?:my\s+|the\s+)?habit\b",
        r"\bcheck\s*in\b",
        r"\bcheckin\b",
        r"\bstreak\b",
        r"\blist\s+(?:my\s+)?habits\b",
        r"\bshow\s+(?:my\s+)?habits\b",
        r"\btrack\s+(?:my\s+|a\s+)?habit\b",
    )
    return not any(re.search(pattern, lower) for pattern in stateful_patterns)
