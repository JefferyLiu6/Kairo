"""Schedule and calendar action executor."""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Optional

from ..calendar.service import CalendarService, CalendarWriteUnavailableError, is_google_calendar_connected
from ..domain.commands import (
    ScheduleAddCommand,
    ScheduleCancelSeriesFromCommand,
    ScheduleModifyOccurrenceCommand,
    ScheduleRemoveCommand,
    ScheduleSkipOccurrenceCommand,
    ScheduleUpdateCommand,
)
from ..parsing.datetime import _default_end_time
from ..persistence.store import _entry_recurrence, expand_series, load_schedule, schedule_add, schedule_add_exception, schedule_add_override, schedule_cancel_series_from, schedule_remove, schedule_update
from ..presentation.formatters import (
    _format_date_natural,
    _format_event_duration,
    _format_schedule_add_reply,
    _format_time_natural,
)
from ..resolvers.schedule import (
    _google_delete_targets_for_events,
    _google_event_ref,
    _hide_google_mirror_refs,
)
from ..domain.types import PMAction
from .common import _result


# ── Conflict detection ────────────────────────────────────────────────────────

def _find_schedule_conflicts(new_entries: list[dict], session_id: str, data_dir: str) -> list[str]:
    """Return human-readable descriptions of time overlaps between new entries and existing events."""
    if is_google_calendar_connected(session_id, data_dir):
        return _find_google_schedule_conflicts(new_entries, session_id, data_dir)
    try:
        existing = load_schedule(session_id, data_dir).entries
    except Exception:
        return []
    conflicts: list[str] = []
    for new in new_entries:
        n_date = new.get("date") or ""
        n_weekday = new.get("weekday")
        n_start = new.get("start") or ""
        n_end = new.get("end") or ""
        n_title = new.get("title") or "New event"
        if not n_start or not n_end:
            continue
        for ex in existing:
            same_when = (n_date and ex.date == n_date) or (
                n_weekday is not None and ex.weekday == n_weekday
            )
            if not same_when or not ex.start or not ex.end:
                continue
            if n_start < ex.end and ex.start < n_end:
                conflicts.append(
                    f"'{n_title}' ({n_start}–{n_end}) overlaps with '{ex.title}' ({ex.start}–{ex.end})"
                )
    return conflicts


def _find_google_schedule_conflicts(new_entries: list[dict], session_id: str, data_dir: str) -> list[str]:
    try:
        existing = [_google_event_ref(event) for event in CalendarService(session_id, data_dir).list_events(limit=1000)]
    except Exception:
        return []
    conflicts: list[str] = []
    for new in new_entries:
        n_date = new.get("date") or ""
        n_start = new.get("start") or ""
        n_end = new.get("end") or ""
        n_title = new.get("title") or "New event"
        if not n_date or not n_start or not n_end:
            continue
        for ex in existing:
            if ex["date"] != n_date or not ex["start"]:
                continue
            ex_end = ex.get("end") or ex["start"]
            if n_start < ex_end and ex["start"] < n_end:
                conflicts.append(
                    f"'{n_title}' ({n_start}–{n_end}) overlaps with '{ex['title']}' ({ex['start']}–{ex_end})"
                )
    return conflicts


# ── Schedule list formatting ──────────────────────────────────────────────────

def _format_schedule_list(session_id: str, data_dir: str) -> str:
    """Return a human-friendly agenda view of the schedule."""
    if is_google_calendar_connected(session_id, data_dir):
        return _format_google_schedule_list(session_id, data_dir)
    try:
        all_entries = load_schedule(session_id, data_dir).entries
    except Exception:
        return "No schedule data available."
    if not all_entries:
        return "Nothing on the schedule."

    today = date.today()
    window_end = today + timedelta(days=13)  # show 2 weeks ahead

    # Flatten: one-off events stay as-is; series get expanded over the window
    flat: list[tuple[str, str, str]] = []  # (date_str, start, title_line)
    series_seen: set[str] = set()

    for e in all_entries:
        rule = _entry_recurrence(e)
        if rule is not None:
            series_key = e.series_id or e.id
            if series_key not in series_seen:
                series_seen.add(series_key)
                for occ_date, occ in expand_series(e, today, window_end):
                    time_part = _format_time_natural(occ.start) if occ.start else ""
                    dur = _format_event_duration(occ.start, occ.end) if occ.start and occ.end else ""
                    flat.append((occ_date.isoformat(), occ.start or "", f"  {time_part} {occ.title}{dur}".strip()))
        elif e.date:
            time_part = _format_time_natural(e.start) if e.start else ""
            dur = _format_event_duration(e.start, e.end) if e.start and e.end else ""
            flat.append((e.date, e.start or "", f"  {time_part} {e.title}{dur}".strip()))

    flat.sort(key=lambda t: (t[0], t[1]))

    # Also collect series summaries for the footer
    series_lines: list[str] = []
    for e in all_entries:
        rule = _entry_recurrence(e)
        if rule is None:
            continue
        by_day_names = {"MO": "Mon", "TU": "Tue", "WE": "Wed", "TH": "Thu", "FR": "Fri", "SA": "Sat", "SU": "Sun"}
        day_str = ", ".join(by_day_names.get(c, c) for c in rule.by_day) if rule.by_day else rule.freq
        time_part = _format_time_natural(e.start) if e.start else ""
        until_str = f" until {_format_date_natural(rule.until) or rule.until}" if rule.until else ""
        series_lines.append(f"  [{e.id}] {e.title} — every {day_str}{until_str} {time_part}".strip())

    lines: list[str] = []
    current_day: Optional[str] = None
    for date_str, _start, event_line in flat:
        if date_str != current_day:
            current_day = date_str
            try:
                d = date.fromisoformat(date_str)
                if d == today:
                    label = "Today"
                elif d == today + timedelta(days=1):
                    label = "Tomorrow"
                elif 0 < (d - today).days <= 6:
                    label = d.strftime("%A")
                else:
                    label = d.strftime("%A, %b %d")
            except ValueError:
                label = date_str
            lines.append(f"**{label}**")
        lines.append(event_line)

    if series_lines:
        if lines:
            lines.append("")
        lines.append("**Recurring series**")
        lines.extend(series_lines)

    return "\n".join(lines) if lines else "Your schedule is clear."


def _format_google_schedule_list(session_id: str, data_dir: str) -> str:
    try:
        events = CalendarService(session_id, data_dir).list_events(limit=200)
    except Exception:
        return "No Google Calendar data available."
    if not events:
        return "Your Google Calendar is clear in the synced range."
    refs = sorted(
        [_google_event_ref(event) for event in events],
        key=lambda ref: (ref["date"], ref["start"], ref["title"].lower()),
    )
    today = date.today()
    lines: list[str] = []
    current_day = ""
    for ref in refs:
        if ref["date"] != current_day:
            current_day = ref["date"]
            try:
                d = date.fromisoformat(current_day)
                if d == today:
                    label = "Today"
                elif d == today + timedelta(days=1):
                    label = "Tomorrow"
                elif 0 < (d - today).days <= 6:
                    label = d.strftime("%A")
                else:
                    label = d.strftime("%A, %b %d")
            except ValueError:
                label = current_day or "Unscheduled"
            lines.append(f"**{label}**")
        time_part = _format_time_natural(ref["start"]) if ref["start"] else ""
        lines.append(f"  {time_part} {ref['title']}".strip())
    return "\n".join(lines)


# ── Google write helpers ──────────────────────────────────────────────────────

def _schedule_entries_can_write_google(entries: list[dict], session_id: str, data_dir: str) -> bool:
    if not entries:
        return False
    try:
        if not CalendarService(session_id, data_dir).has_google_write_account():
            return False
    except Exception:
        return False
    for entry in entries:
        has_recurrence = entry.get("recurrence") is not None
        if entry.get("weekday") is not None and not has_recurrence:
            return False
        if not entry.get("start") or not entry.get("end"):
            return False
        if not has_recurrence and not entry.get("date"):
            return False
    return True


def _write_schedule_entries_to_google(entries: list[dict], session_id: str, data_dir: str) -> list[Any]:
    service = CalendarService(session_id, data_dir)
    return [service.create_google_event_from_entry(entry) for entry in entries]


def _format_google_schedule_add_reply(entries: list[dict]) -> str:
    base = _format_schedule_add_reply(entries)
    if len(entries) == 1 and base.endswith("."):
        return base[:-1] + " to Google Calendar."
    return base + "\nSaved to Google Calendar."


def _google_patch_from_update(patch: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in patch.items() if k != "id" and v is not None}
    if ("start" in out or "end" in out) and "date" not in out:
        out["date"] = target.get("date") or ""
    if "start" in out and "end" not in out:
        out["end"] = _default_end_time(out["start"])
    return out


# ── Action executor ───────────────────────────────────────────────────────────

def execute_schedule_action(action: PMAction, session_id: str, data_dir: str) -> dict[str, Any] | None:
    if action.action_type == "schedule_add":
        command = ScheduleAddCommand.model_validate(action.payload)
        entries = command.legacy_entries()
        conflicts = _find_schedule_conflicts(entries, session_id, data_dir)
        if _schedule_entries_can_write_google(entries, session_id, data_dir):
            try:
                created = _write_schedule_entries_to_google(entries, session_id, data_dir)
            except CalendarWriteUnavailableError:
                created = []
            except Exception as exc:
                return _result(f"I couldn't update Google Calendar: {exc}")
            if created:
                msg = _format_google_schedule_add_reply(entries)
                if conflicts:
                    msg += "\nNote: " + "; ".join(conflicts) + "."
                return _result(msg)
        store_msg = schedule_add(json.dumps(entries), session_id, data_dir)
        if not store_msg.startswith("Error"):
            msg = _format_schedule_add_reply(entries)
            if conflicts:
                msg += "\nNote: " + "; ".join(conflicts) + "."
            return _result(msg)
        return _result(store_msg)
    if action.action_type == "schedule_update":
        command = ScheduleUpdateCommand.model_validate(action.payload)
        google_events = command.legacy_google_events()
        if google_events:
            updates = command.legacy_updates()
            try:
                for target, patch in zip(google_events, updates):
                    provider_event_id = target.get("providerEventId")
                    if not provider_event_id:
                        continue
                    CalendarService(session_id, data_dir).update_google_event(
                        provider_event_id,
                        _google_patch_from_update(patch, target),
                    )
            except CalendarWriteUnavailableError as exc:
                return _result(str(exc))
            except Exception as exc:
                return _result(f"I couldn't update Google Calendar: {exc}")
            return _result("Done! Updated it in Google Calendar.")
        raw = schedule_update(json.dumps(command.legacy_updates()), session_id, data_dir)
        if raw.startswith("Error"):
            return _result(raw)
        updates = command.legacy_updates()
        if updates:
            patch = updates[0]
            parts: list[str] = []
            if patch.get("start"):
                parts.append(f"to {_format_time_natural(patch['start'])}")
            if patch.get("date"):
                parts.append(f"on {_format_date_natural(patch['date']) or patch['date']}")
            if parts:
                return _result(f"Done! Moved it {' '.join(parts)}.")
        return _result(f"Updated {len(updates)} event(s).")
    if action.action_type == "schedule_remove":
        command = ScheduleRemoveCommand.model_validate(action.payload)
        ids = command.ids
        google_events = command.legacy_google_events()
        if google_events:
            try:
                service = CalendarService(session_id, data_dir)
                for provider_event_id in _google_delete_targets_for_events(google_events):
                    service.delete_google_event(provider_event_id)
                _hide_google_mirror_refs(session_id, data_dir, google_events)
            except CalendarWriteUnavailableError as exc:
                return _result(str(exc))
            except Exception as exc:
                return _result(f"I couldn't update Google Calendar: {exc}")
            if len(google_events) == 1:
                return _result(f"Removed '{google_events[0].get('title')}' from Google Calendar.")
            return _result(f"Removed {len(google_events)} events from Google Calendar.")
        titles: list[str] = []
        try:
            sched = load_schedule(session_id, data_dir)
            titles = [e.title for e in sched.entries if e.id in ids]
        except Exception:
            pass
        raw = schedule_remove(json.dumps(ids), session_id, data_dir)
        if raw.startswith("Error"):
            return _result(raw)
        if titles:
            if len(titles) == 1:
                return _result(f"Removed '{titles[0]}' from your calendar.")
            return _result(f"Removed {len(titles)} events from your calendar.")
        return _result("Removed.")
    if action.action_type == "schedule_add_exception":
        command = ScheduleSkipOccurrenceCommand.model_validate(action.payload)
        raw = schedule_add_exception(
            command.series_id,
            command.skip_date,
            session_id,
            data_dir,
        )
        if raw.startswith("Error"):
            return _result(raw)
        skip_when = _format_date_natural(command.skip_date) or command.skip_date
        return _result(f"Skipping {skip_when}, keeping the rest of the series.")
    if action.action_type == "schedule_add_override":
        command = ScheduleModifyOccurrenceCommand.model_validate(action.payload)
        override = command.legacy_override()
        raw = schedule_add_override(
            command.series_id,
            override,
            session_id,
            data_dir,
        )
        if raw.startswith("Error"):
            return _result(raw)
        parts: list[str] = []
        if override.get("start"):
            parts.append(f"to {_format_time_natural(override['start'])}")
        if override.get("original_date"):
            parts.append(f"on {_format_date_natural(override['original_date']) or override['original_date']}")
        return _result(f"Moved just that one {' '.join(parts)}." if parts else "Updated that occurrence.")
    if action.action_type == "schedule_cancel_series_from":
        command = ScheduleCancelSeriesFromCommand.model_validate(action.payload)
        raw = schedule_cancel_series_from(
            command.series_id,
            command.from_date,
            session_id,
            data_dir,
        )
        if raw.startswith("Error"):
            return _result(raw)
        from_when = _format_date_natural(command.from_date) or command.from_date
        return _result(f"Cancelled all future occurrences from {from_when}.")
    if action.action_type == "list_state" and action.payload.get("target", "todos") == "schedule":
        return _result(_format_schedule_list(session_id, data_dir))
    return None
