"""
The graph's state for one turn.

It is a working copy. Lasting state stays where it already lives: the user's
stores, the approval queue, working memory and the turn log. `ingest` loads what
the turn needs and `persist` writes back what changed.

Each field has one kind of writer:

    message, now, history, open_question   ingest
    call, attempts, rejection              coordinate and gate
    actions                                gate (validated proposals, nothing run yet)
    receipts, approvals, results           act and approval (what running them left behind)
    notes                                  any node with something to tell the user
    reply                                  respond, or the assistant a turn was handed to
    route                                  the node that just ran, to pick the next one

The list fields are appended to, never replaced, so nodes cannot erase each
other's results.
"""
from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, Any, Optional, TypedDict

from kairo.core.domain.receipt import Receipt
from kairo.core.domain.types import PMAction


class GraphState(TypedDict, total=False):
    message: str
    now: datetime
    history: list[tuple[str, str]]            # recent (role, text) turns
    open_question: Optional[str]              # what the user was last asked, for the coordinator

    call: Optional[dict[str, Any]]            # the coordinator's tool call: {"name", "args"}
    attempts: int                             # how many times the coordinator has been asked this turn
    rejection: Optional[str]                  # why the gate sent the last call back
    actions: list[PMAction]

    receipts: Annotated[list[Receipt], operator.add]
    approvals: Annotated[list[str], operator.add]     # approval prompts created this turn
    results: Annotated[list[str], operator.add]       # executors' own messages, used if a write cannot be read back
    notes: Annotated[list[str], operator.add]         # other lines for the reply: read results, questions, answers

    reply: str
    route: str
    handled_by_assistant: bool                # the previous assistant answered and logged the turn itself
