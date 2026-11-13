"""The domains Kairo has. Adding one is a registration here; the graph is built from this."""
from __future__ import annotations

from kairo.domains.registry import DomainSpec, Registry
from kairo.domains.tasks import TASKS

# Everything not yet moved onto the graph is handled by the assistant that existed before it.
ASSISTANT = DomainSpec(
    name="assistant",
    summary="Calendar events and anything at a set time, habits, journal, memory, questions about any of "
            "those, and conversation.",
    mode="agent",
)

DOMAINS = Registry()
DOMAINS.register(TASKS)
DOMAINS.register(ASSISTANT)
