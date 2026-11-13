"""
The habits domain: things the user does again and again and wants to keep count of.

Whoever calls these tools is shown the tracked habits with their ids and refers
to a habit by id, so nothing has to be found from a description.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from kairo.core.domain.commands import HabitCommand, action_from_command
from kairo.core.prompt import prompt
from kairo.domains.checks import asks_to_remember, date_word_problem
from kairo.domains.registry import Answer, DomainSpec, NotThisDomain, Proposal, ToolContext, ToolSpec
from kairo.storage.control_store import pm_db_path
from kairo.storage.habits import habit_rows

_DATE = r"^\d{4}-\d{2}-\d{2}$"


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddHabit(_Args):
    """Propose tracking a new habit: something the user does again and again and wants to keep count
    of, with no set time."""

    name: str = Field(description="The habit, in the user's own words, without words like 'habit' and 'track'.")


class LogHabit(_Args):
    """Propose logging that the user did one of the habits being tracked, today or on the day they say."""

    habit_id: str = Field(description="The id of the habit, from the habits listed.")
    date: Optional[str] = Field(pattern=_DATE, description="YYYY-MM-DD if the user said which day; otherwise null "
                                                         "for today.")


class RemoveHabit(_Args):
    """Propose no longer tracking a habit. Its history goes with it; the user is asked to approve first."""

    habit_id: str = Field(description="The id of the habit, from the habits listed.")


class ShowHabits(_Args):
    """Show the user's habits with their streaks. Use it for any question about habits or how one is going."""


def _habits(ctx: ToolContext) -> list[dict]:
    return habit_rows(pm_db_path(ctx.user_id, ctx.data_dir))


def _habit(ctx: ToolContext, habit_id: str) -> Optional[dict]:
    return next((habit for habit in _habits(ctx) if habit["id"] == habit_id), None)


def _unknown(habit_id: str) -> str:
    return f"no habit has id {habit_id}; use an id from the habits listed, or call ask_user if it is not there"


def _day(value: str) -> str:
    day = date.fromisoformat(value)
    return f"{day:%a} {day.day} {day:%b}"


def _streak(count: int) -> str:
    return f"{count} day" + ("" if count == 1 else "s")


_ASKS_FOR_A_HABIT = re.compile(r"\b(habits?|track(ing)?)\b", re.I)


def _check_add(_args: AddHabit, ctx: ToolContext) -> Optional[str]:
    # "remember that I sleep in on Sundays" describes a routine; it does not ask for one to be tracked.
    if asks_to_remember(ctx.message) and not _ASKS_FOR_A_HABIT.search(ctx.message):
        return NotThisDomain("the user asked for this to be remembered, not tracked as a habit; hand it off to "
                             "memory with hand_off", to="memory")
    return None


def _propose_add(args: AddHabit, ctx: ToolContext) -> Proposal:
    name = args.name.strip()
    existing = next((habit for habit in _habits(ctx) if habit["name"].casefold() == name.casefold()), None)
    if existing:
        return Answer(f"You're already tracking {existing['name']}.")
    return [action_from_command("habit_add", HabitCommand(operation="add", name=name), summary=f"Track habit: {name}")]


def _check_log(args: LogHabit, ctx: ToolContext) -> Optional[str]:
    if _habit(ctx, args.habit_id) is None:
        return _unknown(args.habit_id)
    day = args.date or ctx.now.date().isoformat()
    if day > ctx.now.date().isoformat():
        return f"{day} has not happened yet; a habit is logged for today or a day that has passed"
    return date_word_problem(ctx.message, day, ctx.now)


def _propose_log(args: LogHabit, ctx: ToolContext) -> Proposal:
    habit = _habit(ctx, args.habit_id)
    day = args.date or ctx.now.date().isoformat()
    if day in habit["checkins"]:
        return Answer(f"{habit['name']} is already logged for {_day(day)}. Streak: {_streak(habit['streak'])}.")
    command = HabitCommand(operation="checkin", id=habit["id"], name=habit["name"], checkin_date=day)
    return [action_from_command("habit_checkin", command, summary=f"Log habit: {habit['name']} on {_day(day)}")]


def _check_known(args: RemoveHabit, ctx: ToolContext) -> Optional[str]:
    return None if _habit(ctx, args.habit_id) else _unknown(args.habit_id)


def _propose_remove(args: RemoveHabit, ctx: ToolContext) -> Proposal:
    habit = _habit(ctx, args.habit_id)
    command = HabitCommand(operation="remove", id=habit["id"], name=habit["name"])
    return [action_from_command("habit_remove", command, risk_level="medium", requires_approval=True,
                                summary=f"Stop tracking habit: {habit['name']}")]


def _read_habits(_args: ShowHabits, ctx: ToolContext) -> str:
    habits = _habits(ctx)
    if not habits:
        return "You are not tracking any habits yet."
    lines = ["Your habits:"]
    for habit in habits:
        if not habit["checkins"]:
            lines.append(f"- {habit['name']} — not logged yet")
            continue
        streak = f"{habit['streak']}-day streak" if habit["streak"] else "no current streak"
        lines.append(f"- {habit['name']} — {streak}, last logged {_day(habit['checkins'][-1])}")
    return "\n".join(lines)


def _tracked(ctx: ToolContext) -> str:
    """The tracked habits, as whoever calls these tools is shown them."""
    habits = _habits(ctx)
    if not habits:
        return "No habits are tracked yet."
    today = ctx.now.date().isoformat()
    return "\n".join(["Habits being tracked (id in brackets):"] + [
        f"- [{habit['id']}] {habit['name']}" + (" (logged today)" if today in habit["checkins"] else "") for habit in habits])


HABITS_GUIDANCE = prompt("habits_guidance", """\
- A habit is something the user does again and again and wants to keep count of, with no set time:
  "add a habit called evening walk", "track a habit: read". Add one only when the user asks to
  track or add a habit. Something at a clock time or on set days goes to schedule, and a fact about
  their routine that they ask you to remember ("remember that I …") goes to memory.
- When the user says they did one of the habits listed ("I did my walk", "log reading for today"),
  call log_habit with its id. Set a date only when they say which day.
- If they say they did something that is neither a habit nor a task listed, call ask_user and offer
  to start tracking it as a habit.
- Call show_habits for the list of habits, a streak, or how a habit is going.""")


HABITS = DomainSpec(
    name="habits",
    summary="Habits the user tracks: starting or stopping one, logging that it was done, and streaks.",
    guidance=HABITS_GUIDANCE,
    context=_tracked,
    restate="Could you say again which habit you mean, and for which day?",
    tools=(
        ToolSpec("add_habit", "propose", AddHabit, check=_check_add, propose=_propose_add,
                 examples=("Add a habit called morning run", "Track a habit: stretch")),
        ToolSpec("log_habit", "propose", LogHabit, check=_check_log, propose=_propose_log,
                 examples=("I did my morning run", "Log morning run for today")),
        ToolSpec("remove_habit", "propose", RemoveHabit, check=_check_known, propose=_propose_remove,
                 examples=("stop tracking stretch",)),
        ToolSpec("show_habits", "read", ShowHabits, read=_read_habits,
                 examples=("list my habits", "How's my morning run streak?")),
    ),
)
