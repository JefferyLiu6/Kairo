"""The domains Kairo has. Adding one is a registration here; the graph is built from this."""
from __future__ import annotations

from kairo.domains.assistant import ASSISTANT
from kairo.domains.habits import HABITS
from kairo.domains.journal import JOURNAL
from kairo.domains.memory import MEMORY
from kairo.domains.registry import Registry
from kairo.domains.schedule import SCHEDULE
from kairo.domains.tasks import TASKS

DOMAINS = Registry()
DOMAINS.register(TASKS)
DOMAINS.register(HABITS)
DOMAINS.register(JOURNAL)
DOMAINS.register(SCHEDULE)
DOMAINS.register(MEMORY)
DOMAINS.register(ASSISTANT)
