"""The domains Kairo has. Adding one is a registration here; the graph is built from this."""
from __future__ import annotations

from kairo.domains.registry import DomainSpec, Registry
from kairo.domains.schedule import SCHEDULE
from kairo.domains.tasks import TASKS

# Everything not yet moved onto the graph is handled by the assistant that existed before it.
ASSISTANT = DomainSpec(
    name="assistant",
    summary="Habits the user tracks (adding one, logging that it was done, streaks), the journal, facts to "
            "remember about the user, web search, planning a day across tasks and calendar, and conversation.",
    mode="legacy",
)

DOMAINS = Registry()
DOMAINS.register(TASKS)
DOMAINS.register(SCHEDULE)
DOMAINS.register(ASSISTANT)
