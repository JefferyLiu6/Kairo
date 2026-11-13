"""
The memory domain: what Kairo remembers about the user, run by its own agent.

A fact the user asks Kairo to keep goes to their profile. A sensitive one
(health, money, identity; see kairo/utils/sensitivity.py) is never stored
outright: it becomes a private note that waits for the user's approval, and a
private note is never shown to a model or repeated in a reply.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from kairo.core.domain.types import PMAction
from kairo.core.prompt import prompt_template
from kairo.domains.checks import own_words_problem
from kairo.domains.registry import Answer, DomainSpec, Proposal, ToolContext, ToolSpec
from kairo.storage.control_store import list_all_audit_events
from kairo.storage.profile import read_profile
from kairo.storage.snapshot import read_stores
from kairo.storage.store import load_private
from kairo.utils.sensitivity import _contains_sensitive_terms

MAX_SHOWN = 40


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Remember(_Args):
    """Propose keeping one fact or preference about the user for later. A sensitive fact is kept as a
    private note, and only after the user approves."""

    fact: str = Field(min_length=1, description="The fact in the user's own words, without the lead-in "
                                                "('remember that', 'note that', 'for future reference').")


class ShowMemory(_Args):
    """Show what Kairo remembers about the user. Use it when they ask what you know or remember about them."""


class ShowStoredData(_Args):
    """Show a summary of everything Kairo stores for the user: how many tasks, events, habits, journal
    entries, remembered facts and private notes. Use it when they ask what data is kept about them."""


class ShowActivity(_Args):
    """Show the user's activity log: what was changed, what was asked for approval, what they
    approved or rejected. Use it when they ask for the audit log or what you have done."""


class ExportData(_Args):
    """Propose giving the user a copy of their private data. The user is asked to approve first."""


def _facts(ctx: ToolContext) -> list[str]:
    """The remembered facts: the bullets of the user's profile."""
    if not ctx.vault_dir:
        return []
    lines = (line.strip() for line in read_profile(ctx.vault_dir).splitlines())
    return [line.lstrip("-*").strip() for line in lines if line.startswith(("-", "*")) and line.lstrip("-*").strip()]


def _private_notes(ctx: ToolContext) -> int:
    return len(load_private(ctx.user_id, ctx.data_dir).notes_private)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _check_fact(args: Remember, ctx: ToolContext) -> Optional[str]:
    return own_words_problem(args.fact, ctx.wordings(), "a remembered fact")


def _propose_remember(args: Remember, ctx: ToolContext) -> Proposal:
    fact = args.fact.strip().rstrip(".").strip()   # the reply ends the sentence itself
    if _contains_sensitive_terms(fact):
        # The summary is what the approval prompt shows, so it does not repeat the fact.
        return [PMAction("private_note_append", {"note": fact}, risk_level="high", requires_approval=True,
                         summary="Save a sensitive fact to your private notes")]
    if not ctx.vault_dir:
        return Answer("Memory isn't set up here, so I can't keep that.")
    if any(known.casefold() == fact.casefold() for known in _facts(ctx)):
        return Answer(f"I already have that: {fact}.")
    return [PMAction("remember", {"fact": fact}, summary=f"Remember: {fact}")]


def _propose_export(_args: ExportData, _ctx: ToolContext) -> Proposal:
    return [PMAction("private_export", {}, risk_level="high", requires_approval=True,
                     summary="Export your private data")]


def _read_memory(_args: ShowMemory, ctx: ToolContext) -> str:
    facts, notes = _facts(ctx), _private_notes(ctx)
    kept = f"{_plural(notes, 'private note')}, which I keep but do not repeat here."
    if not facts:
        return f"I only have {kept}" if notes else "I haven't been asked to remember anything about you yet."
    more = [f"(and {len(facts) - MAX_SHOWN} more)"] if len(facts) > MAX_SHOWN else []
    return "\n".join(["What I remember about you:", *(f"- {fact}" for fact in facts[:MAX_SHOWN]), *more,
                      *([f"Plus {kept}"] if notes else [])])


def _read_stored_data(_args: ShowStoredData, ctx: ToolContext) -> str:
    stores = read_stores(ctx.user_id, ctx.data_dir, vault_dir=ctx.vault_dir)
    memory = list(stores["memory"].values())
    return "\n".join([
        "Here is everything I store for you:",
        f"- Tasks: {len(stores['todos'])}",
        f"- Calendar events: {len(stores['schedule'])}",
        f"- Habits: {len(stores['habits'])}",
        f"- Journal entries: {len(stores['journal'])}",
        f"- Things I remember about you: {len(_facts(ctx))}",
        f"- Private notes: {sum(item['kind'] == 'private_note' for item in memory)}",
        "Changes that need approval, and what was done, are kept in an audit log. Say “export my data” for a copy "
        "of the private notes.",
    ])


ACTIVITY_SHOWN = 15
_EVENTS = {"action_executed": "done", "action_failed": "failed", "approval_created": "asked for approval",
           "approval_approved": "approved", "approval_executed": "approved and done", "approval_rejected": "rejected",
           "approval_failed": "approved, but it failed"}


def _read_activity(_args: ShowActivity, ctx: ToolContext) -> str:
    """The audit log, newest first. Each line is the recorded event, never a retelling of it."""
    events = [e for e in list_all_audit_events(ctx.user_id, ctx.data_dir, limit=100) if e["eventType"] in _EVENTS]
    if not events:
        return "Nothing has been recorded in your activity log yet."
    lines = ["Your latest activity, newest first:"]
    for event in events[:ACTIVITY_SHOWN]:
        when = datetime.fromisoformat(event["createdAt"]).astimezone(ctx.now.tzinfo)
        what = event["payloadSummary"]
        if not isinstance(what, str):
            what = json.dumps(what, ensure_ascii=False)
        lines.append(f"- {when:%a} {when.day} {when:%b} {when:%H:%M} — {_EVENTS[event['eventType']]}: "
                     f"{what or event['actionType']}")
    return "\n".join(lines)


def _remembered(ctx: ToolContext) -> str:
    """What is remembered, as the memory agent is shown it. Private notes are counted, never shown."""
    facts = _facts(ctx)
    shown = (["What is remembered about the user:", *(f"- {fact}" for fact in facts[:MAX_SHOWN])] if facts
             else ["Nothing is remembered about the user yet."])
    return "\n".join([*shown, f"Private notes kept: {_private_notes(ctx)} (their contents are not shown to you)."])


MEMORY_AGENT = prompt_template("memory_agent", """\
You are Kairo's memory agent. You look after what Kairo remembers about one user: facts and
preferences they asked it to keep.
$clock

$context

For each message, call exactly one tool. You never change anything yourself: a tool call is a
proposal that is checked before it is stored, and the user is shown exactly what was stored.

- remember when the user tells you something about themselves to keep ("remember that …", "note
  that I …", "for future reference …"). Give the fact in their own words and language. You do not
  decide what is sensitive; that is checked for you.
- show_memory when they ask what you know or remember about them.
- show_stored_data when they ask what data is stored or kept about them.
- export_data when they ask for a copy or an export of their data.
- show_activity when they ask for the audit log, or what you have done or changed.
- answer for a question you can answer from what is remembered above ("do I like early
  meetings?"). If it is not there, say you don't have it. Never guess, and never say that
  something was saved, changed or deleted. A remembered fact cannot be deleted yet; say so if asked.
- ask_user when you cannot tell what they want kept.
- hand_back when the message is not about what Kairo remembers or stores.
$open_question""")


MEMORY = DomainSpec(
    name="memory",
    summary="What Kairo remembers about the user: keeping a fact or preference (\"remember that …\"), what it "
            "knows about them, what data it stores, exporting it, and the audit log of what was done.",
    mode="agent",
    prompt=MEMORY_AGENT,
    context=_remembered,
    restate="Could you tell me again, in your own words, what you'd like me to remember?",
    tools=(
        ToolSpec("remember", "propose", Remember, check=_check_fact, propose=_propose_remember,
                 examples=("remember that I prefer meetings after 10am", "Remember that my passport number is X1234567")),
        ToolSpec("export_data", "propose", ExportData, propose=_propose_export, examples=("Export my data",)),
        ToolSpec("show_memory", "read", ShowMemory, read=_read_memory, examples=("what do you know about me?",)),
        ToolSpec("show_stored_data", "read", ShowStoredData, read=_read_stored_data,
                 examples=("What data do you store about me?",)),
        ToolSpec("show_activity", "read", ShowActivity, read=_read_activity, examples=("Show me the audit log",)),
    ),
)
