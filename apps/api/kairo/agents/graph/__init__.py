"""
The Kairo 2.0 agent graph.

    ingest ─► coordinate ─► gate ─► act ─► respond ─► voice ─► persist
       │        │  │  ▲     ▲ │      │          ▲
       │        │  │  └─────┼─┘ (one retry each)│
       │        │  └─► agent ─┘      │          │
       │        └─► step ◄───────────┘ (a plan: one part at a time)
       └─► approval ────────────────────────────┘

`coordinate` and `agent` are the only nodes whose model decides anything, and a
model only proposes: one tool call, checked by `gate` before `act` runs it. The
coordinator calls an inline domain's tools itself and hands other messages to a
domain's own agent (kairo/domains). One agent domain is the catch-all, so every
message is answered on the graph.

A message that asks for several separate things is planned: the coordinator
calls `plan`, and `step` does one part at a time, each through the same
coordinate or agent, gate and act as a message of its own. What needs approval
is held and asked for once, after the last part.

Before a turn that was not split replies, `sweep` has the small model compare
the message with what was done. A request that was left out, in the user's own
words, is then done as a part of its own; anything less certain is ignored.

`respond` builds the reply from what was read back from storage. `voice` then has
a small model reword it so it sounds like an assistant, and sends the rewording
only if it says what the draft says (kairo/agents/graph/voice.py). The rewording
is what the user reads; the models keep reading the draft. KAIRO_VOICE=off sends
the drafts as written.

Web chat runs on it by default. KAIRO_AGENT=legacy switches back to the previous assistant.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import replace
from typing import AsyncIterator

from langgraph.graph import END, START, StateGraph

from kairo.agents.graph import nodes
from kairo.agents.graph.state import GraphState
from kairo.agents.orchestrator.agent import OrchestratorConfig
from kairo.core.domain.session import normalize_pm_session_id
from kairo.core.observability.telemetry import span
from kairo.core.prompt import collecting_prompts
from kairo.storage.control_store import pm_db_path


def _next(state: GraphState) -> str:
    """Where the node that just ran sends the turn. While a plan has parts left, a part that is
    finished goes on to the next one instead of to the reply."""
    route = state["route"]
    if route != "respond":
        return route
    if state.get("plan") is not None and not state.get("plan_done"):
        return "step"
    return "sweep" if nodes.sweepable(state) else "respond"


def _after_act(state: GraphState) -> str:
    return _next({**state, "route": "respond"})


def build_graph():
    graph = StateGraph(GraphState)
    for name in ("ingest", "approval", "coordinate", "step", "agent", "gate", "act", "sweep", "respond", "voice", "persist"):
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, "ingest")
    graph.add_conditional_edges("ingest", _next, ["approval", "coordinate", "respond"])
    graph.add_conditional_edges("coordinate", _next, ["coordinate", "gate", "agent", "respond", "approval", "step", "sweep"])
    graph.add_conditional_edges("step", _next, ["coordinate", "agent", "respond"])
    graph.add_conditional_edges("agent", _next, ["gate", "agent", "respond", "step", "sweep"])
    graph.add_conditional_edges("gate", _next, ["coordinate", "agent", "act", "respond", "step", "sweep"])
    graph.add_edge("approval", "respond")
    graph.add_conditional_edges("act", _after_act, ["respond", "step", "sweep"])
    graph.add_conditional_edges("sweep", _next, ["step", "respond"])
    graph.add_edge("respond", "voice")
    graph.add_edge("voice", "persist")
    graph.add_edge("persist", END)
    return graph.compile()


GRAPH = build_graph()


def graph_enabled() -> bool:
    """Whether web chat runs on the graph. It does unless KAIRO_AGENT=legacy asks for the previous assistant."""
    return os.environ.get("KAIRO_AGENT", "").strip().lower() != "legacy"


async def astream_graph(message: str, config: OrchestratorConfig) -> AsyncIterator[tuple[str, str]]:
    """One turn. Yields the same ("progress" | "token" | "done", text) events as the previous assistant."""
    if not config.vault_dir:
        # Web chat configures no memory vault. What the user asks Kairo to remember is kept in
        # their own data directory, next to everything else stored for them.
        user = config.user_id or normalize_pm_session_id(config.session_id)
        config = replace(config, vault_dir=os.path.dirname(pm_db_path(user, config.data_dir)))
    with span("turn"), collecting_prompts():
        yield ("progress", "Thinking…")
        final = await GRAPH.ainvoke({"message": message},
                                    config={"configurable": {"kairo": config, "started": time.monotonic()},
                                            # A plan of several parts passes through more nodes than the default allows.
                                            "recursion_limit": 80})
        reply = final["reply"]
        yield ("token", reply)
        yield ("done", reply)


def run_graph(message: str, config: OrchestratorConfig) -> str:
    """Synchronous wrapper for tests, scripts and evals."""
    async def collect() -> str:
        reply = ""
        async for kind, value in astream_graph(message, config):
            if kind == "done":
                reply = value
        return reply
    return asyncio.run(collect())
