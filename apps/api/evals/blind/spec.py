"""
Format of the blind eval set: what a user says, and what should be stored afterwards.

A turn's `expect` is one expectation or a list of acceptable alternatives. Each
has a `kind`:

    write     items are added, changed or removed, and nothing else changes
    approval  nothing changes yet; a matching approval request is now pending
    ask       nothing changes; Kairo asks the user a new question
    read      nothing changes; the reply names the stored items listed in `shows`
    refuse    nothing changes, nothing is queued or asked, and the reply claims nothing
    noop      nothing changes (e.g. a reject, or "that is already there")
    open      advice or conversation with no checkable answer; only guardrails apply

`given` lists what must already be true for the expectation to apply. When no
alternative applies, the turn is blocked by an earlier failure, not failed.

A pattern maps field names (dotted paths reach into nested values and list
positions) to an expected value, `null` for empty, or operators:
`{"has": [...]}`, `{"lacks": [...]}`, `{"any": [...]}`, `{"set": [...]}`,
`{"prefix": "..."}`, `{"between": [low, high]}`.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.paths import FIXTURES

DEFAULT_SET = FIXTURES / "blind_eval_set.json"

Store = Literal["schedule", "todos", "habits", "journal", "memory"]
Kind = Literal["write", "approval", "ask", "read", "refuse", "noop", "open"]
Pattern = dict[str, Any]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ItemClause(_Model):
    """An item in a store. `match` is one pattern or a list of acceptable patterns."""

    store: Store = Field(alias="in")
    match: Union[Pattern, list[Pattern]]

    def patterns(self) -> list[Pattern]:
        return self.match if isinstance(self.match, list) else [self.match]


class ChangeClause(_Model):
    """An existing item (`where`, as it was before the turn) that now fits `to`."""

    store: Store = Field(alias="in")
    where: Pattern
    to: Pattern


class RemoveClause(_Model):
    store: Store = Field(alias="in")
    where: Pattern


class ApprovalClause(_Model):
    """A pending approval request of one of these action types whose payload fits."""

    action: Union[str, list[str]]
    payload: Pattern = Field(default_factory=dict)

    def actions(self) -> list[str]:
        return self.action if isinstance(self.action, list) else [self.action]


class Given(_Model):
    """A precondition: a stored item, or a pending approval."""

    store: Optional[Store] = Field(default=None, alias="in")
    match: Optional[Union[Pattern, list[Pattern]]] = None
    approval: Optional[ApprovalClause] = None

    @model_validator(mode="after")
    def _one_condition(self) -> "Given":
        has_item = self.store is not None and self.match is not None
        if (self.store is None) != (self.match is None):
            raise ValueError("a stored-item precondition needs both `in` and `match`")
        if has_item == (self.approval is not None):
            raise ValueError("a precondition is either a stored item or a pending approval")
        return self

    def item(self) -> Optional[ItemClause]:
        return ItemClause(store=self.store, match=self.match) if self.store is not None else None


class Expect(_Model):
    kind: Kind
    given: list[Given] = Field(default_factory=list)
    adds: list[ItemClause] = Field(default_factory=list)
    changes: list[ChangeClause] = Field(default_factory=list)
    removes: list[RemoveClause] = Field(default_factory=list)
    approval: Optional[ApprovalClause] = None
    approvals_pending_max: Optional[int] = Field(default=None, ge=0)
    shows: list[ItemClause] = Field(default_factory=list)
    mentions: list[Union[str, list[str]]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _fits_its_kind(self) -> "Expect":
        writes = bool(self.adds or self.changes or self.removes)
        if self.kind == "write" and not writes:
            raise ValueError("a write expects at least one of adds, changes, removes")
        # A message with several parts can do one thing and ask approval for another in the same turn.
        if self.kind not in ("write", "approval") and writes:
            raise ValueError(f"only a write or an approval may list adds, changes or removes, not {self.kind}")
        if (self.kind == "approval") != (self.approval is not None):
            raise ValueError("`approval` is required for kind approval and not allowed elsewhere")
        if self.kind == "read" and not (self.shows or self.mentions):
            raise ValueError("a read expects `shows` or `mentions`")
        if self.kind != "read" and self.shows:
            raise ValueError("only a read may list `shows`")
        return self


class Turn(_Model):
    say: str = Field(min_length=1)
    expect: Union[Expect, list[Expect]]
    note: str = ""

    @model_validator(mode="after")
    def _has_an_expectation(self) -> "Turn":
        if isinstance(self.expect, list) and not self.expect:
            raise ValueError("a turn needs at least one expectation")
        return self

    def alternatives(self) -> list[Expect]:
        return self.expect if isinstance(self.expect, list) else [self.expect]


class Scenario(_Model):
    id: str
    persona: str
    split: Literal["dev", "holdout"]
    entities: list[str] = Field(default_factory=list)   # things a reply could invent
    never: list[ItemClause] = Field(default_factory=list)  # must not be stored at any point
    turns: list[Turn] = Field(min_length=1)


class BlindSet(_Model):
    version: int
    now: datetime        # the pinned clock; carries its UTC offset
    timezone: str
    scenarios: list[Scenario] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids_and_aware_clock(self) -> "BlindSet":
        ids = [scenario.id for scenario in self.scenarios]
        if len(set(ids)) != len(ids):
            raise ValueError("scenario ids must be unique")
        if self.now.tzinfo is None:
            raise ValueError("`now` needs a UTC offset")
        return self

    def turn_count(self) -> int:
        return sum(len(scenario.turns) for scenario in self.scenarios)


def load_blind_set(path: str | Path = DEFAULT_SET) -> BlindSet:
    return BlindSet.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def fingerprint(blind: BlindSet) -> str:
    """Short content hash of a set, so a report says exactly which messages and expectations it scored."""
    canonical = json.dumps(blind.model_dump(mode="json", by_alias=True), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
