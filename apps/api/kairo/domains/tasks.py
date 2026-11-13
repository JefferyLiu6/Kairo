"""
The tasks domain: the to-do list.

Whoever calls these tools is shown the open tasks with their ids and refers to a
task by id, the way the calendar agent refers to an event. So "the advisor one",
"it" or "the second one" is settled by the model from the list and the
conversation, and code never has to find a task from a description. The user is
only asked which task when the model cannot tell.

Tool wording follows what docs/MODEL_DATE_TEST.md measured: a task is one thing
to get done, never a repeat and never a block of time, and a due date is set
only when the user gave one.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from kairo.core.domain.commands import TodoAddCommand, TodoCompleteCommand, TodoRemoveCommand, action_from_command
from kairo.core.prompt import prompt
from kairo.domains.checks import asks_for_a_repeat, date_word_problem, dates_the_wording_allows, not_what_was_said, past_problem, time_word_problem
from kairo.domains.registry import Answer, DomainSpec, NotThisDomain, Proposal, ToolContext, ToolSpec
from kairo.storage.store import load_todos
from kairo.tools.receipts import due_phrase

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

    task_id: str = Field(description="The id of the task, from the to-do list shown.")


class RemoveTodos(_Args):
    """Propose deleting one or more tasks from the list. The user is asked once to approve, and is
    shown every task that would go."""

    task_ids: list[str] = Field(min_length=1, description="The id of each task to delete, from the to-do list shown.")


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


def _open(ctx: ToolContext) -> dict[str, str]:
    """The open tasks: id -> title."""
    return {item.id: item.title for item in load_todos(ctx.user_id, ctx.data_dir).items if not item.done}


def _unknown(task_ids: list[str], ctx: ToolContext) -> Optional[NotThisDomain]:
    """Why these ids cannot be acted on, if one of them is not an open task. What the user means may
    not be a task at all, so after one retry the message goes to whoever can see everything."""
    missing = next((task_id for task_id in task_ids if task_id not in _open(ctx)), None)
    if missing is None:
        return None
    return NotThisDomain(f"no open task has id {missing}; use an id from the to-do list above, or call ask_user if "
                         "you cannot tell which task is meant. If it is something else (a habit, a calendar "
                         "event), use the tool or hand-off for that instead")


def _not_what_was_said(task_ids: list[str], ctx: ToolContext) -> Optional[str]:
    """The model picks a task by id, so its choice is held to the user's words (checks.not_what_was_said)."""
    return not_what_was_said(task_ids, _open(ctx), ctx.wordings())


def _check_complete(args: CompleteTodo, ctx: ToolContext) -> Optional[str]:
    return _unknown([args.task_id], ctx) or _not_what_was_said([args.task_id], ctx)


def _check_remove(args: RemoveTodos, ctx: ToolContext) -> Optional[str]:
    return _unknown(args.task_ids, ctx) or _not_what_was_said(args.task_ids, ctx)


def _propose_complete(args: CompleteTodo, ctx: ToolContext) -> Proposal:
    title = _open(ctx)[args.task_id]
    return [action_from_command("todo_complete", TodoCompleteCommand(id=args.task_id, query=title),
                                summary=f"Mark todo complete: {title}")]


def _propose_remove(args: RemoveTodos, ctx: ToolContext) -> Proposal:
    titles = _open(ctx)
    return [action_from_command("todo_remove", TodoRemoveCommand(id=task_id, query=titles[task_id]),
                                risk_level="medium", requires_approval=True, summary=f"Remove todo: {titles[task_id]}")
            for task_id in dict.fromkeys(args.task_ids)]            # a task named twice is removed once


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
    items = [item.model_dump(mode="json") for item in load_todos(ctx.user_id, ctx.data_dir).items if not item.done]
    if not items:
        return "The to-do list is empty."
    more = [f"(and {len(items) - MAX_SHOWN} more)"] if len(items) > MAX_SHOWN else []
    listed = [f"- [{item['id']}] {item['title']}" + (f" — {due_phrase(item)}" if item.get("due") else "")
              for item in items[:MAX_SHOWN]]
    return "\n".join(["Open tasks on the to-do list (the id of each task is in brackets):", *listed, *more])


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
- To finish or remove a task, give its id from the to-do list above. Work out which task the user
  means from the list and the conversation: "the advisor one", "it", "the second one". When one
  task fits better than the others (its title is what the user said, or it is the one just talked
  about), take it. Call ask_user only when two tasks fit equally well. Finish a task only if it is
  on the list; never add a task for something the user says they have already done.
- Blocking time on the calendar for tasks ("block time tomorrow for my open tasks") changes no
  task: hand it off to assistant.
- A question about tasks ("what should I do first?", "what's due today?") is answered with
  list_todos. Never finish or remove a task because of a question.
- "Add" always means add_todos, even when something like it is already on the list. Call
  remove_todos only when the user asks to delete or remove tasks.
- remove_todos removes tasks from the to-do list above, by id. For every task at once ("delete all
  my todos"), give every id on the list. The user is
  asked once to approve and is shown each one. Cancelling anything that is not on the to-do list is
  not yours.""")


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
        ToolSpec("complete_todo", "propose", CompleteTodo, check=_check_complete, propose=_propose_complete,
                 examples=("done email advisor", "mark the field trip form todo as done")),
        ToolSpec("remove_todos", "propose", RemoveTodos, check=_check_remove, propose=_propose_remove,
                 examples=("delete todo expense report", "Delete all my todos")),
        ToolSpec("list_todos", "read", ListTodos, read=_read_open_todos,
                 examples=("show my todos", "what follow-ups do I owe people?")),
    ),
)
