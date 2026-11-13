"""The domains Kairo has. Adding one is a registration here; the graph is built from this."""
from __future__ import annotations

from kairo.domains.habits import HABITS
from kairo.domains.journal import JOURNAL
from kairo.domains.memory import MEMORY
from kairo.domains.registry import DomainSpec, Registry
from kairo.domains.schedule import SCHEDULE
from kairo.domains.tasks import TASKS

# Everything not yet moved onto the graph is handled by the assistant that existed before it.
ASSISTANT = DomainSpec(
    name="assistant",
    summary="Web search, planning a day across tasks and calendar, advice, and conversation.",
    mode="legacy",
)

DOMAINS = Registry()
DOMAINS.register(TASKS)
DOMAINS.register(HABITS)
DOMAINS.register(JOURNAL)
DOMAINS.register(SCHEDULE)
DOMAINS.register(MEMORY)
DOMAINS.register(ASSISTANT)
