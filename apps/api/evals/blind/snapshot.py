"""
A snapshot of everything Kairo has stored for one user, plus what it is waiting on.

The stores are read by `kairo.storage.snapshot`, the same reader Kairo uses to
read state back after an action. This adds the two things only an eval needs:
pending approval requests and the open question. Nothing here depends on which
path wrote the state or on what any reply said.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from kairo.core.domain.pending import is_question, parse_pending
from kairo.storage.control_store import list_approval_requests
from kairo.storage.snapshot import STORES, Item, read_stores
from kairo.storage.working_memory import load_active_working_memory

__all__ = ["STORES", "Item", "OpenQuestion", "Snapshot", "read_snapshot"]


@dataclass(frozen=True)
class OpenQuestion:
    """A question Kairo is waiting on. `topic` says what it is about; `stamp` changes when it is asked again."""

    topic: str
    stamp: str


@dataclass(frozen=True)
class Snapshot:
    stores: dict[str, dict[str, Item]] = field(default_factory=dict)  # store -> item id -> item
    approvals: dict[str, Item] = field(default_factory=dict)          # pending only: id -> {action, payload}
    question: Optional[OpenQuestion] = None

    def items(self, store: str) -> list[Item]:
        return list(self.stores.get(store, {}).values())

    def text(self) -> str:
        """Everything stored or awaiting approval, as one lowercase string to search."""
        return json.dumps([self.stores, self.approvals], ensure_ascii=False, default=str).casefold()


def read_snapshot(user_id: str, thread_id: str, data_dir: str, *, vault_dir: Optional[str] = None) -> Snapshot:
    return Snapshot(
        stores=read_stores(user_id, data_dir, vault_dir=vault_dir),
        approvals={
            record.id: {"id": record.id, "action": record.action_type, "payload": record.payload,
                        "summary": record.summary}
            for record in list_approval_requests(user_id, data_dir, status="pending", limit=100)
        },
        question=_question(user_id, thread_id, data_dir),
    )


def _question(user_id: str, thread_id: str, data_dir: str) -> Optional[OpenQuestion]:
    record = load_active_working_memory(thread_id, data_dir, user_id=user_id)
    if record is None:
        return None
    try:
        pending = parse_pending(record.payload)
    except ValueError:
        return None
    if not is_question(pending):
        return None
    return OpenQuestion(topic=_topic(pending), stamp=f"{record.id}@{record.last_updated_at}")


def _topic(pending: Any) -> str:
    """What the question is about: the kind of request and the thing it names."""
    plan = getattr(pending, "plan", None)
    if plan is not None and plan.tasks:
        blocking = getattr(pending, "blocking_task_id", "")
        task = next((t for t in plan.tasks if t.task_id == blocking), plan.tasks[0])
        intent, entities = task.intent, task.entities
    else:
        intent, entities = getattr(pending, "intent", None), getattr(pending, "entities", {}) or {}
    subject = next((str(entities[key]) for key in ("title", "query", "name", "body", "fact") if entities.get(key)),
                   str(getattr(pending, "original_message", "") or getattr(pending, "topic", "") or ""))
    return f"{getattr(intent, 'value', intent) or pending.type}:{subject.casefold()}"
