"""
The tool registry: one `ToolSpec` per tool, grouped into domains.

Everything a model is shown about a tool comes from its spec: the name, the
description (the argument model's docstring) and the strict argument schema.
A tool never writes. A `propose` tool turns its arguments into typed commands
(`PMAction`) that the gate checks and executes; a `read` tool returns text.

A domain runs one of three ways:

    inline   its tools are given to the coordinator, which calls them itself
    agent    the coordinator hands the message to the domain's own agent, which has
             its own prompt, its own tools and its own view of the domain's store
    legacy   the coordinator hands the message to the assistant that existed before
             the graph; there is at most one, and it is the fallback

Moving a domain between inline and agent is a change to its spec, not to the graph.

`Registry.register` refuses an incomplete domain, so a tool cannot exist
without the parts that make it safe to call.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Literal, Optional, Union

from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel

from kairo.core.domain.types import PMAction
from kairo.core.prompt import PromptTemplate

_TOOL_NAME = re.compile(r"[a-z][a-z0-9_]*")


@dataclass(frozen=True)
class ToolContext:
    """What a tool may know about the turn it is called in."""

    user_id: str
    thread_id: str
    data_dir: str
    now: datetime
    message: str = ""               # the user's message, for checks against its own wording
    vault_dir: Optional[str] = None
    request: str = ""               # the earlier message this one answers a question about, if it does

    def wordings(self) -> tuple[str, ...]:
        """The texts a proposal is checked against: this message first, then the request it completes."""
        return tuple(text for text in (self.message, self.request) if text)


@dataclass(frozen=True)
class Answer:
    """What to tell the user when a tool cannot or need not act."""

    text: str
    asks: bool = False              # the text is a question the user is expected to answer
    topic: str = ""                 # what the question is about, for the open-question record
    elsewhere: bool = False         # nothing in this domain matched; the request may belong to another one


class NotThisDomain(str):
    """A check's verdict that the request belongs elsewhere. The text says why, and `to` names the
    domain it belongs to if that is known; if the model does not hand it off itself, the graph does."""

    to: str

    def __new__(cls, text: str, to: str = "") -> "NotThisDomain":
        verdict = super().__new__(cls, text)
        verdict.to = to
        return verdict


@dataclass(frozen=True)
class Proposed:
    """Typed commands, plus anything the user should be told alongside them (a clash, a default taken)."""

    actions: list[PMAction]
    notes: tuple[str, ...] = ()


Proposal = Union[list[PMAction], Proposed, Answer]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: Literal["read", "propose"]
    args: type[BaseModel]                                   # the model-facing arguments; its docstring is the description
    examples: tuple[str, ...] = ()                          # messages this tool should be called for
    check: Optional[Callable[[Any, ToolContext], Optional[str]]] = None   # why the arguments must be redone, if so
    propose: Optional[Callable[[Any, ToolContext], Proposal]] = None      # arguments -> typed commands, or an answer
    read: Optional[Callable[[Any, ToolContext], str]] = None              # arguments -> text for the user

    @property
    def description(self) -> str:
        return " ".join((self.args.__doc__ or "").split())

    def schema(self) -> dict[str, Any]:
        """The tool as a strict function definition: the model cannot omit a field or change its type."""
        tool = convert_to_openai_tool(self.args, strict=True)
        tool["function"]["name"] = self.name
        tool["function"]["description"] = self.description
        return tool


@dataclass(frozen=True)
class DomainSpec:
    name: str
    summary: str                                            # one line, shown to the coordinator for routing
    tools: tuple[ToolSpec, ...] = ()
    mode: Literal["inline", "agent", "legacy"] = "inline"
    prompt: Optional[PromptTemplate] = None                 # agent only: what its model is told ($clock, $context, $open_question)
    context: Optional[Callable[[ToolContext], str]] = None  # the domain's store, as whoever calls its tools is shown it

    def bundle(self) -> str:
        """One version for everything this domain's agent is told: its prompt and its tool definitions."""
        shown = (self.prompt.version if self.prompt else "") + json.dumps([t.schema() for t in self.tools], sort_keys=True)
        return hashlib.sha256(shown.encode("utf-8")).hexdigest()[:8]


class Registry:
    def __init__(self) -> None:
        self._domains: dict[str, DomainSpec] = {}
        self._tools: dict[str, ToolSpec] = {}

    def register(self, domain: DomainSpec) -> None:
        """Add a domain. Raises ValueError, naming the gap, if any part of it is missing."""
        if domain.name in self._domains:
            raise ValueError(f"domain {domain.name!r} is already registered")
        if domain.mode in ("inline", "agent") and not domain.tools:
            raise ValueError(f"{domain.mode} domain {domain.name!r} has no tools")
        if domain.mode == "agent" and domain.prompt is None:
            raise ValueError(f"agent domain {domain.name!r} needs a prompt")
        if domain.mode == "legacy" and (domain.tools or self.fallback() is not None):
            raise ValueError(f"legacy domain {domain.name!r}: there is one legacy domain, and it has no tools here")
        seen: set[str] = set()
        for tool in domain.tools:
            if not _TOOL_NAME.fullmatch(tool.name):
                raise ValueError(f"tool name {tool.name!r} must be lower_snake_case")
            if tool.name in seen or tool.name in self._tools:
                raise ValueError(f"tool {tool.name!r} is registered more than once")
            seen.add(tool.name)
            if not tool.description:
                raise ValueError(f"tool {tool.name!r} needs a description (the docstring of its arguments)")
            if tool.kind == "propose" and tool.propose is None:
                raise ValueError(f"tool {tool.name!r} must say what it proposes (propose=...)")
            if tool.kind == "propose" and not tool.examples:
                raise ValueError(f"tool {tool.name!r} needs at least one example message")
            if tool.kind == "read" and tool.read is None:
                raise ValueError(f"tool {tool.name!r} must say what it reads (read=...)")
            tool.schema()  # fails here, not in front of a user, if the arguments cannot be made strict
        self._domains[domain.name] = domain
        self._tools.update({tool.name: tool for tool in domain.tools})

    def tool(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise ValueError(f"unknown tool {name!r}") from None

    def inline_domains(self) -> list[DomainSpec]:
        return [domain for domain in self._domains.values() if domain.mode == "inline"]

    def inline_tools(self) -> list[ToolSpec]:
        return [tool for domain in self._domains.values() if domain.mode == "inline" for tool in domain.tools]

    def agent_domains(self) -> list[DomainSpec]:
        return [domain for domain in self._domains.values() if domain.mode == "agent"]

    def hand_off_domains(self) -> list[DomainSpec]:
        """Every domain the coordinator can hand a message to: the agents, then the legacy fallback."""
        return [domain for mode in ("agent", "legacy") for domain in self._domains.values() if domain.mode == mode]

    def fallback(self) -> Optional[DomainSpec]:
        return next((domain for domain in self._domains.values() if domain.mode == "legacy"), None)

    def domain(self, name: str) -> Optional[DomainSpec]:
        return self._domains.get(name)

    def domain_of(self, tool_name: str) -> Optional[DomainSpec]:
        return next((d for d in self._domains.values() if any(t.name == tool_name for t in d.tools)), None)

    def routing_lines(self) -> str:
        """One line per domain the coordinator can hand off to."""
        return "\n".join(f"- {domain.name}: {domain.summary}" for domain in self.hand_off_domains())

    def fingerprint(self) -> str:
        """Short hash of everything a model is shown about the tools and domains.

        Rewording a description or renaming an argument changes it, the same way editing a
        prompt changes the prompt's version.
        """
        shown = {
            "tools": [tool.schema() for tool in self._tools.values()],
            "domains": [(d.name, d.summary, d.mode) for d in self._domains.values()],
        }
        return hashlib.sha256(json.dumps(shown, sort_keys=True).encode("utf-8")).hexdigest()[:8]
