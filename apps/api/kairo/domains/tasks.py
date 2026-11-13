"""
The tasks domain: the to-do list.

Tool wording follows what docs/MODEL_DATE_TEST.md measured: a task is one thing
to get done, never a repeat and never a block of time, and a due date is set
only when the user gave one.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from kairo.core.domain.commands import TodoAddCommand, TodoCompleteCommand, TodoRemoveCommand, action_from_command
from kairo.core.prompt import prompt
from kairo.domains.checks import asks_for_a_repeat, date_word_problem, dates_the_wording_allows, past_problem, time_word_problem
from kairo.domains.registry import Answer, DomainSpec, NotThisDomain, Proposal, ToolContext, ToolSpec
from kairo.storage.store import load_todos
from kairo.tools.receipts import due_phrase
from kairo.tools.resolvers.todo import resolve_todo_id

_DATE = r"^\d{4}-\d{2}-\d{2}$"
_TIME = r"^([01]\d|2[0-3]):[0-5]\d$"


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TodoItemArgs(_Args):
    title: str = Field(description="What to do, in the user's own words, without the due date, the time, or "
                                   "words like 'todo' and 'remind me'.")
    due_date: Optional[str] = Field(pattern=_DATE, description="YYYY-MM-DD if the user gave a deadline or a day; "
                                                               "otherwise null.")
    due_time: Optional[str] = Field(pattern=_TIME, description="24-hour HH:MM if the user gave a time; otherwise null.")


class AddTodos(_Args):
    """Propose one or more tasks: one-off things to get done, with or without a deadline. A task never
    repeats and never holds time on the calendar. Put every item of a list in one call."""

    items: list[TodoItemArgs] = Field(min_length=1)


class CompleteTodo(_Args):
    """Propose marking one task as done."""

    query: str = Field(description="Words from the task's title, enough to tell it from the others.")


class RemoveTodo(_Args):
    """Propose deleting one task from the list. The user is asked to approve before it is removed."""

    query: str = Field(description="Words from the task's title, enough to tell it from the others.")


class ListTodos(_Args):
    """Show the user's open tasks. Use it for any question about what is on the to-do list."""


def _check_new_todos(args: AddTodos, ctx: ToolContext) -> Optional[str]:
    if asks_for_a_repeat(ctx.message):
        return NotThisDomain("a task cannot repeat, and this message asks for something that repeats; "
                             "hand it off to schedule with hand_off", to="schedule")
    # With several items the message's date word may belong to only one of them, so a missing
    # date is accepted there; with one item, a date word in the message must be carried over.
    alone = len(args.items) == 1
    for item in args.items:
        problem = past_problem(item.due_date, ctx.now) or date_word_problem(
            ctx.message, item.due_date if item.due_date or not item.due_time else None, ctx.now,
            must_have_date=alone and not item.due_time,   # a time with no date means today
        ) or (time_word_problem(ctx.message, item.due_time) if alone else None)
        if problem:
            return f"task '{item.title}': {problem}"
    return None


def _propose_new_todos(args: AddTodos, ctx: ToolContext) -> Proposal:
    # An open task with the same title and the same due date is already there: it is not added
    # twice, whether the user repeated it or the model carried it over from an earlier message.
    on_the_list = {(item.title.strip().casefold(), item.due or None)
                   for item in load_todos(ctx.user_id, ctx.data_dir).items if not item.done}
    actions, already = [], []
    for item in args.items:
        title, day = item.title.strip(), item.due_date
        if item.due_time and not day:  # "at 4pm" with no day: the day the wording points to, else today
            allowed = dates_the_wording_allows(ctx.message, ctx.now)
            day = (min(allowed) if allowed else ctx.now.date()).isoformat()
        due = f"{day}T{item.due_time}:00" if day and item.due_time else day
        if (title.casefold(), due or None) in on_the_list:
            already.append(title)
            continue
        on_the_list.add((title.casefold(), due or None))
        actions.append(action_from_command("todo_add", TodoAddCommand(title=title, due=due), summary=f"Add todo: {title}"))
    return actions or Answer("That's already on your list: " + ", ".join(already) + ".")


def _find(query: str, ctx: ToolContext) -> tuple[Optional[str], Optional[str], Optional[Answer]]:
    """(task id, task title, None) for exactly one match; otherwise (None, None, what to tell the user)."""
    found = resolve_todo_id(ctx.user_id, ctx.data_dir, {"query": query})
    if not found["ok"]:
        ambiguous = bool(found.get("candidates"))
        return None, None, Answer(found["message"], asks=ambiguous, topic=query, elsewhere=not ambiguous)
    title = next(item.title for item in load_todos(ctx.user_id, ctx.data_dir).items if item.id == found["id"])
    return found["id"], title, None


def _propose_complete(args: CompleteTodo, ctx: ToolContext) -> Proposal:
    task_id, title, answer = _find(args.query, ctx)
    if answer:
        return answer
    return [action_from_command("todo_complete", TodoCompleteCommand(id=task_id, query=title),
                                summary=f"Mark todo complete: {title}")]


def _propose_remove(args: RemoveTodo, ctx: ToolContext) -> Proposal:
    task_id, title, answer = _find(args.query, ctx)
    if answer:
        return answer
    return [action_from_command("todo_remove", TodoRemoveCommand(id=task_id, query=title),
                                risk_level="medium", requires_approval=True, summary=f"Remove todo: {title}")]


def _read_open_todos(_args: ListTodos, ctx: ToolContext) -> str:
    items = [item.model_dump(mode="json") for item in load_todos(ctx.user_id, ctx.data_dir).items]
    open_items = [item for item in items if not item["done"]]
    if not open_items:
        return "You have no open tasks."
    lines = ["Your open tasks:"]
    for item in open_items:
        lines.append(f"- {item['title']}" + (f" — {due_phrase(item)}" if item.get("due") else ""))
    done = len(items) - len(open_items)
    return "\n".join(lines + ([f"Done: {done}."] if done else []))


MAX_SHOWN = 30


def _open_tasks(ctx: ToolContext) -> str:
    """The open tasks, as whoever calls these tools is shown them."""
    titles = [item.title for item in load_todos(ctx.user_id, ctx.data_dir).items if not item.done]
    if not titles:
        return "The to-do list is empty."
    more = [f"(and {len(titles) - MAX_SHOWN} more)"] if len(titles) > MAX_SHOWN else []
    return "\n".join(["Open tasks on the to-do list:", *(f"- {title}" for title in titles[:MAX_SHOWN]), *more])


TASKS_GUIDANCE = prompt("tasks_guidance", """\
- A task is a one-off thing to get done, with or without a deadline. Put every item of a list in
  one add_todos call.
- A one-off reminder is a task, due at the time given: "remind me at 9pm to take the bins out"
  is the task "take the bins out" with due_time 21:00. A reminder that repeats goes to schedule.
- Anything that repeats is not a task, and neither is a call, meeting, session, appointment, shift,
  pickup or block of time on the calendar. Those go to schedule, and so does every request to move,
  cancel or skip one and every question about the schedule or what is coming up.
- Dates are YYYY-MM-DD and times are 24-hour HH:MM, worked out from the dates above. Set a due
  date or time only when the user gave one, and always when they did.
- A title is the user's own words for the thing, in their language, without the date, the time, or
  words like "todo" and "remind me".
- To finish or remove a task, give words from its title. Use the conversation to work out what
  "it" or "the second one" refers to. Finish a task only if it is on the list; never add a task
  for something the user says they have already done.
- A question about tasks ("what should I do first?", "what's due today?") is answered with
  list_todos. Never finish or remove a task because of a question.
- "Add" always means add_todos, even when something like it is already on the list. Call
  remove_todo only when the user asks to delete or remove a task.
- remove_todo removes one task from the to-do list above. Only when the user asks to delete every
  task at once ("delete all my todos"), call ask_user: say that tasks are removed one at a time and
  ask which to start with. Cancelling anything that is not on the to-do list is not yours.""")


TASKS = DomainSpec(
    name="tasks",
    guidance=TASKS_GUIDANCE,
    restate="Could you give me the exact day and time for “{message}”?",
    context=_open_tasks,
    summary="The to-do list: adding things to get done, finishing or removing a task, and what is on the list.",
    tools=(
        ToolSpec("add_todos", "propose", AddTodos, check=_check_new_todos, propose=_propose_new_todos,
                 examples=("add todo fix figure 3 due friday", "brain dump: email advisor, buy cat food",
                           "remind me at 4pm to leave for the dentist")),
        ToolSpec("complete_todo", "propose", CompleteTodo, propose=_propose_complete,
                 examples=("done email advisor", "mark the field trip form todo as done")),
        ToolSpec("remove_todo", "propose", RemoveTodo, propose=_propose_remove,
                 examples=("delete todo expense report",)),
        ToolSpec("list_todos", "read", ListTodos, read=_read_open_todos,
                 examples=("show my todos", "what follow-ups do I owe people?")),
    ),
)
