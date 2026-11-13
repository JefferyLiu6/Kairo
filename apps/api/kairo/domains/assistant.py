"""
The assistant domain: conversation, advice, planning in words, and looking things up.

It is the catch-all: a message no other domain can place ends up here. Its agent
is shown everything that is stored (tasks, calendar, habits, remembered facts)
and can answer from it, but it has no tool that changes anything.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from kairo.core.prompt import prompt_template
from kairo.domains.habits import HABITS
from kairo.domains.memory import MEMORY
from kairo.domains.registry import DomainSpec, ToolContext, ToolSpec
from kairo.domains.schedule import calendar_view
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
- web_search when the answer needs facts from outside.
- ask_user when you cannot tell what they want.
Keep answers short: a few sentences, or a short list for a plan.
$open_question""")


ASSISTANT = DomainSpec(
    name="assistant",
    summary="Conversation, advice, planning a day or a week across tasks and calendar, and looking something "
            "up on the web.",
    mode="agent",
    catch_all=True,
    prompt=ASSISTANT_AGENT,
    context=_overview,
    tools=(
        ToolSpec("web_search", "lookup", WebSearch, check=_check_search, read=_search,
                 examples=("what's the weather like today?",)),
    ),
)
