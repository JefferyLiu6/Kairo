"""
The paths the blind eval can score.

A path turns a message into a reply for one user in one data directory. Every
path writes to the same Kairo stores, so they share one snapshot reader and only
differ in how a message is sent. To score a new path, add a class with `name`,
`describe()` and `open()` and register it in `PATHS`.
"""
from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional, Protocol

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.context import register_configure_hook

from evals import prompt_versions
from evals.blind.ledger import prompt_set
from evals.blind.snapshot import Snapshot, read_snapshot
from kairo.core.domain.session import normalize_pm_session_id
from kairo.core.llm_env import load_default_llm_from_env
from kairo.storage.control_store import pm_db_path

_MODEL_ENV = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OLLAMA_API_KEY", "OLLAMA", "OLLAMA_BASE_URL")


class PathUnavailable(RuntimeError):
    """The path cannot run here (for example, no model credentials)."""


@dataclass(frozen=True)
class Sent:
    reply: str
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class Session(Protocol):
    def send(self, message: str) -> Sent: ...
    def snapshot(self) -> Snapshot: ...


class Path(Protocol):
    name: str
    def describe(self) -> dict[str, Any]: ...
    def open(self, scenario_id: str, data_dir: str) -> Session: ...


# ── Counting model calls ──────────────────────────────────────────────────────

class _Usage(BaseCallbackHandler):
    def __init__(self) -> None:
        self.calls = self.input_tokens = self.output_tokens = 0

    def on_llm_end(self, response: Any, **_: Any) -> None:
        self.calls += 1
        for generations in getattr(response, "generations", []):
            for generation in generations:
                usage = getattr(getattr(generation, "message", None), "usage_metadata", None) or {}
                self.input_tokens += int(usage.get("input_tokens") or 0)
                self.output_tokens += int(usage.get("output_tokens") or 0)


_usage: ContextVar[Optional[_Usage]] = ContextVar("blind_eval_usage", default=None)
register_configure_hook(_usage, inheritable=True)  # LangChain attaches the handler to every run in this context


@contextmanager
def count_model_calls() -> Iterator[_Usage]:
    """Count every chat-model call made inside the block, whoever makes it."""
    usage = _Usage()
    token = _usage.set(usage)
    try:
        yield usage
    finally:
        _usage.reset(token)


@contextmanager
def _without_model_credentials() -> Iterator[None]:
    saved = {name: os.environ.pop(name) for name in _MODEL_ENV if name in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


# ── Shared session ────────────────────────────────────────────────────────────

class _KairoSession:
    """One fresh user and thread in `data_dir`; `reply_to` is the path's way of answering."""

    def __init__(self, scenario_id: str, data_dir: str, *, vault_in_user_dir: bool) -> None:
        # A new id per session: in-process caches are keyed by user and must not carry over between runs.
        self.user_id = f"blind-{scenario_id}-{uuid.uuid4().hex[:6]}"
        self.thread_id = normalize_pm_session_id(self.user_id)
        self.data_dir = data_dir
        self.vault_dir = os.path.dirname(pm_db_path(self.user_id, data_dir)) if vault_in_user_dir else None
        self.reply_to: Callable[[str], str]
        self.config: Any = None   # the path's own config, for a path that builds on another

    def send(self, message: str) -> Sent:
        with count_model_calls() as usage:
            reply = self.reply_to(message)
        return Sent(reply, usage.calls, usage.input_tokens, usage.output_tokens)

    def snapshot(self) -> Snapshot:
        return read_snapshot(self.user_id, self.thread_id, self.data_dir, vault_dir=self.vault_dir)


# ── Paths ─────────────────────────────────────────────────────────────────────

class PMWorkflowPath:
    """The typed PM workflow on its own: deterministic, no model, memory vault configured."""

    name = "pm"

    def describe(self) -> dict[str, Any]:
        return {"model": None, "prompt_set": None, "prompts": {},
                "note": "typed workflow only; model credentials are hidden from it"}

    def open(self, scenario_id: str, data_dir: str) -> Session:
        from kairo.agents.personal_manager.agent import PMConfig, run_pm

        session = _KairoSession(scenario_id, data_dir, vault_in_user_dir=True)
        config = PMConfig(user_id=session.user_id, provider="openai", model="unused", data_dir=data_dir,
                          vault_dir=session.vault_dir, session_id=session.thread_id)

        def reply_to(message: str) -> str:
            with _without_model_credentials():
                return run_pm(message, config)

        session.reply_to = reply_to
        return session


class OrchestratorPath:
    """The web-chat path, configured the way `/orchestrator/stream` configures it."""

    name = "orchestrator"

    def describe(self) -> dict[str, Any]:
        env = load_default_llm_from_env()
        prompts = prompt_versions.current()
        return {"provider": env.get("provider"), "model": env.get("model"),
                "pm_model": os.environ.get("PM_MODEL", env.get("model")),
                "prompt_set": prompt_set(prompts), "prompts": prompts,
                "note": "mirrors kairo/api/app.py orchestrator_stream, including no memory vault"}

    def open(self, scenario_id: str, data_dir: str) -> Session:
        from kairo.agents.orchestrator.agent import OrchestratorConfig, run_orchestrator

        env = load_default_llm_from_env()
        if not env:
            raise PathUnavailable("the orchestrator path needs model credentials "
                                  "(OPENAI_API_KEY, ANTHROPIC_API_KEY or an Ollama setting)")
        session = _KairoSession(scenario_id, data_dir, vault_in_user_dir=False)
        config = OrchestratorConfig(
            user_id=session.user_id, session_id=session.thread_id, data_dir=data_dir,
            provider=env["provider"], model=env["model"], api_key=env.get("api_key"), base_url=env.get("base_url"),
            pm_provider=env["provider"], pm_model=os.environ.get("PM_MODEL", env["model"]),
            pm_api_key=env.get("api_key"), pm_base_url=env.get("base_url"),
        )
        session.config = config
        session.reply_to = lambda message: run_orchestrator(message, config)
        return session


class GraphPath(OrchestratorPath):
    """The 2.0 agent graph, configured like web chat. What it has not taken over goes to the orchestrator."""

    name = "graph"

    def describe(self) -> dict[str, Any]:
        from kairo.agents.graph.nodes import bundle_version

        return {**super().describe(), "bundle": bundle_version(),
                "note": "KAIRO_AGENT=graph: tasks on the graph, everything else handed to the orchestrator"}

    def open(self, scenario_id: str, data_dir: str) -> Session:
        from kairo.agents.graph import run_graph

        session = super().open(scenario_id, data_dir)
        config = session.config
        session.reply_to = lambda message: run_graph(message, config)
        return session


PATHS: dict[str, Callable[[], Path]] = {
    PMWorkflowPath.name: PMWorkflowPath,
    OrchestratorPath.name: OrchestratorPath,
    GraphPath.name: GraphPath,
}
