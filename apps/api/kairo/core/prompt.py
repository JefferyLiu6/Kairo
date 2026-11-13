"""
Versioned prompts.

Every prompt sent to a model is declared once with `prompt(name, text)` or
`prompt_template(name, template)`. Its version is the first 8 hex digits of the
SHA-256 of its text, so editing a prompt changes its version automatically and
there is no hand-maintained "v2" to forget.

A `Prompt` is a `str`, so it drops into existing code unchanged. When one is
sent to a model, `record_prompt` attaches `prompt.<name>: <version>` to the
current LangSmith run, so every trace shows exactly which prompt text produced it.

tests/fixtures/prompt_versions.json snapshots every version; a test fails when
a prompt changes until `make prompt-versions` updates it, so each prompt edit
appears as a one-line diff in review.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from string import Template
from typing import Any, Iterator, Optional

from kairo.core.observability.tracing import tag_prompt


def _version(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


class Prompt(str):
    """Prompt text that knows its name and version."""

    name: str
    version: str

    def __new__(cls, name: str, text: str, *, version: str | None = None) -> "Prompt":
        obj = super().__new__(cls, text)
        obj.name = name
        obj.version = version or _version(text)
        return obj

    @property
    def id(self) -> str:
        return f"{self.name}@{self.version}"


class PromptTemplate:
    """A prompt with `$placeholders` filled per call (e.g. today's date).

    The version is the hash of the template, so it changes when the wording
    changes, not every time the date does. `$` placeholders (string.Template)
    leave JSON braces in the text alone.
    """

    def __init__(self, name: str, template: str) -> None:
        self.name = name
        self.template = Template(template)
        self.version = _version(template)

    @property
    def id(self) -> str:
        return f"{self.name}@{self.version}"

    def render(self, **values: Any) -> Prompt:
        return Prompt(self.name, self.template.substitute(values), version=self.version)


_REGISTRY: dict[str, Prompt | PromptTemplate] = {}


def _register(item: Prompt | PromptTemplate) -> Any:
    existing = _REGISTRY.get(item.name)
    if existing is not None and existing.version != item.version:
        raise ValueError(f"two different prompts are named {item.name!r}")
    _REGISTRY[item.name] = item
    return item


def prompt(name: str, text: str) -> Prompt:
    """Declare a fixed prompt."""
    return _register(Prompt(name, text))


def prompt_template(name: str, template: str) -> PromptTemplate:
    """Declare a prompt with `$placeholders`."""
    return _register(PromptTemplate(name, template))


def prompt_versions() -> dict[str, str]:
    """name -> version for every declared prompt (import the modules that declare them first)."""
    return {name: item.version for name, item in sorted(_REGISTRY.items())}


_used: ContextVar[Optional[dict[str, str]]] = ContextVar("kairo_prompts_used", default=None)


@contextmanager
def collecting_prompts() -> Iterator[dict[str, str]]:
    """Note every versioned prompt sent inside the block, as name -> version."""
    used: dict[str, str] = {}
    token = _used.set(used)
    try:
        yield used
    finally:
        _used.reset(token)


def prompts_used() -> dict[str, str]:
    """The prompts sent so far in the current collector; empty outside one."""
    return dict(_used.get() or {})


def record_prompt(text: Any) -> None:
    """Attach the prompt's version to the current trace and turn, if `text` is a versioned prompt."""
    if isinstance(text, Prompt):
        tag_prompt(text.name, text.version)
        used = _used.get()
        if used is not None:
            used[text.name] = text.version
