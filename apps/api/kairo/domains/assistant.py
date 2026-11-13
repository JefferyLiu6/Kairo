"""
The assistant domain: conversation, advice, planning in words, and looking things up.

It is the catch-all: a message no other domain can place ends up here. Its agent
is shown everything that is stored (tasks, calendar, habits, remembered facts)
and can answer from it. It changes nothing itself. The one thing it can propose
is putting a plan on the calendar (`block_time`): the times are its own, so the
whole plan waits for the user's approval, listed block by block.
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from kairo.core.prompt import prompt_template
from kairo.domains.habits import HABITS
from kairo.domains.memory import MEMORY
from kairo.domains.checks import past_problem
from kairo.domains.registry import DomainSpec, Proposal, ToolContext, ToolSpec
from kairo.domains.schedule import calendar_view, slot_problem, suggested_events_action
from kairo.storage.store import load_todos
from kairo.tools.receipts import due_phrase
from kairo.tools.web_search import web_search
from kairo.utils.sensitivity import _contains_sensitive_terms

MAX_TASKS = 30
MAX_RESULT = 2000     # characters of a search result shown to the agent


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WebSearch(_Args):
    """Look something up on the web when the answer needs facts from outside: weather, news, a
    definition, a price. You are shown the results and then answer."""

    query: str = Field(min_length=2, description="What to search for. Never put the user's private details in it.")


_TIME = r"^([01]\d|2[0-3]):[0-5]\d$"
MAX_BLOCKS = 8


class Block(_Args):
    title: str = Field(min_length=1, description="What the time is for, in the user's words for it where they have "
                                                 "some (a task's own title, for a task).")
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD.")
    start: str = Field(pattern=_TIME, description="24-hour HH:MM, at a time the calendar shows as free.")
    end: str = Field(pattern=_TIME, description="24-hour HH:MM, after the start.")


class BlockTime(_Args):
    """Propose putting a plan on the calendar: one block of time for each thing, at times you choose.
    Only when the user asks you to block time or to put the plan on the calendar; a plan they only
    want to hear is an answer. The user is shown every block and asked once to approve them."""

    blocks: list[Block] = Field(min_length=1, max_length=MAX_BLOCKS)


_ASKS_FOR_BLOCKS = re.compile(r"\b(block\w*|calendar|schedul\w*|book\w*|time for|slot\w*)\b", re.I)


def _check_blocks(args: BlockTime, ctx: ToolContext) -> Optional[str]:
    """The user has to have asked for time on the calendar ("plan my day" asks for a plan, not for
    events). Then every block has to be in the future, in the hours a time may be suggested in,
    free on the calendar, and clear of the other blocks."""
    if not any(_ASKS_FOR_BLOCKS.search(text) for text in ctx.wordings()):
        return ("the message asks for a plan, not for time on the calendar: give the plan with answer, and say "
                "they can ask you to block the time")
    for index, block in enumerate(args.blocks):
        problem = past_problem(block.date, ctx.now, start=block.start) or slot_problem(ctx, block.date, block.start, block.end)
        if problem:
            return f"block '{block.title}': {problem}"
        for other in args.blocks[:index]:
            if other.date == block.date and block.start < other.end and other.start < block.end:
                return f"block '{block.title}' ({block.start}–{block.end}) overlaps your own block '{other.title}'"
    return None


def _propose_blocks(args: BlockTime, _ctx: ToolContext) -> Proposal:
    return [suggested_events_action([{"title": block.title.strip(), "date": block.date, "start": block.start,
                                      "end": block.end, "recurrence": None} for block in args.blocks])]


def _check_search(args: WebSearch, _ctx: ToolContext) -> Optional[str]:
    if _contains_sensitive_terms(args.query):
        return ("that search would send private details to a search engine, so it is not run; answer without "
                "searching and say why, or call ask_user")
    return None


def _search(args: WebSearch, _ctx: ToolContext) -> str:
    return web_search(args.query)[:MAX_RESULT]


def _overview(ctx: ToolContext) -> str:
    """Everything stored for the user, as the assistant is shown it. Private notes are only counted."""
    todos = [item.model_dump(mode="json") for item in load_todos(ctx.user_id, ctx.data_dir).items if not item.done]
    tasks = (["Open tasks:"] + [f"- {t['title']}" + (f" — {due_phrase(t)}" if t.get("due") else "") for t in todos[:MAX_TASKS]]
             if todos else ["The to-do list is empty."])
    return "\n\n".join(["\n".join(tasks), calendar_view(ctx), HABITS.context(ctx), MEMORY.context(ctx)])


ASSISTANT_AGENT = prompt_template("assistant_agent", """\
You are Kairo's assistant for everything that is not a single change to the user's tasks,
calendar, habits, journal or memory: conversation, advice, planning a day or a week in words, and
looking something up.
$clock

$context

For each message, call exactly one tool.
- answer for conversation, advice and planning. What you say about the user's tasks, calendar,
  habits or what is remembered comes from what is shown above; if something is not there, say so.
  You cannot change anything: never say that something was added, changed, saved or removed, and
  never promise to do it. If the user wants a change, tell them in one line how to ask for it.
- Asked what you can do, describe Kairo as a whole: it adds and finishes tasks, puts events on the
  calendar and moves or cancels them, tracks habits, keeps a journal, and remembers what the user
  asks it to. The user only has to say so in a message.
- block_time only when the user asks you to put a plan on the calendar or to block time for things
  and leaves the times to you ("block time tomorrow for my open tasks", "plan my afternoon and
  put it on my calendar"). Give one block for each thing, at times the calendar above shows as
  free, with room between them. The user is shown the list and approves it, so they still decide.
  When they only ask for a plan ("plan my day"), use answer.
- web_search when the answer needs facts from outside.
- ask_user when you cannot tell what they want.
Keep answers short: a few sentences, or a short list for a plan.
$open_question""")


ASSISTANT = DomainSpec(
    name="assistant",
    summary="Conversation, advice, planning a day or a week across tasks and calendar (also blocking time for "
            "the plan when asked), and looking something up on the web.",
    mode="agent",
    catch_all=True,
    prompt=ASSISTANT_AGENT,
    context=_overview,
    tools=(
        ToolSpec("block_time", "propose", BlockTime, check=_check_blocks, propose=_propose_blocks,
                 examples=("block time tomorrow for my open tasks", "plan my afternoon and put it on my calendar")),
        ToolSpec("web_search", "lookup", WebSearch, check=_check_search, read=_search,
                 examples=("what's the weather like today?",)),
    ),
)
