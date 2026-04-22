"""Schedule resolver: vague request → concrete schedule targets."""
from __future__ import annotations

import re
from typing import Any

from ..calendar.service import CalendarService, is_google_calendar_connected
from ..calendar.store import mark_mirror_deleted
from ..domain.schedule import GoogleCalendarRef, ScheduleResolution, ScheduleTarget
from ..parsing.text import _norm
from ..persistence.store import _entry_recurrence, load_schedule


_GENERIC_EVENT_TOKENS = frozenset({
    "call", "meeting", "event", "session", "appointment", "task", "thing",
    "chat", "sync", "touchbase",
})


def _title_match(query: str, title: str) -> bool:
    """Substring first, then token-overlap with plural collapsing.

    Users phrase lookups loosely ("standups" for "Morning standup", "meetings
    with Alex" for "Coffee with Alex"). A strict substring check is too brittle;
    this falls back to matching on significant tokens with simple plural
    normalization. If the query collapses to only generic event-type tokens
    ("call", "meeting", …) we refuse the fuzzy fallback so the turn surfaces a
    clarification rather than picking an arbitrary match.
    """
    qnorm = _norm(query)
    tnorm = _norm(title)
    if not qnorm:
        return True
    if qnorm in tnorm:
        return True
    title_tokens = {_singularize(tok) for tok in re.findall(r"[a-z0-9]+", tnorm)}
    query_tokens = [_singularize(tok) for tok in re.findall(r"[a-z0-9]+", qnorm) if len(tok) >= 3]
    if not query_tokens:
        return False
    specific = [tok for tok in query_tokens if tok not in _GENERIC_EVENT_TOKENS]
    if not specific:
        return False
    return any(tok in title_tokens for tok in specific)


def _singularize(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("es") and not token.endswith("ses"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def resolve_schedule_targets(session_id: str, data_dir: str, payload: dict[str, Any]) -> ScheduleResolution:
    google_primary = is_google_calendar_connected(session_id, data_dir)
    data = load_schedule(session_id, data_dir)
    if payload.get("id") and not google_primary:
        matches = [e for e in data.entries if e.id == payload["id"]]
        google_matches = [] if matches else _matching_google_schedule_refs(session_id, data_dir, payload)
    else:
        query = _norm(payload.get("query", ""))
        if google_primary:
            matches = []
        else:
            matches = list(data.entries)
            if payload.get("reference_date"):
                dated = [e for e in matches if e.date == payload["reference_date"]]
                if dated:
                    matches = dated
                else:
                    # No exact date match — fall back to dateless entries (which
                    # carry no date conflict) so the query can still resolve them.
                    matches = [e for e in matches if not e.date]
            if query:
                matches = [e for e in matches if _title_match(query, e.title)]
            if payload.get("start"):
                time_matches = [e for e in matches if e.start == payload["start"]]
                if time_matches:
                    matches = time_matches
            if payload.get("end"):
                end_matches = [e for e in matches if e.end == payload["end"]]
                if end_matches:
                    matches = end_matches
            if not matches and payload.get("start"):
                # Time-only reference: "delete my 3pm meeting" -> filter by start time
                pool = list(data.entries)
                if payload.get("reference_date"):
                    pool = [e for e in pool if e.date == payload["reference_date"]]
                matches = [e for e in pool if e.start == payload["start"]]
            category = payload.get("category")
            if category:
                category_matches = [e for e in matches if category in _norm(e.title)]
                if category_matches:
                    matches = category_matches
            if payload.get("ordinal"):
                ordinal = int(payload["ordinal"])
                matches = sorted(matches, key=lambda e: (e.date or "", e.start or "", e.title.lower(), e.id))
                if ordinal < 1 or ordinal > len(matches):
                    return ScheduleResolution(ok=False, message=f"I could not find the #{ordinal} matching schedule event.")
                matches = [matches[ordinal - 1]]
        google_matches = _matching_google_schedule_refs(session_id, data_dir, payload)
    total_matches = len(matches) + len(google_matches)
    if total_matches == 0:
        return ScheduleResolution(ok=False, message="I could not find a matching schedule event.")
    targets = [
        *[_local_schedule_target(match, payload) for match in matches],
        *[_google_schedule_target(match, payload) for match in google_matches],
    ]
    if total_matches > 1 and payload.get("bulk"):
        return ScheduleResolution(ok=True, targets=targets)
    if total_matches > 1:
        local_choices = [f"{e.id}:{e.title}" for e in matches]
        google_choices = [f"google:{e['id']}:{e['title']}" for e in google_matches]
        choices = ", ".join([*local_choices, *google_choices])
        return ScheduleResolution(ok=False, message=f"Multiple schedule events matched. Use an id: {choices}")
    return ScheduleResolution(ok=True, targets=targets)


def _resolve_schedule_refs(session_id: str, data_dir: str, payload: dict[str, Any]) -> dict[str, Any]:
    return resolve_schedule_targets(session_id, data_dir, payload).to_legacy()


def _local_schedule_target(event: Any, payload: dict[str, Any]) -> ScheduleTarget:
    occurrence_date = str(payload.get("original_date") or payload.get("skip_date") or "")
    recurrence = _entry_recurrence(event)
    if occurrence_date:
        kind = "occurrence"
    elif recurrence is not None:
        kind = "series"
    else:
        kind = "one_off"
    return ScheduleTarget(
        kind=kind,
        id=event.id,
        title=event.title,
        date=event.date or "",
        start=event.start or "",
        end=event.end or "",
        series_id=event.series_id or event.id if recurrence is not None or occurrence_date else "",
        occurrence_date=occurrence_date,
    )


def _google_schedule_target(ref: dict[str, Any], payload: dict[str, Any]) -> ScheduleTarget:
    google = GoogleCalendarRef.from_legacy(ref)
    return ScheduleTarget(
        kind="google_mirror",
        id=google.id,
        title=google.title,
        date=google.date,
        start=google.start,
        end=google.end,
        series_id=google.recurring_event_id,
        occurrence_date=str(payload.get("original_date") or payload.get("skip_date") or google.date or ""),
        google=google,
    )


def _matching_google_schedule_refs(session_id: str, data_dir: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        events = CalendarService(session_id, data_dir).list_events(limit=1000)
    except Exception:
        return []
    refs = [_google_event_ref(event) for event in events]
    if payload.get("id"):
        target = str(payload["id"])
        return [ref for ref in refs if ref["id"] == target or ref["providerEventId"] == target]
    query = _norm(payload.get("query", ""))
    matches = refs
    range_start = payload.get("range_start")
    range_end = payload.get("range_end")
    if range_start:
        matches = [ref for ref in matches if ref["date"] >= range_start]
    if range_end:
        matches = [ref for ref in matches if ref["date"] <= range_end]
    if payload.get("reference_date"):
        matches = [ref for ref in matches if ref["date"] == payload["reference_date"]]
    if query:
        matches = [ref for ref in matches if _title_match(query, ref["title"])]
    if payload.get("start"):
        time_matches = [ref for ref in matches if ref["start"] == payload["start"]]
        if time_matches:
            matches = time_matches
    if payload.get("end"):
        end_matches = [ref for ref in matches if ref["end"] == payload["end"]]
        if end_matches:
            matches = end_matches
    if not matches and payload.get("start"):
        pool = refs
        if payload.get("reference_date"):
            pool = [ref for ref in pool if ref["date"] == payload["reference_date"]]
        matches = [ref for ref in pool if ref["start"] == payload["start"]]
    category = payload.get("category")
    if category:
        category_matches = [ref for ref in matches if category in _norm(ref["title"])]
        if category_matches:
            matches = category_matches
    if payload.get("ordinal"):
        ordinal = int(payload["ordinal"])
        matches = sorted(matches, key=lambda ref: (ref["date"], ref["start"], ref["title"].lower(), ref["id"]))
        if ordinal < 1 or ordinal > len(matches):
            return []
        matches = [matches[ordinal - 1]]
    return matches


def _google_delete_targets_for_events(events: list[dict[str, Any]]) -> list[str]:
    targets: list[str] = []
    seen: set[str] = set()
    for event in events:
        provider_event_id = str(event.get("recurringEventId") or event.get("providerEventId") or "")
        if not provider_event_id or provider_event_id in seen:
            continue
        seen.add(provider_event_id)
        targets.append(provider_event_id)
    return targets


def _hide_google_mirror_refs(session_id: str, data_dir: str, events: list[dict[str, Any]]) -> None:
    for event in events:
        account_id = str(event.get("accountId") or "")
        provider_event_id = str(event.get("providerEventId") or "")
        if not account_id or not provider_event_id:
            continue
        mark_mirror_deleted(
            session_id,
            data_dir,
            account_id=account_id,
            provider_event_id=provider_event_id,
        )


def _google_event_ref(event: Any) -> dict[str, Any]:
    event_date, event_start = _split_calendar_ref_when(event.start_at)
    _, event_end = _split_calendar_ref_when(event.end_at)
    raw = event.raw if isinstance(event.raw, dict) else {}
    return {
        "id": event.id,
        "providerEventId": event.provider_event_id,
        "recurringEventId": str(raw.get("recurringEventId") or ""),
        "accountId": event.account_id,
        "title": event.title,
        "date": event_date,
        "start": event_start,
        "end": event_end,
    }


def _split_calendar_ref_when(value: str) -> tuple[str, str]:
    if not value:
        return "", ""
    if "T" not in value:
        return value[:10], ""
    date_part, time_part = value.split("T", 1)
    return date_part[:10], time_part[:5]


__all__ = [
    "resolve_schedule_targets",
    "_resolve_schedule_refs",
    "_google_delete_targets_for_events",
    "_hide_google_mirror_refs",
    "_google_event_ref",
    "_split_calendar_ref_when",
    "_matching_google_schedule_refs",
]
