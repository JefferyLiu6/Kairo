"""What an executed action left in storage, as read back afterwards."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

Item = dict[str, Any]
ChangeKind = Literal["added", "changed", "removed"]
ReceiptStatus = Literal["verified", "mismatch", "failed", "unverified"]


@dataclass(frozen=True)
class Change:
    """One stored item that an action added, changed or removed."""

    store: str                # schedule | todos | habits | journal | memory
    kind: ChangeKind
    before: Optional[Item]    # None when added
    after: Optional[Item]     # None when removed


@dataclass(frozen=True)
class Receipt:
    """
    The stores, read back after one action.

    verified    the stores changed the way the command said
    mismatch    the stores changed, but not the way the command said (see `problems`)
    failed      the executor reported failure; `changes` is whatever changed anyway
    unverified  the executor reported success but nothing changed locally
                (already done, or written to an external calendar)
    """

    action_type: str
    status: ReceiptStatus
    changes: tuple[Change, ...] = ()
    problems: tuple[str, ...] = ()
    message: str = ""         # the executor's own message
