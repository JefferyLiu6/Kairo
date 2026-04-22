"""Preference-scored missing-field completion for PM plans."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

from ..calendar.service import CalendarService, is_google_calendar_connected
from ..domain.types import PMIntent, PMPlanExtraction, PMTaskExtraction
from ..parsing.datetime import _smart_end_time
from ..presentation.formatters import _format_date_natural, _format_time_natural
from ..persistence.personalization import (
    BehavioralPatternRecord,
    FieldChoiceRecord,
    PreferenceRecord,
    decay_behavioral_patterns,
    list_behavioral_patterns,
    list_field_choices,
    list_user_preferences,
    recent_choice_penalty,
    record_choice_shown,
)
from ..persistence.store import _entry_recurrence, expand_series, load_schedule


_KNOWN_CATEGORIES = {
    "meeting",
    "breakfast",
    "lunch",
    "dinner",
    "workout",
    "errands",
    "deep_work",
    "personal_appt",
}

_SEMANTIC_WINDOWS: dict[str, list[tuple[str, str]]] = {
    "meeting": [("10:00", "16:00")],
    "breakfast": [("07:00", "10:00")],
    "lunch": [("11:30", "13:30")],
    "dinner": [("17:30", "20:00")],
    "workout": [("06:00", "09:00"), ("17:00", "20:00")],
    "errands": [("10:00", "17:00")],
    "deep_work": [("09:00", "12:00"), ("13:00", "16:00")],
    "personal_appt": [("10:00", "16:00")],
}

_SEMANTIC_DEFAULT_TIMES: dict[str, list[str]] = {
    "meeting": ["10:00", "11:00", "14:00", "15:00"],
    "breakfast": ["09:00", "08:30", "08:00", "09:30"],
    "lunch": ["12:00", "12:30", "13:00"],
    "dinner": ["18:00", "19:00", "19:30"],
    "workout": ["07:00", "18:00", "19:00"],
    "errands": ["10:00", "14:00", "16:00"],
    "deep_work": ["09:00", "10:00", "13:00"],
    "personal_appt": ["10:00", "14:00", "15:00"],
}

_NEUTRAL_TIMES = ["09:00", "10:00", "14:00", "16:00"]


@dataclass
class _Scope:
    scope_type: str
    scope_key: str
    category: str
    event_type: str


@dataclass
class _Candidate:
    date: str
    start: str
    end: str
    source: str
    source_confidence: float
    scope: _Scope
    pattern_confidence: float = 0.0
    semantic_fit_confidence: float = 0.0
    calendar_certainty: float = 0.70
    score: float = 0.0
    reason: str = ""
    signals: dict[str, float] = field(default_factory=dict)

    @property
    def values(self) -> dict[str, Any]:
        return {"date": self.date, "start": self.start, "end": self.end}

    @property
    def candidate_confidence(self) -> float:
        return round(
            (0.35 * self.source_confidence)
            + (0.30 * self.pattern_confidence)
            + (0.20 * self.semantic_fit_confidence)
            + (0.15 * self.calendar_certainty),
            4,
        )


@dataclass(frozen=True)
class FieldCompletionProposal:
    prompt: str
    choices: list[dict[str, Any]]


def build_field_completion_proposal(
    plan: PMPlanExtraction,
    task: PMTaskExtraction,
    missing: list[str],
    config: Any,
) -> Optional[FieldCompletionProposal]:
    """Build ranked choices for a missing-field blocker, or return None."""
    sid = str(config.session_id)
    data_dir = str(config.data_dir)
    if not _is_fillable(task, missing):
        return None

    decay_behavioral_patterns(sid, data_dir)
    scope = _classify_scope(task)
    preferences = list_user_preferences(sid, data_dir)
    patterns = list_behavioral_patterns(sid, data_dir)
    choices = list_field_choices(
        sid,
        data_dir,
        intent=task.intent.value,
        field_name=_field_name(missing),
    )
    candidates = _generate_candidates(task, missing, scope, preferences, patterns, choices)
    if not candidates:
        return None

    events = _load_calendar_events(sid, data_dir)
    ranked = _score_and_rank_candidates(
        candidates,
        task,
        missing,
        sid,
        data_dir,
        preferences,
        patterns,
        events,
    )
    if not ranked:
        return None

    top = ranked[:3]
    if top[0].candidate_confidence < 0.20:
        return None

    choice_payloads = [_candidate_to_choice(idx, item) for idx, item in enumerate(top, start=1)]
    for item in choice_payloads:
        record_choice_shown(
            sid,
            data_dir,
            intent=task.intent.value,
            field_name=_field_name(missing),
            value=item["values"],
            label=item["label"],
            scope_type=item["scope"]["scope_type"],
            scope_key=item["scope"]["scope_key"],
            source=str(item.get("source") or "scored_candidate"),
        )
    return FieldCompletionProposal(
        prompt=_format_choice_prompt(
            choice_payloads,
            personalized=top[0].candidate_confidence >= 0.55,
            task=task,
        ),
        choices=choice_payloads,
    )


def _is_fillable(task: PMTaskExtraction, missing: list[str]) -> bool:
    missing_set = set(missing)
    if task.intent == PMIntent.CREATE_SCHEDULE_EVENT:
        return bool(missing_set & {"date", "start"})
    if task.intent == PMIntent.UPDATE_SCHEDULE_EVENT:
        return "new date or time" in missing_set
    return False


def _field_name(missing: list[str]) -> str:
    normalized = [str(item).replace(" ", "_") for item in missing]
    if {"date", "start"}.issubset(set(missing)):
        return "date_start"
    if "new date or time" in missing:
        return "date_start"
    return "+".join(sorted(normalized)) or "field"


def _classify_scope(task: PMTaskExtraction) -> _Scope:
    title = _task_title(task)
    lower = _norm(title)
    category = "unknown"
    if re.search(r"\b(standup|stand-up|meeting|sync|call|1:1|one-on-one|interview)\b", lower):
        category = "meeting"
    elif "breakfast" in lower:
        category = "breakfast"
    elif "lunch" in lower:
        category = "lunch"
    elif "dinner" in lower or "supper" in lower:
        category = "dinner"
    elif re.search(r"\b(workout|gym|run|jog|yoga|training)\b", lower):
        category = "workout"
    elif re.search(r"\b(errand|grocery|shopping|pickup|pick up|drop off)\b", lower):
        category = "errands"
    elif re.search(r"\b(deep work|focus|focus block|write|coding block)\b", lower):
        category = "deep_work"
    elif re.search(r"\b(dentist|doctor|appointment|appt|checkup|therapy|haircut)\b", lower):
        category = "personal_appt"

    event_type = _event_type_key(title)
    scope_type = "event_type" if event_type and category != "unknown" else "category"
    scope_key = event_type if scope_type == "event_type" else (category if category != "unknown" else "*")
    return _Scope(scope_type=scope_type, scope_key=scope_key, category=category, event_type=event_type)


def _task_title(task: PMTaskExtraction) -> str:
    entities = task.entities
    entries = entities.get("entries")
    if isinstance(entries, list) and entries and isinstance(entries[0], dict):
        return str(entries[0].get("title") or entities.get("title") or "")
    return str(entities.get("title") or entities.get("query") or entities.get("category") or "")


def _event_type_key(title: str) -> str:
    lower = _norm(title)
    lower = re.sub(r"\b(my|the|a|an|with|appointment|appt|meeting|event)\b", " ", lower)
    lower = re.sub(r"[^a-z0-9]+", " ", lower)
    return " ".join(lower.split())[:80] or "unknown"


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().strip().split())


def _generate_candidates(
    task: PMTaskExtraction,
    missing: list[str],
    scope: _Scope,
    preferences: list[PreferenceRecord],
    patterns: list[BehavioralPatternRecord],
    choices: list[FieldChoiceRecord],
) -> list[_Candidate]:
    dates = _candidate_dates(task, missing)
    times = _candidate_times(scope, preferences, patterns, choices)
    existing_start = str(task.entities.get("start") or "")
    if existing_start and "start" not in set(missing) and "new date or time" not in set(missing):
        times = [("current_user_instruction", 1.00, existing_start), *times]
    title = _task_title(task)
    candidates: list[_Candidate] = []
    seen: set[tuple[str, str]] = set()
    for source, source_conf, time_value in times:
        for date_value in dates:
            key = (date_value, time_value)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                _Candidate(
                    date=date_value,
                    start=time_value,
                    end=_smart_end_time(time_value, title),
                    source=source,
                    source_confidence=source_conf,
                    scope=scope,
                )
            )
    return candidates


def _candidate_dates(task: PMTaskExtraction, missing: list[str]) -> list[str]:
    entities = task.entities
    existing = entities.get("date") or entities.get("reference_date")
    if existing and "date" not in missing and "new date or time" not in missing:
        return [str(existing)]
    if task.intent == PMIntent.UPDATE_SCHEDULE_EVENT and existing:
        return [str(existing)]
    today = date.today()
    if task.entities.get("date_bias") == "today":
        return [(today + timedelta(days=offset)).isoformat() for offset in range(0, 7)]
    return [(today + timedelta(days=offset)).isoformat() for offset in range(1, 8)]


def _candidate_times(
    scope: _Scope,
    preferences: list[PreferenceRecord],
    patterns: list[BehavioralPatternRecord],
    choices: list[FieldChoiceRecord],
) -> list[tuple[str, float, str]]:
    values: list[tuple[str, float, str]] = []
    for pref in preferences:
        if not _scope_applies(pref.scope_type, pref.scope_key, scope):
            continue
        extracted = _times_from_rule(pref.rule_type, pref.value)
        for time_value in extracted:
            values.append(("explicit_preference", 0.90, time_value))

    for pattern in patterns:
        if not _scope_applies(pattern.scope_type, pattern.scope_key, scope):
            continue
        extracted = _times_from_rule(pattern.rule_type, pattern.value)
        for time_value in extracted:
            values.append(("learned_pattern", max(0.0, min(pattern.confidence, 0.95)), time_value))

    for choice in choices:
        if not _scope_applies(choice.scope_type, choice.scope_key, scope):
            continue
        start = str(choice.value.get("start") or "")
        if start:
            values.append(("field_choice_evidence", choice.confidence, start))

    for time_value in _SEMANTIC_DEFAULT_TIMES.get(scope.category, []):
        values.append(("semantic_default", 0.50, time_value))
    for time_value in _NEUTRAL_TIMES:
        values.append(("neutral_default", 0.40, time_value))

    best_by_time: dict[str, tuple[str, float, str]] = {}
    for source, confidence, time_value in values:
        if not _valid_time(time_value):
            continue
        current = best_by_time.get(time_value)
        if current is None or confidence > current[1]:
            best_by_time[time_value] = (source, confidence, time_value)
    return list(best_by_time.values())


def _times_from_rule(rule_type: str, value: dict[str, Any]) -> list[str]:
    if rule_type in {"preferred_window", "blocked_window"}:
        start = str(value.get("start") or "")
        end = str(value.get("end") or "")
        center = str(value.get("center") or "")
        if center:
            return [center]
        if _valid_time(start) and _valid_time(end):
            return [_midpoint_time(start, end)]
        if _valid_time(start):
            return [start]
        return []
    if rule_type == "start_after":
        start = str(value.get("time") or value.get("start") or "")
        return [start] if _valid_time(start) else []
    if rule_type == "start_before":
        end = str(value.get("time") or value.get("end") or "")
        return [end] if _valid_time(end) else []
    start = str(value.get("start") or "")
    return [start] if _valid_time(start) else []


def _midpoint_time(start: str, end: str) -> str:
    start_m = _time_to_minutes(start)
    end_m = _time_to_minutes(end)
    if end_m <= start_m:
        return start
    mid = start_m + ((end_m - start_m) // 2)
    return _minutes_to_time(mid)


def _score_and_rank_candidates(
    candidates: list[_Candidate],
    task: PMTaskExtraction,
    missing: list[str],
    session_id: str,
    data_dir: str,
    preferences: list[PreferenceRecord],
    patterns: list[BehavioralPatternRecord],
    events: list[dict[str, Any]],
) -> list[_Candidate]:
    field_name = _field_name(missing)
    for candidate in candidates:
        candidate.signals["explicit_preference"] = _score_preferences(candidate, preferences)
        candidate.signals["learned_pattern"] = _score_patterns(candidate, patterns)
        candidate.signals["semantic_fit"] = _score_semantic(candidate)
        calendar_score, certainty, disruption = _score_calendar(candidate, events)
        candidate.calendar_certainty = certainty
        candidate.signals["calendar_fit"] = calendar_score
        candidate.signals["disruption"] = disruption
        candidate.signals["repetition_penalty"] = recent_choice_penalty(
            session_id,
            data_dir,
            intent=task.intent.value,
            field_name=field_name,
            value=candidate.values,
        )
        candidate.signals["uncertainty_penalty"] = -20.0 if candidate.candidate_confidence < 0.55 else 0.0
        if candidate.source in {"neutral_default", "semantic_default"} and candidate.scope.category == "unknown":
            candidate.signals["broad_space_penalty"] = -25.0
        else:
            candidate.signals["broad_space_penalty"] = 0.0
        candidate.score = sum(candidate.signals.values())
        candidate.reason = _candidate_reason(candidate)

    sorted_candidates = sorted(candidates, key=lambda item: (item.score, item.candidate_confidence), reverse=True)
    return _select_diverse(sorted_candidates)


def _score_preferences(candidate: _Candidate, preferences: list[PreferenceRecord]) -> float:
    score = 0.0
    for pref in preferences:
        if not _scope_applies(pref.scope_type, pref.scope_key, candidate.scope):
            continue
        match = _rule_matches(pref.rule_type, pref.value, candidate.start)
        if pref.rule_type == "blocked_window" and match:
            score -= 100.0
        elif match:
            score += 30.0
        elif pref.rule_type in {"preferred_window", "start_after", "start_before"}:
            score -= 60.0
    return score


def _score_patterns(candidate: _Candidate, patterns: list[BehavioralPatternRecord]) -> float:
    score = 0.0
    best_confidence = 0.0
    for pattern in patterns:
        if not _scope_applies(pattern.scope_type, pattern.scope_key, candidate.scope):
            continue
        if not _rule_matches(pattern.rule_type, pattern.value, candidate.start):
            continue
        adjusted = _adjusted_pattern_confidence(pattern, candidate.scope)
        best_confidence = max(best_confidence, adjusted)
        if pattern.scope_type == "event_type":
            score += 18.0 * adjusted
        elif pattern.scope_type == "category":
            score += 12.0 * adjusted
        else:
            score += 8.0 * adjusted
    candidate.pattern_confidence = best_confidence
    return score


def _adjusted_pattern_confidence(pattern: BehavioralPatternRecord, scope: _Scope) -> float:
    if pattern.scope_type == "event_type" and pattern.scope_key == scope.event_type:
        return pattern.confidence
    if pattern.scope_type == "category" and pattern.scope_key == scope.category:
        return pattern.confidence * 0.85
    if pattern.scope_type == "global":
        return pattern.confidence * 0.70
    return pattern.confidence * 0.50


def _score_semantic(candidate: _Candidate) -> float:
    if candidate.scope.category not in _KNOWN_CATEGORIES:
        candidate.semantic_fit_confidence = 0.0
        return 0.0
    windows = _SEMANTIC_WINDOWS.get(candidate.scope.category, [])
    if any(_time_in_window(candidate.start, start, end) for start, end in windows):
        candidate.semantic_fit_confidence = 0.85
        return 15.0
    candidate.semantic_fit_confidence = 0.50
    return 0.0


def _score_calendar(candidate: _Candidate, events: list[dict[str, Any]]) -> tuple[float, float, float]:
    if not events:
        return (20.0, 0.70, 0.0)
    conflict = False
    back_to_back = False
    start = _time_to_minutes(candidate.start)
    end = _time_to_minutes(candidate.end)
    for event in events:
        if event.get("date") != candidate.date:
            continue
        ev_start = str(event.get("start") or "")
        ev_end = str(event.get("end") or ev_start)
        if not _valid_time(ev_start):
            continue
        ev_start_m = _time_to_minutes(ev_start)
        ev_end_m = _time_to_minutes(ev_end) if _valid_time(ev_end) else ev_start_m
        if start < ev_end_m and ev_start_m < end:
            conflict = True
        if start == ev_end_m or end == ev_start_m:
            back_to_back = True
    if conflict:
        return (-80.0, 0.20, -15.0 if back_to_back else 0.0)
    return (20.0, 1.0, -15.0 if back_to_back else 0.0)


def _select_diverse(candidates: list[_Candidate]) -> list[_Candidate]:
    selected: list[_Candidate] = []
    for candidate in candidates:
        if any(_near_duplicate(candidate, existing) for existing in selected):
            continue
        if len(selected) == 1 and _time_band(candidate.start) == _time_band(selected[0].start):
            alternate = _first_close_different_band(candidates, selected)
            if alternate is not None and alternate is not candidate:
                candidate = alternate
        if selected:
            bonus = 0.0
            first = selected[0]
            if candidate.date != first.date:
                bonus += 4.0
            if _time_band(candidate.start) != _time_band(first.start):
                bonus += 4.0
            candidate.signals["diversity_bonus"] = bonus
            candidate.score += bonus
        selected.append(candidate)
        if len(selected) == 3:
            break
    return selected


def _first_close_different_band(
    candidates: list[_Candidate],
    selected: list[_Candidate],
) -> _Candidate | None:
    first = selected[0]
    floor = first.score - 10.0
    for candidate in candidates:
        if candidate.score < floor:
            continue
        if any(_near_duplicate(candidate, existing) for existing in selected):
            continue
        if _time_band(candidate.start) != _time_band(first.start):
            return candidate
    return None


def _near_duplicate(a: _Candidate, b: _Candidate) -> bool:
    if a.date != b.date:
        return False
    return abs(_time_to_minutes(a.start) - _time_to_minutes(b.start)) <= 30


def _scope_applies(scope_type: str, scope_key: str, target: _Scope) -> bool:
    if scope_type == "global":
        return True
    if scope_type == "category":
        return scope_key == target.category
    if scope_type == "event_type":
        return scope_key == target.event_type
    return False


def _rule_matches(rule_type: str, value: dict[str, Any], start: str) -> bool:
    if not _valid_time(start):
        return False
    if rule_type in {"preferred_window", "blocked_window"}:
        return _time_in_window(start, str(value.get("start") or ""), str(value.get("end") or ""))
    if rule_type == "start_after":
        threshold = str(value.get("time") or value.get("start") or "")
        return _valid_time(threshold) and _time_to_minutes(start) >= _time_to_minutes(threshold)
    if rule_type == "start_before":
        threshold = str(value.get("time") or value.get("end") or "")
        return _valid_time(threshold) and _time_to_minutes(start) <= _time_to_minutes(threshold)
    return False


def _candidate_reason(candidate: _Candidate) -> str:
    conflict_score = candidate.signals.get("calendar_fit", 0.0)
    if conflict_score < 0:
        return "Possible calendar conflict."
    preference = candidate.signals.get("explicit_preference", 0.0)
    pattern = candidate.signals.get("learned_pattern", 0.0)
    semantic = candidate.signals.get("semantic_fit", 0.0)
    if preference > 0:
        return "Matches your saved preference and has no conflict."
    if pattern > 0 and candidate.candidate_confidence >= 0.55:
        return f"Best fit with your usual {candidate.scope.scope_key} window and no conflict."
    if semantic > 0 and candidate.scope.category != "unknown":
        return f"Good fit for {candidate.scope.category.replace('_', ' ')} and no conflict."
    return "Open option with no known conflict."


def _candidate_to_choice(idx: int, candidate: _Candidate) -> dict[str, Any]:
    label = f"{_format_date_natural(candidate.date).capitalize()} at {_format_time_natural(candidate.start)}"
    return {
        "id": str(idx),
        "label": label,
        "values": candidate.values,
        "score": round(candidate.score, 2),
        "candidate_confidence": candidate.candidate_confidence,
        "confidence_components": {
            "source_confidence": round(candidate.source_confidence, 4),
            "pattern_confidence": round(candidate.pattern_confidence, 4),
            "semantic_fit_confidence": round(candidate.semantic_fit_confidence, 4),
            "calendar_certainty": round(candidate.calendar_certainty, 4),
        },
        "reason": candidate.reason,
        "scope": {
            "scope_type": candidate.scope.scope_type,
            "scope_key": candidate.scope.scope_key,
        },
        "source": candidate.source,
        "signals": {key: round(value, 2) for key, value in candidate.signals.items()},
    }


def _format_choice_prompt(
    choices: list[dict[str, Any]],
    *,
    personalized: bool,
    task: PMTaskExtraction,
) -> str:
    routine_prompt = _ambiguous_routine_prompt(task)
    if routine_prompt is not None:
        heading, other_line = routine_prompt
        lines = [heading, ""]
        for choice in choices:
            lines.append(f"{choice['id']}. {choice['label']} — {choice['reason']}")
        lines.append("")
        lines.append(other_line)
        return "\n".join(lines)

    if personalized:
        lines = ["I found a few good options. My recommendation is 1.", ""]
    else:
        lines = ["I found a few workable options. My recommendation is 1.", ""]
    for choice in choices:
        lines.append(f"{choice['id']}. {choice['label']} — {choice['reason']}")
    lines.append("")
    lines.append("Or type another date/time.")
    return "\n".join(lines)


def _ambiguous_routine_prompt(task: PMTaskExtraction) -> tuple[str, str] | None:
    if not task.entities.get("ambiguous_life_event"):
        return None
    title = _task_title(task)
    lower = _norm(title)
    if "breakfast" in lower:
        return ("Do you want me to schedule breakfast?", "Or type what you want to eat instead.")
    if "lunch" in lower:
        return ("Do you want me to schedule lunch?", "Or type what you want to eat instead.")
    if "dinner" in lower or "supper" in lower:
        return ("Do you want me to schedule dinner?", "Or type what you want to eat instead.")
    if re.search(r"\b(workout|gym|exercise|run|jog|walk|yoga)\b", lower):
        return ("Do you want me to schedule it?", "Or type another date/time.")
    return ("Do you want me to schedule it?", "Or type another date/time.")


def _load_calendar_events(session_id: str, data_dir: str) -> list[dict[str, Any]]:
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
            ]
        except Exception:
            return []
    try:
        entries = load_schedule(session_id, data_dir).entries
    except Exception:
        return []
    today = date.today()
    window_end = today + timedelta(days=30)
    events: list[dict[str, Any]] = []
    for entry in entries:
        rule = _entry_recurrence(entry)
        if rule:
            for occ_date, occ in expand_series(entry, today, window_end):
                events.append(
                    {
                        "date": occ_date.isoformat(),
                        "start": occ.start,
                        "end": occ.end,
                        "title": occ.title,
                    }
                )
        elif entry.date:
            events.append(
                {
                    "date": entry.date,
                    "start": entry.start,
                    "end": entry.end,
                    "title": entry.title,
                }
            )
    return events


def _split_iso_like(value: str) -> tuple[str, str]:
    if not value:
        return "", ""
    if "T" not in value:
        return value[:10], ""
    date_part, time_part = value.split("T", 1)
    time_part = time_part.replace("Z", "")
    for sep in ("+", "-"):
        if sep in time_part[1:]:
            time_part = time_part.split(sep, 1)[0]
            break
    return date_part[:10], time_part[:5]


def _valid_time(value: str) -> bool:
    try:
        hour, minute = value.split(":", 1)
        return 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59
    except (ValueError, AttributeError):
        return False


def _time_to_minutes(value: str) -> int:
    hour, minute = [int(part) for part in value.split(":", 1)]
    return hour * 60 + minute


def _minutes_to_time(value: int) -> str:
    value = max(0, min(23 * 60 + 59, value))
    return f"{value // 60:02d}:{value % 60:02d}"


def _time_in_window(value: str, start: str, end: str) -> bool:
    if not (_valid_time(value) and _valid_time(start) and _valid_time(end)):
        return False
    minutes = _time_to_minutes(value)
    return _time_to_minutes(start) <= minutes <= _time_to_minutes(end)


def _time_band(value: str) -> str:
    minutes = _time_to_minutes(value)
    if minutes < 12 * 60:
        return "morning"
    if minutes < 17 * 60:
        return "afternoon"
    return "evening"
