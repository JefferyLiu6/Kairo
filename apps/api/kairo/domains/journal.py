"""The journal domain: entries kept in the user's own words, newest first."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from kairo.core.domain.commands import JournalCommand, action_from_command
from kairo.core.prompt import prompt
from kairo.domains.checks import own_words_problem
from kairo.domains.registry import DomainSpec, Proposal, ToolContext, ToolSpec
from kairo.storage.control_store import pm_db_path
from kairo.storage.journal import journal_rows

SHOWN = 5             # entries shown when the user asks to see the journal
MAX_BODY = 300        # characters of an entry shown in a list


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WriteJournal(_Args):
    """Propose saving a journal entry: how the day went, a thought, a reflection the user wants kept."""

    body: str = Field(min_length=1, description="The entry in the user's exact words, without the lead-in "
                                                "('journal:', 'dear diary', 'add to my journal that').")


class ShowJournal(_Args):
    """Show the user's journal: the latest entries, or the entries that mention something."""

    query: Optional[str] = Field(description="Words to look for, if the user asked about something in particular; "
                                             "otherwise null for the latest entries.")


def _check_entry(args: WriteJournal, ctx: ToolContext) -> Optional[str]:
    return own_words_problem(args.body, ctx.wordings(), "a journal entry")


def _propose_entry(args: WriteJournal, _ctx: ToolContext) -> Proposal:
    return [action_from_command("journal_append", JournalCommand(operation="append", body=args.body.strip()),
                                summary="Add journal entry")]


def _read_journal(args: ShowJournal, ctx: ToolContext) -> str:
    query = (args.query or "").strip()
    rows = journal_rows(pm_db_path(ctx.user_id, ctx.data_dir), limit=SHOWN, query=query)
    if not rows:
        return f"No journal entries match “{query}”." if query else "Your journal is empty."
    lines = [f"Journal entries matching “{query}”:" if query else "Your latest journal entries:"]
    for row in rows:
        day = datetime.fromisoformat(row["ts"]).astimezone(ctx.now.tzinfo).date()
        body = row["body"] if len(row["body"]) <= MAX_BODY else row["body"][:MAX_BODY] + "…"
        lines.append(f"- {day:%a} {day.day} {day:%b}: {body}")
    return "\n".join(lines)


JOURNAL_GUIDANCE = prompt("journal_guidance", """\
- Call write_journal when the user wants something kept in their journal or diary: how the day
  went, a thought, a reflection ("journal: …", "dear diary …", "add to my journal that …"). Copy
  their words exactly; do not tidy, shorten or summarise them.
- A fact about the user to keep for later ("note that I …", "remember that I …") is not a journal
  entry; it goes to memory.
- Call show_journal when they want to read the journal or find what they wrote about something.""")


JOURNAL = DomainSpec(
    name="journal",
    summary="The journal: saving an entry in the user's own words, and reading or searching past entries.",
    guidance=JOURNAL_GUIDANCE,
    restate="Could you give me the journal entry again, exactly as you want it saved?",
    tools=(
        ToolSpec("write_journal", "propose", WriteJournal, check=_check_entry, propose=_propose_entry,
                 examples=("journal: rough day, but I shipped the eval", "add to my journal that I felt calm today")),
        ToolSpec("show_journal", "read", ShowJournal, read=_read_journal,
                 examples=("show my journal", "what did I write about the launch?")),
    ),
)
