"""
Kairo storage — port of schedule.ts + private-store.ts

Schedule and private metadata are stored per-session under:
  data/personal-manager/<base64url-session-id>/schedule.json
  data/personal-manager/<base64url-session-id>/private.json
  data/personal-manager/<base64url-session-id>/todos.json
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import date, timedelta
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Schedule ───────────────────────────────────────────────────────────────────

# RFC 5545 day codes → Python date.weekday() (0=Mon … 6=Sun)
_BY_DAY_TO_PYTHON_WD: dict[str, int] = {
    "MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6,
}
_PYTHON_WD_TO_BY_DAY: dict[int, str] = {v: k for k, v in _BY_DAY_TO_PYTHON_WD.items()}
# Legacy internal encoding: 0=Sun … 6=Sat
_OLD_WD_TO_BY_DAY: dict[int, str] = {
    0: "SU", 1: "MO", 2: "TU", 3: "WE", 4: "TH", 5: "FR", 6: "SA",
}


class RecurrenceRule(BaseModel):
    freq: str = "weekly"                              # "daily" | "weekly" | "monthly"
    interval: int = 1
    by_day: list[str] = Field(default_factory=list)  # RFC 5545: "MO","TU","WE","TH","FR","SA","SU"
    until: Optional[str] = None                      # YYYY-MM-DD inclusive


class OverrideEntry(BaseModel):
    original_date: str           # YYYY-MM-DD of the occurrence being overridden
    cancelled: bool = False
    title: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    notes: Optional[str] = None


class ScheduleEntry(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str
    date: Optional[str] = None      # YYYY-MM-DD for one-off events or series anchor
    weekday: Optional[int] = None   # DEPRECATED — 0=Sun … 6=Sat; use recurrence instead
    start: str = ""                 # HH:mm
    end: str = ""                   # HH:mm
    notes: str = ""
    # Recurrence
    series_id: Optional[str] = None
    recurrence: Optional[RecurrenceRule] = None
    exceptions: list[str] = Field(default_factory=list)      # YYYY-MM-DD dates to skip
    overrides: list[OverrideEntry] = Field(default_factory=list)


class ScheduleData(BaseModel):
    version: int = 1
    entries: list[ScheduleEntry] = Field(default_factory=list)


def _normalize_schedule_entry_raw(e: Any) -> dict[str, Any]:
    """
    LLMs often emit ISO datetimes in start/end. We store date (YYYY-MM-DD) and
    start/end as HH:mm for ScheduleEntry.
    """
    if not isinstance(e, dict):
        return {}
    out: dict[str, Any] = dict(e)
    start = out.get("start")
    if isinstance(start, str) and "T" in start:
        try:
            date_part, time_part = start.split("T", 1)
            if not out.get("date"):
                out["date"] = date_part[:10]
            out["start"] = time_part[:5] if len(time_part) >= 5 else ""
        except Exception:
            pass
    end = out.get("end")
    if isinstance(end, str) and "T" in end:
        try:
            _, time_part = end.split("T", 1)
            out["end"] = time_part[:5] if len(time_part) >= 5 else ""
        except Exception:
            pass
    # Normalize recurrence: LLMs sometimes emit "daily"/"weekly" as a plain string
    rec = out.get("recurrence")
    if isinstance(rec, str) and rec:
        out["recurrence"] = {"freq": rec, "by_day": [], "interval": 1}
    elif rec is not None and not isinstance(rec, dict):
        out["recurrence"] = None
    return out


# ── Private metadata ───────────────────────────────────────────────────────────

class PrivateMetadata(BaseModel):
    profile: dict[str, Any] = Field(default_factory=dict)
    active_plans: list[Any] = Field(default_factory=list)
    notes_private: list[str] = Field(default_factory=list)


# ── Path helpers ───────────────────────────────────────────────────────────────

def _pm_dir(session_id: str, data_dir: str) -> str:
    encoded = base64.urlsafe_b64encode(session_id.encode()).decode().rstrip("=")
    return os.path.join(data_dir, "personal-manager", encoded)


def _schedule_path(session_id: str, data_dir: str) -> str:
    return os.path.join(_pm_dir(session_id, data_dir), "schedule.json")


def _private_path(session_id: str, data_dir: str) -> str:
    return os.path.join(_pm_dir(session_id, data_dir), "private.json")


# ── Schedule I/O ───────────────────────────────────────────────────────────────

def load_schedule(session_id: str, data_dir: str) -> ScheduleData:
    path = _schedule_path(session_id, data_dir)
    if not os.path.exists(path):
        return ScheduleData()
    try:
        with open(path, encoding="utf-8") as f:
            return ScheduleData.model_validate_json(f.read())
    except Exception:
        return ScheduleData()


def save_schedule(data: ScheduleData, session_id: str, data_dir: str) -> None:
    path = _schedule_path(session_id, data_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data.model_dump_json(indent=2))


# ── Schedule operations (called by tool) ───────────────────────────────────────

def schedule_read(session_id: str, data_dir: str) -> str:
    data = load_schedule(session_id, data_dir)
    raw = data.model_dump_json(indent=2)
    return raw[:32_000]


def schedule_replace(entries_json: str, session_id: str, data_dir: str) -> str:
    try:
        raw = json.loads(entries_json)
        if isinstance(raw, dict):
            raw_entries = raw.get("entries", [])
        else:
            raw_entries = raw
        if not isinstance(raw_entries, list):
            return "Error: schedule payload must be a list or { version, entries }"
        entries = [
            ScheduleEntry.model_validate(_normalize_schedule_entry_raw(e))
            for e in raw_entries
        ]
        save_schedule(ScheduleData(entries=entries), session_id, data_dir)
        return f"OK: schedule replaced with {len(entries)} entries"
    except Exception as exc:
        return f"Error: {exc}"


def schedule_add(entries_json: str, session_id: str, data_dir: str) -> str:
    try:
        data = load_schedule(session_id, data_dir)
        new_entries = json.loads(entries_json)
        if not isinstance(new_entries, list):
            return "Error: add_events expects a JSON array"
        for e in (new_entries if isinstance(new_entries, list) else []):
            data.entries.append(
                ScheduleEntry.model_validate(_normalize_schedule_entry_raw(e)),
            )
        if len(data.entries) > 200:
            return "Error: schedule is full (max 200 entries)"
        save_schedule(data, session_id, data_dir)
        return f"OK: added {len(new_entries)} entries"
    except Exception as exc:
        return f"Error: {exc}"


def schedule_update(entries_json: str, session_id: str, data_dir: str) -> str:
    try:
        data    = load_schedule(session_id, data_dir)
        raw_updates = json.loads(entries_json)
        if not isinstance(raw_updates, list):
            return "Error: update_events expects a JSON array"
        raw_map = {
            e["id"]: _normalize_schedule_entry_raw(e)
            for e in raw_updates
            if isinstance(e, dict) and "id" in e
        }
        updates = raw_map
        count   = 0
        for entry in data.entries:
            if entry.id in updates:
                patch = updates[entry.id]
                for k, v in patch.items():
                    if hasattr(entry, k):
                        setattr(entry, k, v)
                count += 1
        save_schedule(data, session_id, data_dir)
        return f"OK: updated {count} entries"
    except Exception as exc:
        return f"Error: {exc}"


def schedule_remove(ids_json: str, session_id: str, data_dir: str) -> str:
    try:
        raw_ids = json.loads(ids_json)
        if not isinstance(raw_ids, list):
            return "Error: remove_events expects a JSON array of ids"
        ids  = set(raw_ids)
        data = load_schedule(session_id, data_dir)
        before = len(data.entries)
        data.entries = [e for e in data.entries if e.id not in ids]
        save_schedule(data, session_id, data_dir)
        return f"OK: removed {before - len(data.entries)} entries"
    except Exception as exc:
        return f"Error: {exc}"


def _entry_recurrence(entry: ScheduleEntry) -> Optional[RecurrenceRule]:
    """Return the effective RecurrenceRule, migrating legacy weekday-int entries on the fly."""
    if entry.recurrence:
        return entry.recurrence
    if entry.weekday is not None:
        code = _OLD_WD_TO_BY_DAY.get(entry.weekday)
        if code:
            return RecurrenceRule(freq="weekly", by_day=[code])
    return None


def expand_series(
    entry: ScheduleEntry,
    window_start: date,
    window_end: date,
) -> list[tuple[date, ScheduleEntry]]:
    """Expand a recurring entry into (occurrence_date, effective_entry) pairs within the window."""
    rule = _entry_recurrence(entry)
    if rule is None:
        return []

    target_wds: set[int] = {
        _BY_DAY_TO_PYTHON_WD[c] for c in rule.by_day if c in _BY_DAY_TO_PYTHON_WD
    }

    series_start: Optional[date] = None
    if entry.date:
        try:
            series_start = date.fromisoformat(entry.date)
        except ValueError:
            pass

    until: Optional[date] = None
    if rule.until:
        try:
            until = date.fromisoformat(rule.until)
        except ValueError:
            pass

    exceptions_set = set(entry.exceptions)
    override_map = {ov.original_date: ov for ov in entry.overrides}

    result: list[tuple[date, ScheduleEntry]] = []
    cur = window_start
    while cur <= window_end:
        if series_start and cur < series_start:
            cur += timedelta(days=1)
            continue
        if until and cur > until:
            break

        matches = False
        if rule.freq == "daily":
            matches = True
        elif rule.freq == "weekly":
            matches = bool(target_wds) and cur.weekday() in target_wds
        elif rule.freq == "monthly":
            matches = series_start is not None and cur.day == series_start.day

        if matches:
            date_str = cur.isoformat()
            if date_str not in exceptions_set:
                ov = override_map.get(date_str)
                if ov and ov.cancelled:
                    pass
                else:
                    update: dict[str, Any] = {
                        "date": date_str,
                        "recurrence": None,
                        "exceptions": [],
                        "overrides": [],
                        "weekday": None,
                    }
                    if ov:
                        if ov.title is not None:
                            update["title"] = ov.title
                        if ov.start is not None:
                            update["start"] = ov.start
                        if ov.end is not None:
                            update["end"] = ov.end
                        if ov.notes is not None:
                            update["notes"] = ov.notes
                    result.append((cur, entry.model_copy(update=update)))

        cur += timedelta(days=1)
    return result


def schedule_add_exception(series_id: str, skip_date: str, session_id: str, data_dir: str) -> str:
    try:
        data = load_schedule(session_id, data_dir)
        for entry in data.entries:
            if entry.id == series_id or entry.series_id == series_id:
                if skip_date not in entry.exceptions:
                    entry.exceptions.append(skip_date)
                save_schedule(data, session_id, data_dir)
                return f"OK: skipped {skip_date} for '{entry.title}'"
        return f"Error: no series found with id '{series_id}'"
    except Exception as exc:
        return f"Error: {exc}"


def schedule_add_override(series_id: str, override_data: dict[str, Any], session_id: str, data_dir: str) -> str:
    try:
        original_date = override_data.get("original_date", "")
        if not original_date:
            return "Error: override must include original_date"
        ov = OverrideEntry(**{k: v for k, v in override_data.items() if k in OverrideEntry.model_fields})
        data = load_schedule(session_id, data_dir)
        for entry in data.entries:
            if entry.id == series_id or entry.series_id == series_id:
                entry.overrides = [o for o in entry.overrides if o.original_date != original_date]
                entry.overrides.append(ov)
                save_schedule(data, session_id, data_dir)
                return f"OK: override added for {original_date}"
        return f"Error: no series found with id '{series_id}'"
    except Exception as exc:
        return f"Error: {exc}"


def schedule_cancel_series_from(series_id: str, from_date: str, session_id: str, data_dir: str) -> str:
    try:
        cutoff = (date.fromisoformat(from_date) - timedelta(days=1)).isoformat()
        data = load_schedule(session_id, data_dir)
        for i, entry in enumerate(data.entries):
            if entry.id == series_id or entry.series_id == series_id:
                rule = _entry_recurrence(entry)
                if rule is None:
                    return f"Error: '{entry.title}' is not a recurring event"
                new_rule = rule.model_copy(update={"until": cutoff})
                data.entries[i] = entry.model_copy(update={"recurrence": new_rule, "weekday": None})
                save_schedule(data, session_id, data_dir)
                return f"OK: cancelled '{entry.title}' from {from_date}"
        return f"Error: no series found with id '{series_id}'"
    except Exception as exc:
        return f"Error: {exc}"


def format_schedule_for_context(session_id: str, data_dir: str) -> str:
    data = load_schedule(session_id, data_dir)
    if not data.entries:
        return "Schedule: (empty)"
    _BY_DAY_NAME = {"MO": "Mon", "TU": "Tue", "WE": "Wed", "TH": "Thu", "FR": "Fri", "SA": "Sat", "SU": "Sun"}
    lines = ["## Schedule"]
    for e in data.entries:
        rule = _entry_recurrence(e)
        if rule is not None:
            by_day_names = [_BY_DAY_NAME.get(c, c) for c in rule.by_day]
            if rule.freq == "daily" or set(rule.by_day) == {"SU", "MO", "TU", "WE", "TH", "FR", "SA"}:
                day_str = "day"
            elif by_day_names:
                day_str = ", ".join(by_day_names)
            else:
                day_str = rule.freq
            start_str = f" from {e.date}" if e.date else ""
            until_str = f" until {rule.until}" if rule.until else ""
            time_range = f" {e.start}–{e.end}" if e.start else ""
            exc_str = f" exceptions=[{', '.join(e.exceptions)}]" if e.exceptions else ""
            lines.append(f"- [{e.id}] {e.title} | Every {day_str}{start_str}{until_str}{time_range}{exc_str}".strip())
        else:
            when = e.date or "floating"
            time_range = f" {e.start}–{e.end}" if e.start else ""
            lines.append(f"- [{e.id}] {e.title} | {when}{time_range}".strip())
    return "\n".join(lines)


# ── Private metadata I/O ───────────────────────────────────────────────────────

def load_private(session_id: str, data_dir: str) -> PrivateMetadata:
    path = _private_path(session_id, data_dir)
    if not os.path.exists(path):
        return PrivateMetadata()
    try:
        with open(path, encoding="utf-8") as f:
            return PrivateMetadata.model_validate_json(f.read())
    except Exception:
        return PrivateMetadata()


def save_private(meta: PrivateMetadata, session_id: str, data_dir: str) -> None:
    path = _private_path(session_id, data_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(meta.model_dump_json(indent=2))


# ── Private metadata operations ────────────────────────────────────────────────

_ALLOWED_PATCH_KEYS = {"profile", "active_plans", "notes_private"}


def private_patch(key: str, value_json: str, session_id: str, data_dir: str) -> str:
    if key not in _ALLOWED_PATCH_KEYS:
        return f"Error: '{key}' is not a valid private key. Allowed: {', '.join(_ALLOWED_PATCH_KEYS)}"
    try:
        value = json.loads(value_json)
        meta  = load_private(session_id, data_dir)
        setattr(meta, key, value)
        save_private(meta, session_id, data_dir)
        return f"OK: {key} updated"
    except Exception as exc:
        return f"Error: {exc}"


def private_read(session_id: str, data_dir: str) -> str:
    meta = load_private(session_id, data_dir)
    return meta.model_dump_json(indent=2)


def private_export(session_id: str, data_dir: str) -> str:
    return private_read(session_id, data_dir)


# ── Todo list ──────────────────────────────────────────────────────────────────

class TodoItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str
    done: bool = False
    due: Optional[str] = None   # YYYY-MM-DD, optional


class TodoData(BaseModel):
    version: int = 1
    items: list[TodoItem] = Field(default_factory=list)


def _todos_path(session_id: str, data_dir: str) -> str:
    return os.path.join(_pm_dir(session_id, data_dir), "todos.json")


def load_todos(session_id: str, data_dir: str) -> TodoData:
    path = _todos_path(session_id, data_dir)
    if not os.path.exists(path):
        return TodoData()
    try:
        with open(path, encoding="utf-8") as f:
            return TodoData.model_validate_json(f.read())
    except Exception:
        return TodoData()


def save_todos(data: TodoData, session_id: str, data_dir: str) -> None:
    path = _todos_path(session_id, data_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data.model_dump_json(indent=2))


def todo_add(title: str, due: Optional[str], session_id: str, data_dir: str) -> str:
    data = load_todos(session_id, data_dir)
    if len(data.items) >= 200:
        return "Error: todo list is full (max 200 items)"
    item = TodoItem(title=title, due=due)
    data.items.append(item)
    save_todos(data, session_id, data_dir)
    return f"OK: added todo [{item.id}] {item.title}"


def todo_complete(item_id: str, session_id: str, data_dir: str) -> str:
    data = load_todos(session_id, data_dir)
    for item in data.items:
        if item.id == item_id:
            item.done = True
            save_todos(data, session_id, data_dir)
            return f"OK: marked [{item_id}] as done"
    return f"Error: todo id '{item_id}' not found"


def todo_remove(item_id: str, session_id: str, data_dir: str) -> str:
    data = load_todos(session_id, data_dir)
    before = len(data.items)
    data.items = [i for i in data.items if i.id != item_id]
    if len(data.items) == before:
        return f"Error: todo id '{item_id}' not found"
    save_todos(data, session_id, data_dir)
    return f"OK: removed [{item_id}]"


def todo_list(session_id: str, data_dir: str) -> str:
    data = load_todos(session_id, data_dir)
    if not data.items:
        return "Todo list: (empty)"
    lines = ["## Todos"]
    for i in data.items:
        status = "x" if i.done else " "
        due_str = f" (due {i.due})" if i.due else ""
        lines.append(f"- [{status}] [{i.id}] {i.title}{due_str}")
    return "\n".join(lines)


def format_todos_for_context(session_id: str, data_dir: str) -> str:
    return todo_list(session_id, data_dir)


# ── Upcoming events ───────────────────────────────────────────────────────────

def get_upcoming_events(session_id: str, data_dir: str, days: int = 1) -> list[dict]:
    """
    Return schedule entries whose date falls within the next `days` days,
    plus recurring entries that match a weekday within that window.
    Results are sorted by (date, start).
    """
    from datetime import date, timedelta

    data = load_schedule(session_id, data_dir)
    today = date.today()
    safe_days = max(1, days)
    window = {today + timedelta(days=i) for i in range(safe_days)}
    window_end = today + timedelta(days=safe_days - 1)
    window_weekdays = {d.isoweekday() % 7 for d in window}  # 0=Sun … 6=Sat

    results: list[dict] = []
    for e in data.entries:
        rule = _entry_recurrence(e)
        if rule is not None:
            for matched_date, occurrence in expand_series(e, today, window_end):
                results.append({
                    "id": f"{e.id}:{matched_date.isoformat()}",
                    "title": occurrence.title,
                    "date": matched_date.isoformat(),
                    "start": occurrence.start,
                    "end": occurrence.end,
                    "notes": occurrence.notes,
                })
            continue

        matched_date: date | None = None
        if e.date:
            try:
                ev_date = date.fromisoformat(e.date)
                if ev_date in window:
                    matched_date = ev_date
            except ValueError:
                pass
        elif e.weekday is not None and e.weekday in window_weekdays:
            # Find the actual date in the window that matches this weekday
            for d in sorted(window):
                if d.isoweekday() % 7 == e.weekday:
                    matched_date = d
                    break

        if matched_date is not None:
            results.append({
                "id": e.id,
                "title": e.title,
                "date": matched_date.isoformat(),
                "start": e.start,
                "end": e.end,
                "notes": e.notes,
            })

    results.sort(key=lambda x: (x["date"], x["start"] or ""))
    return results
