"""
The graph's state for one turn.

It is a working copy. Lasting state stays where it already lives: the user's
stores, the approval queue, working memory and the turn log. `ingest` loads what
the turn needs and `persist` writes back what changed.

Each field has one kind of writer:

    message, now, history                  ingest
    question, offer                        ingest (what was waiting on the user when the message arrived)
    call, proposer, rejection              coordinate, agent and gate
    attempts, agent_attempts               coordinate and agent (their own counter each)
    domain                                 coordinate or gate (which agent the message was handed to)
    handler                                whichever node decided who dealt with the message
    actions                                gate (validated proposals, nothing run yet)
    receipts, approvals, results           act and approval (what running them left behind)
    notes                                  any node with something to tell the user
    findings                               gate (what a lookup tool returned, for the agent that asked)
    plan, step_index, focus, findings_from step and coordinate (a message with several parts, done one part at a time)
    held                                   act (commands of a plan that wait to be asked for together)
    spent                                  step (model calls made on earlier parts of a plan)
    swept                                  sweep (which may also start a plan, for a part that was left undone)
    asked                                  whichever node asked the user a new question
    draft                                  respond (the reply as the records give it; what the models read next turn)
    reply, wording                         voice (the reply as the user reads it, and whether it was reworded)
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
    question: Optional[dict[str, Any]]        # the graph's own open question (a GraphQuestion), taken off the table
    offer: Optional[dict[str, str]]           # the approval the user may answer in plain words: {"id", "summary"}

    call: Optional[dict[str, Any]]            # the last tool call a model made: {"name", "args"}
    proposer: str                             # who made it: "coordinator" or a domain's name
    attempts: int                             # model calls the coordinator has made this turn
    agent_attempts: int                       # model calls the domain agent has made this turn
    rejection: Optional[str]                  # why the last call was sent back, for the model that made it
    domain: str                               # the agent domain the message was handed to
    handler: Optional[str]                    # who dealt with the message: a domain, "coordinator" or "approval"
    decision: str                             # an approval decided in plain words: "approve <id>" / "reject <id>"
    actions: list[PMAction]

    receipts: Annotated[list[Receipt], operator.add]
    approvals: Annotated[list[str], operator.add]     # approval prompts created this turn
    results: Annotated[list[str], operator.add]       # executors' own messages, used if a write cannot be read back
    notes: Annotated[list[str], operator.add]         # other lines for the reply: read results, questions, answers
    findings: Annotated[list[str], operator.add]      # lookup results, shown to the agent on its next call

    plan: Optional[list[dict[str, str]]]      # the parts of a message with several: [{"domain", "request"}]
    step_index: int                           # how many parts have been started
    focus: str                                # the part being done now, in the user's words
    findings_from: int                        # where this part's lookup results start in `findings`
    plan_done: bool                           # every part has been done and what waited was asked for
    swept: bool                               # the turn was checked for a part of the message left undone
    held: Annotated[list[PMAction], operator.add]     # a plan's commands that need approval, asked for once at the end
    spent: Annotated[int, operator.add]               # model calls made on the plan's earlier parts

    asked: bool                               # a new question was put to the user this turn
    draft: str
    reply: str
    wording: str                              # "reworded", or "as written (<why>)"
    route: str
