"""Resolve task references only against choices actually shown in this thread.

Selections retain stable IDs and are revalidated before normal action planning.
Working memory supplies account/thread isolation, cancellation and a ten-minute TTL.
"""
from __future__ import annotations

import re
from typing import Any

from ..domain.types import PMIntent, PMPlanExtraction, PMTaskExtraction
from ..persistence.store import load_todos
from ..persistence.working_memory import save_working_memory
from ..resolvers.todo import resolve_todo_id

TASK_ACTIONS = {PMIntent.COMPLETE_TODO, PMIntent.REMOVE_TODO}
_ORDINAL = re.compile(
    r"(?:(complete|finish|remove|delete|mark)\s+)?(?:the\s+)?"
    r"(first|second|third|fourth|fifth|\d+(?:st|nd|rd|th)?)"
    r"(?:\s+(?:one|task|todo))?(?:\s+(?:as\s+)?done)?[.!]?", re.I,
)
_NEW_COMMAND = re.compile(
    r"^(?:actually,?\s+)?(?:please\s+)?"
    r"(?:add|create|new|complete|finish|mark|remove|delete|schedule|book|"
    r"list|show|remember|log|move|reschedule|cancel)\b", re.I,
)


def _save(config: Any, thread: str, user: str, payload: dict) -> None:
    save_working_memory(thread, config.data_dir, user_id=user,
                        mode='awaiting_disambiguation', source='todo_target_resolution',
                        expected_reply='task_id_title_or_number', payload=payload,
                        summary='Task choices shown; awaiting an explicit selection')


def remember_todo_list(config: Any, thread: str, user: str, displayed: str) -> bool:
    rows = [i.model_dump(mode='json') for i in load_todos(user, config.data_dir).items]
    # Match both IDs and order to the executor's actual output, never infer a list
    # from arbitrary conversation text. A concurrent list change invalidates it.
    shown_ids = re.findall(r'^- \[[ x]\] \[([^\]]+)\]', displayed, re.M)
    rendered = ['## Todos']
    for row in rows:
        status = 'x' if row['done'] else ' '
        due = f" (due {row['due']})" if row.get('due') else ''
        rendered.append(f"- [{status}] [{row['id']}] {row['title']}{due}")
    if (not rows or shown_ids != [r['id'] for r in rows]
            or displayed != '\n'.join(rendered)):
        return False
    _save(config, thread, user, {'type': 'todo_list', 'candidates': rows})
    return True


def prepare_todo_target(plan: PMPlanExtraction, config: Any, thread: str, user: str) -> str | None:
    # Preserve existing partial-execution policy for mixed-action plans.
    if len(plan.tasks) != 1 or plan.tasks[0].intent not in TASK_ACTIONS:
        return None
    task = plan.tasks[0]
    if task.missing_fields or task.confidence < .75:
        return None
    result = resolve_todo_id(user, config.data_dir, task.entities)
    if result['ok']:
        task.entities['id'] = result['id']
        return None
    candidates = result.get('candidates')
    if candidates:
        _save(config, thread, user, {'type': 'todo_selection',
              'plan': plan.model_dump(mode='json'), 'candidates': candidates})
    return result['message']


def resume_todo_selection(pending: dict, message: str, config: Any, user: str
                          ) -> tuple[PMPlanExtraction | None, str | None]:
    """Return selected plan, clarification, or (None, None) for a new request."""
    text = message.strip()
    ordinal = _ORDINAL.fullmatch(text)
    if not ordinal and _NEW_COMMAND.match(text):
        return None, None
    candidates = pending.get('candidates', [])
    selected = None
    if ordinal:
        value = ordinal[2].lower()
        words = ['first', 'second', 'third', 'fourth', 'fifth']
        index = words.index(value) if value in words else int(re.match(r'\d+', value)[0]) - 1
        if 0 <= index < len(candidates):
            selected = candidates[index]
    else:
        query = re.sub(r'^(?:the|my)\s+', '', text, flags=re.I).strip(' .').casefold()
        # Exact ID/title is authoritative; otherwise a unique title substring.
        matches = [r for r in candidates if query in {r['id'].casefold(), r['title'].casefold()}]
        if not matches and query and query not in {'yes', 'ok', 'okay', 'that', 'that one', 'it'}:
            matches = [r for r in candidates if query in r['title'].casefold()]
        if len(matches) == 1:
            selected = matches[0]
    if pending['type'] == 'todo_list' and not (ordinal and ordinal[1]):
        # Merely listing tasks must never create an implied mutation request.
        return None, None
    if selected is None:
        return None, 'Which one? Please use a displayed task ID, a unique title, or its number.'
    current = {i.id: i.model_dump(mode='json') for i in load_todos(user, config.data_dir).items}
    if current.get(selected['id']) != selected:
        return None, 'That task changed or was removed. Please list your tasks again and choose a target.'
    if pending['type'] == 'todo_selection':
        plan = PMPlanExtraction.model_validate(pending['plan'])
    else:
        plan = PMPlanExtraction(tasks=[PMTaskExtraction(task_id='todo_reference',
                                      intent=PMIntent.COMPLETE_TODO)])
    task = plan.tasks[0]
    if ordinal and ordinal[1]:
        task.intent = (PMIntent.REMOVE_TODO if ordinal[1].lower() in {'remove', 'delete'}
                       else PMIntent.COMPLETE_TODO)
    task.entities = {'id': selected['id'], 'query': selected['title']}
    task.missing_fields = []
    return plan, None
