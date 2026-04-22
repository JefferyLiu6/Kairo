"""Non-mutating time-slot recommendations for the Kairo."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..calendar.service import CalendarService, is_google_calendar_connected
from ..domain.types import PMIntent, PMPlanExtraction, PMTaskExtraction
from ..parsing.datetime import _default_end_time, _parse_date, _parse_time
from ..parsing.text import _norm
from ..persistence.control_store import pm_db_path
from ..persistence.habits import habit_list
from ..persistence.personalization import FieldChoiceRecord, list_field_choices, list_user_preferences, record_choice_shown
from ..persistence.store import _entry_recurrence, expand_series, load_schedule, load_todos
from ..presentation.formatters import _format_date_natural, _format_time_natural

_RECOMMENDATION_INTENT = "TIME_SLOT_RECOMMENDATION"
_RECOMMENDATION_FIELD = "suggestion"


@dataclass(frozen=True)
class _SlotRequest:
    date: str
    start: str
    end: str


@dataclass(frozen=True)
class _Suggestion:
    label: str
    reason: str
    score: float
    kind: str
    category: str = ""


@dataclass(frozen=True)
class TimeSlotRecommendationProposal:
    prompt: str
    plan: PMPlanExtraction
    blocking_task_id: str
    missing: list[str]
    choices: list[dict[str, Any]]


def build_time_slot_recommendation(message: str, config: Any) -> str | None:
    proposal = build_time_slot_recommendation_proposal(message, config)
    return proposal.prompt if proposal is not None else None


def is_time_slot_recommendation_request(message: str) -> bool:
    return _parse_time_slot_request(message) is not None


def build_time_slot_recommendation_proposal(message: str, config: Any) -> TimeSlotRecommendationProposal | None:
    request = _parse_time_slot_request(message)
    if request is None:
        return None
    return _proposal_for_request(request, config)


def replay_time_slot_recommendation(
    pending: dict[str, Any], config: Any
) -> TimeSlotRecommendationProposal | None:
    """Re-run a recommendation for the slot stored in a pending field_choices.

    Used when the user says "something else" / "give me another" — rotation
    state is already persisted, so the next call naturally skips the
    already-shown suggestions.
    """
    plan = pending.get("plan") if isinstance(pending, dict) else None
    if not isinstance(plan, dict):
        return None
    tasks = plan.get("tasks") if isinstance(plan.get("tasks"), list) else None
    if not tasks:
        return None
    entities = tasks[0].get("entities") if isinstance(tasks[0], dict) else None
    if not isinstance(entities, dict) or not entities.get("time_slot_recommendation"):
        return None
    slot_date = entities.get("date")
    slot_start = entities.get("start")
    slot_end = entities.get("end")
    if not (slot_date and slot_start and slot_end):
        return None
    request = _SlotRequest(date=str(slot_date), start=str(slot_start), end=str(slot_end))
    return _proposal_for_request(request, config)


def _proposal_for_request(
    request: _SlotRequest, config: Any
) -> TimeSlotRecommendationProposal:
    sid = str(config.session_id)
    data_dir = str(config.data_dir)
    events = _events_for_date(sid, data_dir, request.date)
    conflict = _first_overlap(request, events)
    if conflict is not None:
        prompt, choices = _format_busy_reply(request, conflict, sid, data_dir)
    else:
        prompt, choices = _format_open_slot_reply(request, sid, data_dir)
    return TimeSlotRecommendationProposal(
        prompt=prompt,
        plan=_recommendation_plan(request),
        blocking_task_id="task-1",
        missing=["date", "start"],
        choices=choices,
    )


def _parse_time_slot_request(message: str) -> _SlotRequest | None:
    lower = _norm(message)
    if not _looks_like_time_slot_advice(lower):
        return None
    start = _parse_time(message)
    if not start:
        return None
    slot_date = _parse_date(message) or date.today().isoformat()
    return _SlotRequest(date=slot_date, start=start, end=_default_end_time(start))


def _looks_like_time_slot_advice(lower: str) -> bool:
    if not any(
        marker in lower
        for marker in (
            "what should i do",
            "what can i do",
            "what do i do",
            "what to do",
            "help me decide",
            "suggest something",
            "recommend something",
            "recommend a thing",
            "give me something",
        )
    ):
        return False
    if any(
        marker in lower
        for marker in (
            "add ",
            "create ",
            "book ",
            "put ",
            "schedule ",
            "delete ",
            "remove ",
            "cancel ",
            "move ",
            "reschedule ",
            "update ",
            "change ",
            "remember ",
            "save ",
            "write ",
            "log ",
            "record ",
            "track ",
            "search ",
            "export ",
        )
    ):
        return False
    return True


def _recommendation_plan(request: _SlotRequest | None = None) -> PMPlanExtraction:
    entities: dict[str, Any] = {"time_slot_recommendation": True}
    if request is not None:
        entities.update({"date": request.date, "start": request.start, "end": request.end})
    task = PMTaskExtraction(
        task_id="task-1",
        intent=PMIntent.CREATE_SCHEDULE_EVENT,
        entities=entities,
        confidence=0.85,
        missing_fields=["date", "start"],
        source="time_slot_recommendation",
    )
    return PMPlanExtraction(tasks=[task], confidence=0.85, source="time_slot_recommendation")


def _format_open_slot_reply(
    request: _SlotRequest,
    session_id: str,
    data_dir: str,
) -> tuple[str, list[dict[str, Any]]]:
    suggestions = _rank_suggestions(request, session_id, data_dir)
    top = suggestions[:3]
    _record_shown_suggestions(request, session_id, data_dir, top)
    choices = _suggestions_to_choices(request, top)
    lines = [
        f"At {_format_time_natural(request.start)} {_format_date_natural(request.date)}, I suggest one of these. My recommendation is 1.",
        "",
    ]
    for idx, suggestion in enumerate(top, start=1):
        lines.append(f"{idx}. {suggestion.label} - {suggestion.reason}")
    lines.append("")
    lines.append("Reply 1, 2, or 3 to put one on your calendar, or tell me another plan.")
    return "\n".join(lines), choices


def _format_busy_reply(
    request: _SlotRequest,
    event: dict[str, Any],
    session_id: str,
    data_dir: str,
) -> tuple[str, list[dict[str, Any]]]:
    title = str(event.get("title") or "something")
    start = str(event.get("start") or request.start)
    end = str(event.get("end") or "")
    time_range = _format_time_natural(start)
    if end:
        time_range = f"{time_range}-{_format_time_natural(end)}"
    suggestions = _rank_suggestions(
        _SlotRequest(date=request.date, start=end or request.end, end=_default_end_time(end or request.end)),
        session_id,
        data_dir,
    )
    suggestion_request = _SlotRequest(date=request.date, start=end or request.end, end=_default_end_time(end or request.end))
    top = suggestions[:3]
    _record_shown_suggestions(suggestion_request, session_id, data_dir, top)
    choices = _suggestions_to_choices(suggestion_request, top)
    lines = [
        f"At {_format_time_natural(request.start)} {_format_date_natural(request.date)}, you already have {title} ({time_range}). My recommendation is to keep that.",
        "",
        "After that, consider:",
        "",
    ]
    for idx, suggestion in enumerate(top, start=1):
        lines.append(f"{idx}. {suggestion.label} - {suggestion.reason}")
    lines.append("")
    lines.append("Reply 1, 2, or 3 to put one on your calendar after that, or tell me another plan.")
    return "\n".join(lines), choices


def _rank_suggestions(request: _SlotRequest, session_id: str, data_dir: str) -> list[_Suggestion]:
    recent = list_field_choices(
        session_id,
        data_dir,
        intent=_RECOMMENDATION_INTENT,
        field_name=_RECOMMENDATION_FIELD,
    )
    suggestions = [
        *_todo_suggestions(request, session_id, data_dir),
        *_habit_suggestions(session_id, data_dir),
        *_preferred_activity_suggestions(request, session_id, data_dir, recent),
        *_time_band_suggestions(request),
    ]
    scored = [
        (
            suggestion.score
            + _rotation_bonus(request, suggestion)
            + _recent_suggestion_penalty(request, suggestion, recent),
            suggestion,
        )
        for suggestion in suggestions
    ]
    selected: list[_Suggestion] = []
    seen_kinds: set[str] = set()
    for _, suggestion in sorted(scored, key=lambda item: item[0], reverse=True):
        if suggestion.kind in seen_kinds:
            continue
        selected.append(suggestion)
        seen_kinds.add(suggestion.kind)
        if len(selected) == 3:
            return selected
    return selected


def _preferred_activity_suggestions(
    request: _SlotRequest,
    session_id: str,
    data_dir: str,
    recent: list[FieldChoiceRecord],
) -> list[_Suggestion]:
    minutes = _time_to_minutes(request.start)
    if minutes < 7 * 60 or minutes > 21 * 60:
        return []
    suggestions: list[_Suggestion] = []
    for pref in list_user_preferences(session_id, data_dir):
        if pref.rule_type != "preferred_activity" or pref.confidence < 0.60:
            continue
        activity = str(pref.value.get("activity") or pref.scope_key or "").strip().lower()
        label = str(pref.value.get("label") or activity.title()).strip()
        if not activity or _activity_recently_overused(activity, label, recent):
            continue
        suggestions.append(
            _Suggestion(
                label=f"Play {label.lower()}",
                reason=f"matches your {label.lower()} preference and the slot is open.",
                score=37.0 + min(pref.confidence, 0.95),
                kind=f"activity:{activity}",
                category=str(pref.value.get("category") or "personal"),
            )
        )
    return suggestions[:2]


def _todo_suggestions(request: _SlotRequest, session_id: str, data_dir: str) -> list[_Suggestion]:
    todos = load_todos(session_id, data_dir).items
    active = [item for item in todos if not item.done]
    if not active:
        return []
    due_today_or_overdue = [
        item for item in active
        if item.due and item.due <= request.date
    ]
    if due_today_or_overdue:
        item = sorted(due_today_or_overdue, key=lambda todo: (todo.due or "", todo.title.lower()))[0]
        due_phrase = "overdue" if item.due and item.due < request.date else "due today"
        return [
            _Suggestion(
                label=f"Make progress on '{item.title}'",
                reason=f"it is {due_phrase}; keep the pass small and concrete.",
                score=54.0,
                kind="todo",
            )
        ]
    item = sorted(active, key=lambda todo: ((todo.due or "9999-99-99"), todo.title.lower()))[0]
    return [
        _Suggestion(
            label=f"Make progress on '{item.title}'",
            reason="it is already on your list; a 20-minute pass is enough.",
            score=28.0,
            kind="todo",
        )
    ]


def _habit_suggestions(session_id: str, data_dir: str) -> list[_Suggestion]:
    habits = _habit_names(session_id, data_dir)
    if not habits:
        return []
    habit = _best_evening_habit(habits)
    return [
        _Suggestion(
            label=f"Check in on '{habit}'",
            reason="it is already one of your habits; keep it lightweight.",
            score=32.0,
            kind="habit",
        )
    ]


def _time_band_suggestions(request: _SlotRequest) -> list[_Suggestion]:
    minutes = _time_to_minutes(request.start)
    if minutes >= 19 * 60:
        return [
            _Suggestion(
                label="Wind down",
                reason="your calendar looks clear, and this is a good evening recovery slot.",
                score=40.0,
                kind="recovery",
            ),
            _Suggestion(
                label="Read for 20 minutes",
                reason="low-friction downtime that still feels intentional.",
                score=34.0,
                kind="reading",
            ),
            _Suggestion(
                label="Do a quick home reset",
                reason="clear one small surface or prep one thing for tomorrow.",
                score=32.0,
                kind="admin",
            ),
            _Suggestion(
                label="Journal a short note",
                reason="capture what mattered today without turning it into a long review.",
                score=31.0,
                kind="journal",
            ),
            _Suggestion(
                label="Review tomorrow",
                reason="choose one priority and close open loops for 10 minutes.",
                score=30.0,
                kind="planning",
            ),
            _Suggestion(
                label="Take a short walk",
                reason="light movement gives you a reset without turning it into a full task.",
                score=26.0,
                kind="movement",
            ),
            _Suggestion(
                label="Prep tomorrow's first step",
                reason="make the next morning easier with one concrete setup action.",
                score=24.0,
                kind="setup",
            ),
        ]
    if minutes < 12 * 60:
        return [
            _Suggestion(
                label="Do one focused task",
                reason="morning energy is usually best spent on a clear first win.",
                score=38.0,
                kind="focus",
            ),
            _Suggestion(
                label="Review today's plan",
                reason="check calendar pressure before the day gets busy.",
                score=34.0,
                kind="planning",
            ),
            _Suggestion(
                label="Take a short walk",
                reason="light movement can make the next work block easier.",
                score=24.0,
                kind="movement",
            ),
        ]
    if minutes < 17 * 60:
        return [
            _Suggestion(
                label="Do one focused task",
                reason="this is still a useful work window if your calendar is clear.",
                score=36.0,
                kind="focus",
            ),
            _Suggestion(
                label="Clear one small admin task",
                reason="a bounded errand or message keeps the slot from sprawling.",
                score=30.0,
                kind="admin",
            ),
            _Suggestion(
                label="Take a short reset",
                reason="a small break can protect the rest of the afternoon.",
                score=22.0,
                kind="recovery",
            ),
        ]
    return [
        _Suggestion(
            label="Wrap up loose ends",
            reason="this is a good transition slot before evening.",
            score=35.0,
            kind="admin",
        ),
        _Suggestion(
            label="Take a short walk",
            reason="light movement helps separate work from the rest of the day.",
            score=30.0,
            kind="movement",
        ),
        _Suggestion(
            label="Review tomorrow",
            reason="pick one next priority before you wind down.",
            score=26.0,
            kind="planning",
        ),
    ]


def _record_shown_suggestions(
    request: _SlotRequest,
    session_id: str,
    data_dir: str,
    suggestions: list[_Suggestion],
) -> None:
    scope_key = _slot_scope_key(request)
    for suggestion in suggestions:
        record_choice_shown(
            session_id,
            data_dir,
            intent=_RECOMMENDATION_INTENT,
            field_name=_RECOMMENDATION_FIELD,
            value=_suggestion_value(request, suggestion),
            label=suggestion.label,
            scope_type="category",
            scope_key=scope_key,
            source="time_slot_recommendation",
        )


def _suggestions_to_choices(request: _SlotRequest, suggestions: list[_Suggestion]) -> list[dict[str, Any]]:
    choices: list[dict[str, Any]] = []
    for idx, suggestion in enumerate(suggestions, start=1):
        values = {
            "title": suggestion.label,
            "date": request.date,
            "start": request.start,
            "end": _suggestion_end_time(request.start, suggestion),
            "notes": f"Chosen from a {request.start} recommendation.",
        }
        choices.append(
            {
                "id": str(idx),
                "label": suggestion.label,
                "values": values,
                "score": round(suggestion.score, 2),
                "candidate_confidence": 0.7,
                "confidence_components": {
                    "source_confidence": 0.7,
                    "pattern_confidence": 0.0,
                    "semantic_fit_confidence": 0.5,
                    "calendar_certainty": 0.7,
                },
                "reason": suggestion.reason,
                "scope": {
                    "scope_type": "category",
                    "scope_key": _slot_scope_key(request),
                },
                "source": "time_slot_recommendation",
                "signals": {"recommendation_score": round(suggestion.score, 2)},
            }
        )
    return choices


def _suggestion_end_time(start: str, suggestion: _Suggestion) -> str:
    duration = 20
    if suggestion.kind.startswith("activity:"):
        activity = suggestion.kind.split(":", 1)[1]
        duration = 90 if activity in {"basketball", "tennis"} else 60
    elif suggestion.kind == "recovery":
        duration = 30
    elif suggestion.kind in {"movement", "journal", "planning", "setup", "admin"}:
        duration = 20
    elif suggestion.kind == "todo":
        duration = 30
    return _minutes_to_time(_time_to_minutes(start) + duration)


def _activity_recently_overused(
    activity: str,
    label: str,
    recent: list[FieldChoiceRecord],
) -> bool:
    shown_cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    selected_cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    expected_kind = f"activity:{activity}"
    expected_label = f"play {label.lower()}"
    for record in recent:
        old_kind = str(record.value.get("kind") or "")
        old_label = str(record.value.get("label") or record.label or "").lower()
        if old_kind != expected_kind and old_label != expected_label:
            continue
        selected_at = _parse_iso(record.last_selected_at)
        if selected_at is not None and selected_at >= selected_cutoff:
            return True
        shown_at = _parse_iso(record.last_shown_at)
        if shown_at is not None and shown_at >= shown_cutoff:
            return True
    return False


def _recent_suggestion_penalty(
    request: _SlotRequest,
    suggestion: _Suggestion,
    recent: list[FieldChoiceRecord],
) -> float:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    day_cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    scope_key = _slot_scope_key(request)
    worst = 0.0
    for record in recent:
        if record.scope_key != scope_key:
            continue
        shown_at = _parse_iso(record.last_shown_at)
        if shown_at is None or shown_at < cutoff:
            continue
        old_label = str(record.value.get("label") or "")
        old_kind = str(record.value.get("kind") or "")
        # Repeated shows amplify the penalty so rotation keeps discovering
        # unshown suggestions even past the initial rotation window.
        repeat_factor = max(0, record.shown_count - 1)
        if old_label == suggestion.label:
            base = -32.0 if shown_at >= day_cutoff else -18.0
            worst = min(worst, base - 18.0 * repeat_factor)
        elif old_kind == suggestion.kind:
            base = -10.0 if shown_at >= day_cutoff else -6.0
            worst = min(worst, base - 4.0 * repeat_factor)
    if suggestion.kind == "todo" and suggestion.score >= 50.0:
        return max(worst, -8.0)
    return worst


def _rotation_bonus(request: _SlotRequest, suggestion: _Suggestion) -> float:
    seed = f"{request.date}:{request.start}:{suggestion.kind}:{suggestion.label}"
    return float(sum(ord(char) for char in seed) % 7)


def _suggestion_value(request: _SlotRequest, suggestion: _Suggestion) -> dict[str, Any]:
    return {
        "date": request.date,
        "start": request.start,
        "time_band": _slot_scope_key(request),
        "kind": suggestion.kind,
        "label": suggestion.label,
    }


def _slot_scope_key(request: _SlotRequest) -> str:
    minutes = _time_to_minutes(request.start)
    if minutes < 12 * 60:
        return "morning"
    if minutes < 17 * 60:
        return "afternoon"
    if minutes < 19 * 60:
        return "early_evening"
    return "evening"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _habit_names(session_id: str, data_dir: str) -> list[str]:
    raw = habit_list(pm_db_path(session_id, data_dir))
    names: list[str] = []
    for line in raw.splitlines():
        match = re.match(r"- \[[^\]]+\]\s+(.+?)\s+-\s+streak:", line)
        if match:
            names.append(match.group(1).strip())
    return names


def _best_evening_habit(habits: list[str]) -> str:
    preferred = ("journal", "read", "stretch", "walk", "meditat", "sleep", "yoga")
    for keyword in preferred:
        for habit in habits:
            if keyword in habit.lower():
                return habit
    return habits[0]


def _events_for_date(session_id: str, data_dir: str, target_date: str) -> list[dict[str, Any]]:
    if is_google_calendar_connected(session_id, data_dir):
        try:
            return [
                {
                    "date": _split_iso_like(event.start_at)[0],
                    "start": _split_iso_like(event.start_at)[1],
                    "end": _split_iso_like(event.end_at)[1],
                    "title": event.title,
                }
                for event in CalendarService(session_id, data_dir).list_events(limit=1000)
                if _split_iso_like(event.start_at)[0] == target_date
            ]
        except Exception:
            return []

    try:
        entries = load_schedule(session_id, data_dir).entries
    except Exception:
        return []
    try:
        day = date.fromisoformat(target_date)
    except ValueError:
        return []

    events: list[dict[str, Any]] = []
    for entry in entries:
        rule = _entry_recurrence(entry)
        if rule:
            for occ_date, occ in expand_series(entry, day, day):
                events.append(
                    {
                        "date": occ_date.isoformat(),
                        "start": occ.start,
                        "end": occ.end,
                        "title": occ.title,
                    }
                )
            continue
        if entry.date == target_date:
            events.append(
                {
                    "date": entry.date,
                    "start": entry.start,
                    "end": entry.end,
                    "title": entry.title,
                }
            )
    return events


def _first_overlap(request: _SlotRequest, events: list[dict[str, Any]]) -> dict[str, Any] | None:
    start = _time_to_minutes(request.start)
    end = _time_to_minutes(request.end)
    for event in sorted(events, key=lambda item: str(item.get("start") or "")):
        ev_start = str(event.get("start") or "")
        ev_end = str(event.get("end") or ev_start)
        if not (_valid_time(ev_start) and _valid_time(ev_end)):
            continue
        if start < _time_to_minutes(ev_end) and _time_to_minutes(ev_start) < end:
            return event
    return None


def _valid_time(value: str) -> bool:
    return bool(re.match(r"^\d{2}:\d{2}$", value))


def _time_to_minutes(value: str) -> int:
    if not _valid_time(value):
        return 0
    hour, minute = [int(part) for part in value.split(":", 1)]
    return hour * 60 + minute


def _minutes_to_time(value: int) -> str:
    value = max(0, min((23 * 60) + 59, value))
    return f"{value // 60:02d}:{value % 60:02d}"


def _split_iso_like(value: str) -> tuple[str, str]:
    text = str(value or "")
    if "T" in text:
        date_part, time_part = text.split("T", 1)
        return date_part[:10], time_part[:5]
    return text[:10], ""
