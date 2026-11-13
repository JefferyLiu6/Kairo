"""
Receipts: read the stores back after an action, check them against the command, say what is stored.

    with collecting() as made:
        ...anything that ends in execute_pm_action...
    reply = render(made.receipts)

Inside a collector, `execute_pm_action` snapshots the user's stores before and
after each write, and `verify` turns the difference into a `Receipt`. A reply
built by `render` comes from the stored records, so it cannot describe
something that is not there. Outside a collector nothing is snapshotted.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence

from kairo.core.domain.receipt import Change, Item, Receipt
from kairo.core.domain.types import PMAction
from kairo.storage.snapshot import STORES, Stores, read_stores
from kairo.utils.formatters import _format_recurrence_natural, _format_time_natural

# Stores where a change the command did not ask for is a problem. Memory is left
# out: Kairo may learn a preference alongside any action.
_STRICT_STORES = ("schedule", "todos", "habits", "journal")


# ── Collecting receipts for a turn ────────────────────────────────────────────

@dataclass
class Collected:
    receipts: list[Receipt] = field(default_factory=list)
    workflow_replied: bool = False   # the typed workflow answered the turn (not the chat fallback)


_collector: ContextVar[Optional[Collected]] = ContextVar("kairo_receipts", default=None)


@contextmanager
def collecting() -> Iterator[Collected]:
    """Collect a receipt for every write executed inside the block."""
    collected = Collected()
    token = _collector.set(collected)
    try:
        yield collected
    finally:
        _collector.reset(token)


def record(receipt: Receipt) -> None:
    """Add a receipt to the current collector, if there is one."""
    collected = _collector.get()
    if collected is not None:
        collected.receipts.append(receipt)


def note_workflow_reply() -> None:
    """Record that the typed workflow, whose replies are deterministic, answered this turn."""
    collected = _collector.get()
    if collected is not None:
        collected.workflow_replied = True


def observed(action: PMAction, config: Any, user_id: str, execute: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run `execute`; inside a collector, read the stores before and after and record the receipt."""
    collected = _collector.get()
    if collected is None or action.action_type not in _CHECKS:
        return execute()
    vault_dir = getattr(config, "vault_dir", None)
    before = read_stores(user_id, config.data_dir, vault_dir=vault_dir)
    result = execute()
    after = read_stores(user_id, config.data_dir, vault_dir=vault_dir)
    receipt = verify(action, bool(result.get("ok")), str(result.get("message", "")), before, after)
    if receipt is not None:
        record(receipt)
    return result


# ── What changed ──────────────────────────────────────────────────────────────

def diff_stores(before: Stores, after: Stores) -> list[Change]:
    changes: list[Change] = []
    for store in STORES:
        old, new = before.get(store, {}), after.get(store, {})
        changes += [Change(store, "added", None, new[key]) for key in new if key not in old]
        changes += [Change(store, "changed", old[key], new[key]) for key in new if key in old and old[key] != new[key]]
        changes += [Change(store, "removed", old[key], None) for key in old if key not in new]
    return changes


# ── Checking the change against the command ───────────────────────────────────

def _blank(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _same(stored: Any, commanded: Any) -> bool:
    if _blank(stored) and _blank(commanded):
        return True
    if isinstance(stored, str) and isinstance(commanded, str):
        return stored.strip() == commanded.strip()
    return stored == commanded


def _label(item: Optional[Mapping[str, Any]]) -> str:
    item = item or {}
    return str(item.get("title") or item.get("name") or item.get("body") or item.get("text") or item.get("id") or "")


class _Check:
    """Matches the changes an action should have made, and collects what does not fit."""

    def __init__(self, changes: Sequence[Change]) -> None:
        self.unclaimed = list(changes)
        self.problems: list[str] = []

    def expect(self, store: str, kind: str, *, item_id: Optional[str] = None,
               fields: Optional[Mapping[str, Any]] = None,
               also: Optional[Callable[[Change], Optional[str]]] = None) -> None:
        """Claim the change of this kind that fits best; record every way it differs from the command."""
        def problems_for(change: Change) -> list[str]:
            item = change.before if kind == "removed" else change.after
            found = [f"{name} is {item.get(name)!r}, the command said {wanted!r}"
                     for name, wanted in (fields or {}).items() if not _same(item.get(name), wanted)]
            extra = also(change) if also else None
            return found + ([extra] if extra else [])

        def is_target(change: Change) -> bool:
            item = change.before or change.after or {}
            return item_id is None or item_id in (item.get("id"), item.get("series_id"))

        candidates = [c for c in self.unclaimed if c.store == store and c.kind == kind and is_target(c)]
        if not candidates:
            target = f" {item_id}" if item_id else ""
            self.problems.append(f"nothing was {kind} in {store} for{target or ' this command'}")
            return
        best = min(candidates, key=lambda c: len(problems_for(c)))
        self.unclaimed.remove(best)
        self.problems += problems_for(best)

    def finish(self) -> list[str]:
        """Any change in a strict store that the command did not account for is a problem too."""
        return self.problems + [f"also {c.kind} in {c.store}: {_label(c.before or c.after)!r}"
                                for c in self.unclaimed if c.store in _STRICT_STORES]


def _recurrence_problem(wanted: Optional[Mapping[str, Any]]) -> Callable[[Change], Optional[str]]:
    def check(change: Change) -> Optional[str]:
        stored = (change.after or {}).get("recurrence")
        if _blank(wanted):
            return None  # the command did not ask for a repeat; the store may normalise one from a weekday
        if _blank(stored):
            return f"recurrence is missing, the command said {dict(wanted)!r}"
        if stored.get("freq") != wanted.get("freq") or set(stored.get("by_day") or []) != set(wanted.get("by_day") or []):
            return f"recurrence is {stored!r}, the command said {dict(wanted)!r}"
        return None
    return check


def _given(payload: Mapping[str, Any], *names: str) -> dict[str, Any]:
    """The named fields the command actually set."""
    return {name: payload[name] for name in names if not _blank(payload.get(name))}


def _check_todo_add(p: Mapping[str, Any], check: _Check) -> None:
    check.expect("todos", "added", fields={"title": p.get("title"), "due": p.get("due")})


def _check_todo_complete(p: Mapping[str, Any], check: _Check) -> None:
    check.expect("todos", "changed", item_id=p.get("id") or None, fields={"done": True})


def _check_todo_remove(p: Mapping[str, Any], check: _Check) -> None:
    check.expect("todos", "removed", item_id=p.get("id") or None)


def _check_schedule_add(p: Mapping[str, Any], check: _Check) -> None:
    for entry in p.get("entries") or []:
        check.expect("schedule", "added", fields=_given(entry, "title", "date", "start", "end"),
                     also=_recurrence_problem(entry.get("recurrence")))


def _check_schedule_update(p: Mapping[str, Any], check: _Check) -> None:
    for update in p.get("updates") or []:
        check.expect("schedule", "changed", item_id=update.get("id"),
                     fields=_given(update, "title", "date", "start", "end", "notes"))


def _check_schedule_remove(p: Mapping[str, Any], check: _Check) -> None:
    for item_id in p.get("ids") or []:
        check.expect("schedule", "removed", item_id=item_id)


def _check_schedule_skip(p: Mapping[str, Any], check: _Check) -> None:
    def skipped(change: Change) -> Optional[str]:
        dates = (change.after or {}).get("exceptions") or []
        return None if p.get("skip_date") in dates else f"{p.get('skip_date')} is not in exceptions {dates!r}"
    check.expect("schedule", "changed", item_id=p.get("series_id"), also=skipped)


def _check_schedule_override(p: Mapping[str, Any], check: _Check) -> None:
    original = (p.get("override") or {}).get("original_date")

    def overridden(change: Change) -> Optional[str]:
        dates = [o.get("original_date") for o in (change.after or {}).get("overrides") or []]
        return None if original in dates else f"{original} is not in overrides {dates!r}"
    check.expect("schedule", "changed", item_id=p.get("series_id"), also=overridden)


def _check_schedule_cancel_from(p: Mapping[str, Any], check: _Check) -> None:
    # Ending a series either sets its last date or, if nothing is left, removes it.
    ended = [c for c in check.unclaimed if c.store == "schedule" and c.kind in ("changed", "removed")]
    check.expect("schedule", ended[0].kind if ended else "changed", item_id=p.get("series_id"))


def _check_habit_add(p: Mapping[str, Any], check: _Check) -> None:
    check.expect("habits", "added", fields={"name": p.get("name")})


def _check_habit_checkin(p: Mapping[str, Any], check: _Check) -> None:
    def logged(change: Change) -> Optional[str]:
        before, after = (change.before or {}).get("checkins") or [], (change.after or {}).get("checkins") or []
        wanted = p.get("checkin_date")
        if wanted and wanted not in after:
            return f"{wanted} is not in checkins {after!r}"
        return None if len(after) > len(before) else "no check-in was added"
    check.expect("habits", "changed", item_id=p.get("id") or None, also=logged)


def _check_habit_remove(p: Mapping[str, Any], check: _Check) -> None:
    check.expect("habits", "removed", item_id=p.get("id") or None)


def _check_journal_append(p: Mapping[str, Any], check: _Check) -> None:
    check.expect("journal", "added", fields={"body": p.get("body")})


def _check_remember(p: Mapping[str, Any], check: _Check) -> None:
    check.expect("memory", "added", fields={"kind": "fact", "text": p.get("fact")})


def _check_private_note(p: Mapping[str, Any], check: _Check) -> None:
    check.expect("memory", "added", fields={"kind": "private_note", "text": p.get("note")})


# Every action type that writes, and how to check it. An action type not listed here has no receipt.
_CHECKS: dict[str, Callable[[Mapping[str, Any], _Check], None]] = {
    "todo_add": _check_todo_add,
    "todo_complete": _check_todo_complete,
    "todo_remove": _check_todo_remove,
    "schedule_add": _check_schedule_add,
    "schedule_update": _check_schedule_update,
    "schedule_remove": _check_schedule_remove,
    "schedule_add_exception": _check_schedule_skip,
    "schedule_add_override": _check_schedule_override,
    "schedule_cancel_series_from": _check_schedule_cancel_from,
    "habit_add": _check_habit_add,
    "habit_checkin": _check_habit_checkin,
    "habit_remove": _check_habit_remove,
    "journal_append": _check_journal_append,
    "remember": _check_remember,
    "private_note_append": _check_private_note,
}


def verify(action: PMAction, ok: bool, message: str, before: Stores, after: Stores) -> Optional[Receipt]:
    """The receipt for one executed action, or None if the action does not write."""
    checker = _CHECKS.get(action.action_type)
    if checker is None:
        return None
    changes = tuple(diff_stores(before, after))
    if not ok:
        return Receipt(action.action_type, "failed", changes, message=message)
    if not changes:
        return Receipt(action.action_type, "unverified", message=message)
    check = _Check(changes)
    checker(action.payload, check)
    problems = tuple(check.finish())
    return Receipt(action.action_type, "mismatch" if problems else "verified", changes, problems, message)


# ── Saying what is stored ─────────────────────────────────────────────────────

def _day(value: str) -> str:
    """'2026-11-17' -> 'Tue 17 Nov'. Anything unparseable is shown as stored."""
    try:
        day = date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return str(value)
    return f"{day:%a} {day.day} {day:%b}"


def _hours(item: Mapping[str, Any]) -> str:
    start, end = _format_time_natural(item.get("start") or ""), _format_time_natural(item.get("end") or "")
    return f"{start}–{end}" if start and end else start


def _when(item: Mapping[str, Any]) -> str:
    if item.get("recurrence"):
        day = _format_recurrence_natural(item["recurrence"])
    else:
        day = _day(item["date"]) if item.get("date") else ""
    return ", ".join(part for part in (day, _hours(item)) if part)


def _event(item: Mapping[str, Any]) -> str:
    when = _when(item)
    return f"{item.get('title') or 'Untitled'} — {when}" if when else str(item.get("title") or "Untitled")


def due_phrase(item: Mapping[str, Any]) -> str:
    """'due Fri 13 Nov', 'due Fri 13 Nov, 5 PM' or 'no due date' for a stored task."""
    due = item.get("due")
    if not due:
        return "no due date"
    at = _format_time_natural(str(due)[11:16]) if len(str(due)) > 10 else ""
    return f"due {_day(str(due))}" + (f", {at}" if at else "")


def _schedule_change(before: Item, after: Item) -> str:
    title = after.get("title") or "Untitled"
    skipped = [d for d in after.get("exceptions") or [] if d not in (before.get("exceptions") or [])]
    if skipped:
        return f"Skipping {title} on {', '.join(_day(d) for d in skipped)}; the rest of the series stays."
    earlier = {o.get("original_date") for o in before.get("overrides") or []}
    moved = [o.get("original_date") for o in after.get("overrides") or [] if o.get("original_date") not in earlier]
    if moved:
        return f"Changed {title} on {', '.join(_day(d) for d in moved)} only; the rest of the series stays."
    until = (after.get("recurrence") or {}).get("until")
    if until and until != (before.get("recurrence") or {}).get("until"):
        return f"Ended the series {title}; its last day is {_day(until)}."
    return f"Updated: {_event(after)} (was {_when(before) or _event(before)})."


def _sentence(change: Change) -> str:
    before, after = change.before or {}, change.after or {}
    if change.store == "schedule":
        if change.kind == "added":
            return f"Added to your calendar: {_event(after)}."
        if change.kind == "removed":
            return f"Removed from your calendar: {_event(before)}."
        return _schedule_change(before, after)
    if change.store == "todos":
        if change.kind == "added":
            return f"Added task: {after.get('title')} — {due_phrase(after)}."
        if change.kind == "removed":
            return f"Removed task: {before.get('title')}."
        if after.get("done") and not before.get("done"):
            return f"Checked off: {after.get('title')}."
        return f"Updated task: {after.get('title')} — {due_phrase(after)}."
    if change.store == "habits":
        if change.kind == "added":
            return f"Now tracking: {after.get('name')}."
        if change.kind == "removed":
            return f"Stopped tracking: {before.get('name')}."
        logged = [d for d in after.get("checkins") or [] if d not in (before.get("checkins") or [])]
        return f"Logged {after.get('name')} for {', '.join(_day(d) for d in logged) or 'today'}."
    if change.store == "journal":
        if change.kind == "removed":
            return "Removed a journal entry."
        body = str(after.get("body") or "")
        return f"Saved to your journal: “{body[:200]}{'…' if len(body) > 200 else ''}”"
    if change.store == "memory" and change.kind == "added":
        # A private note may hold something sensitive, so it is acknowledged, not repeated.
        return "Saved to your private notes." if after.get("kind") == "private_note" else f"Remembered: {after.get('text')}."
    return ""


def read_back_reply(receipts: Sequence[Receipt]) -> Optional[str]:
    """The reply for writes that were all read back from storage, or None if that cannot be said.

    A write the stores cannot confirm (already done, or made in an external calendar) is `unverified`;
    then the caller keeps whatever reply it had.
    """
    if not receipts or any(receipt.status == "unverified" for receipt in receipts):
        return None
    return render(receipts) or None


def render(receipts: Sequence[Receipt]) -> str:
    """The reply for a turn's receipts: one sentence per stored change, from the stored records."""
    lines: list[str] = []
    for receipt in receipts:
        stored = [s for s in (_sentence(change) for change in receipt.changes) if s]
        if receipt.status == "failed":
            if stored:
                lines.append("Something went wrong partway. What is stored now: " + " ".join(stored))
            elif receipt.message and not receipt.message.lower().startswith("error"):
                lines.append(receipt.message)
            else:
                lines.append("That didn't go through, and nothing was saved.")
            continue
        text = " ".join(stored) or receipt.message
        if receipt.status == "mismatch":
            text += " Note: this is not exactly what was asked — " + "; ".join(receipt.problems) + "."
        lines.append(text)
    return "\n".join(line for line in lines if line)
