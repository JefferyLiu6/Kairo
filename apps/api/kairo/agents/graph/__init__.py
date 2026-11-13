"""
The Kairo 2.0 agent graph.

    ingest ─► coordinate ─► gate ─► act ─► respond ─► persist
       │        │  │  ▲     ▲ │                 ▲
       │        │  │  └─────┼─┘ (one retry each)│
       │        │  └─► agent ─┘                 │
       │        └─► assistant (hand-off; answers and logs the turn itself)
       └─► approval ────────────────────────────┘

`coordinate` and `agent` are the only nodes that call a model, and a model only
proposes: one tool call, checked by `gate` before `act` runs it. The coordinator
calls an inline domain's tools itself and hands other messages to a domain's
own agent (kairo/domains). A domain that has not been moved onto the graph yet
is handed to the assistant that existed before (kairo/agents/orchestrator), so
the graph can take over one domain at a time.

Enable it for web chat with KAIRO_AGENT=graph.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import AsyncIterator

from langgraph.graph import END, START, StateGraph

from kairo.agents.graph import nodes
from kairo.agents.graph.state import GraphState
from kairo.agents.orchestrator.agent import OrchestratorConfig
from kairo.core.observability.telemetry import span
from kairo.core.prompt import collecting_prompts


def _next(state: GraphState) -> str:
    return state["route"]


def build_graph():
    graph = StateGraph(GraphState)
    for name in ("ingest", "approval", "coordinate", "agent", "gate", "act", "assistant", "respond", "persist"):
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, "ingest")
    graph.add_conditional_edges("ingest", _next, ["approval", "coordinate", "respond"])
    graph.add_conditional_edges("coordinate", _next, ["gate", "agent", "assistant", "respond", "approval"])
    graph.add_conditional_edges("agent", _next, ["gate", "agent", "assistant", "respond"])
    graph.add_conditional_edges("gate", _next, ["coordinate", "agent", "act", "respond", "assistant"])
    graph.add_edge("approval", "respond")
    graph.add_edge("act", "respond")
    graph.add_edge("respond", "persist")
    graph.add_edge("persist", END)
    graph.add_edge("assistant", END)
    return graph.compile()


GRAPH = build_graph()


def graph_enabled() -> bool:
    """Whether web chat should run on the graph (KAIRO_AGENT=graph) or on the previous assistant."""
    return os.environ.get("KAIRO_AGENT", "").strip().lower() == "graph"


async def astream_graph(message: str, config: OrchestratorConfig) -> AsyncIterator[tuple[str, str]]:
    """One turn. Yields the same ("progress" | "token" | "done", text) events as the previous assistant."""
    with span("turn"), collecting_prompts():
        yield ("progress", "Thinking…")
        final = await GRAPH.ainvoke({"message": message},
                                    config={"configurable": {"kairo": config, "started": time.monotonic()}})
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
