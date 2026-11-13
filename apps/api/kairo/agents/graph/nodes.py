"""
The graph's nodes.

Only `coordinate` calls a model, and the model can only propose: it picks one
tool and fills in its arguments. `gate` checks the proposal, `act` runs it
through the same approval policy and executors as before and reads the stores
back, and `respond` builds the reply from what was read back.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError

from kairo.agents.graph.prompts import COORDINATOR
from kairo.agents.graph.state import GraphState
from kairo.agents.orchestrator.agent import OrchestratorConfig, astream_orchestrator
from kairo.agents.orchestrator.memory import get_working_memory
from kairo.agents.personal_manager.application.approval_flow import approve_from_chat, reject_from_chat, stored_read_back, submit_action
from kairo.agents.personal_manager.application.pending_reply import approval_verb, load_open_question, route_message
from kairo.agents.personal_manager.extractors.intent import _looks_like_injection
from kairo.core.domain.pending import FreeformPending
from kairo.core.domain.session import normalize_pm_session_id
from kairo.core.domain.types import PMIntent
from kairo.core.llm_env import build_llm
from kairo.core.observability.telemetry import emit
from kairo.core.prompt import Prompt, prompts_used, record_prompt
from kairo.domains.catalog import DOMAINS
from kairo.domains.checks import calendar_hint
from kairo.domains.registry import Answer, NotThisDomain, ToolContext
from kairo.storage.decision_log import log_orchestrator_turn
from kairo.storage.working_memory import clear_active_working_memory, load_active_working_memory, save_working_memory
from kairo.tools.receipts import collecting, read_back_reply
from kairo.utils.clock import local_now

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2          # the coordinator gets one retry after the gate sends a call back
HISTORY_TURNS = 6
_GRAPH_QUESTION = "graph"  # working-memory source for a question this graph asked
_REFUSAL = "I can't take instructions embedded inside a user message. Please rephrase in your own words."


# ── What the coordinator can call ─────────────────────────────────────────────

def _function(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": name, "description": description, "strict": True,
        "parameters": {"type": "object", "properties": properties, "required": list(properties),
                       "additionalProperties": False}}}


def coordinator_tools() -> list[dict[str, Any]]:
    """Inline domains' tools, then the two the coordinator always has: hand_off and ask_user."""
    domains = [domain.name for domain in DOMAINS.agent_domains()]
    return [tool.schema() for tool in DOMAINS.inline_tools()] + [
        _function("hand_off", "Pass the message to the part of Kairo that handles it.",
                  {"domain": {"type": "string", "enum": domains}}),
        _function("ask_user", "Ask the user one question when you cannot tell what they want done.",
                  {"question": {"type": "string"}}),
    ]


def bundle_version() -> str:
    """One version for everything the coordinator's model is told: its prompt and every tool definition."""
    shown = COORDINATOR.version + json.dumps(coordinator_tools(), sort_keys=True)
    return hashlib.sha256(shown.encode("utf-8")).hexdigest()[:8]


def ask_model(config: OrchestratorConfig, system: Prompt, turns: list[tuple[str, str]],
              tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One model call. Returns the tool calls as {"name", "args"}; the model must call exactly one."""
    llm = build_llm(config.provider, config.model, config.api_key, config.base_url)
    bound = llm.bind_tools(tools, tool_choice="any", parallel_tool_calls=False)
    record_prompt(system)
    messages: list[Any] = [SystemMessage(content=str(system))]
    messages += [(HumanMessage if role == "user" else AIMessage)(content=text) for role, text in turns]
    response = bound.invoke(messages)
    usage = getattr(response, "usage_metadata", None) or {}
    emit("llm_usage", input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
         prompt=system.id, model=config.model)
    return [{"name": call["name"], "args": call["args"]} for call in response.tool_calls]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _config(config: RunnableConfig) -> OrchestratorConfig:
    return config["configurable"]["kairo"]


def _ids(cfg: OrchestratorConfig) -> tuple[str, str]:
    thread = normalize_pm_session_id(cfg.session_id)
    return cfg.user_id or thread, thread


def _context(state: GraphState, cfg: OrchestratorConfig) -> ToolContext:
    user, thread = _ids(cfg)
    return ToolContext(user_id=user, thread_id=thread, data_dir=cfg.data_dir, now=state["now"],
                       message=state["message"], vault_dir=cfg.vault_dir)


def _remember_question(cfg: OrchestratorConfig, question: str, topic: str = "") -> None:
    """Keep the question until the user's next message, so it can be shown to the coordinator then."""
    user, thread = _ids(cfg)
    pending = FreeformPending(intent=PMIntent.UNKNOWN, entities={"query": topic or question}, missing=["answer"])
    save_working_memory(thread, cfg.data_dir, user_id=user, mode="awaiting_freeform", source=_GRAPH_QUESTION,
                        expected_reply="freeform_answer", payload=pending.model_dump(mode="json"), summary=question)


# ── Nodes ─────────────────────────────────────────────────────────────────────

async def ingest(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    cfg = _config(config)
    user, thread = _ids(cfg)
    message = state["message"]
    update: dict[str, Any] = {"now": local_now(), "attempts": 0, "rejection": None, "open_question": None}
    if _looks_like_injection(message):
        return {**update, "notes": [_REFUSAL], "route": "respond", "history": []}

    # A question this graph asked is asked once: take it off the table and show it to the coordinator.
    # A question the previous assistant asked is left for it; the coordinator is told one is open.
    record = load_active_working_memory(thread, cfg.data_dir, user_id=user)
    theirs = None
    if record is not None and record.source == _GRAPH_QUESTION:
        clear_active_working_memory(thread, cfg.data_dir, user_id=user, status="resolved")
        update["open_question"] = f"You just asked the user: \"{record.summary}\" Their message may be the answer."
    else:
        theirs = load_open_question(thread, cfg.data_dir, user_id=user)
        if theirs is not None:
            update["open_question"] = ("The assistant is waiting for an answer to a question it asked (see the last "
                                       "assistant message). If this message answers it, call hand_off.")

    turns = get_working_memory(user, thread, cfg.data_dir).turns[-HISTORY_TURNS:]
    update["history"] = [(turn["role"], turn["content"]) for turn in turns]
    update["route"] = "approval" if route_message(message, theirs) == "approval_command" else "coordinate"
    return update


async def approval(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """approve / reject: no model. The approval service owns execute-once and thread scope."""
    cfg = _config(config)
    command = state["message"].strip().lower()
    handler = approve_from_chat if approval_verb(state["message"]) == "approve" else reject_from_chat
    try:
        with collecting() as made:
            reply = handler(command, cfg)
        if read_back_reply(made.receipts):
            return {"receipts": made.receipts}   # respond describes the change from the stored record
        # Nothing ran now (a reject, or an approval sent again): say what the approval service says.
        return {"notes": [stored_read_back(command, cfg) or reply]}
    except Exception:
        logger.exception("approval command failed")
        return {"notes": ["I couldn't verify the approval result. Check your calendar or task list before trying again."]}


async def coordinate(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    cfg = _config(config)
    now = state["now"]
    system = COORDINATOR.render(
        clock=f"Current date and time: {now:%A} {now:%Y-%m-%d %H:%M}.\nThe next two weeks:\n{calendar_hint(now)}",
        domains=DOMAINS.routing_lines(mode="agent"),
        open_question=("\n" + state["open_question"]) if state.get("open_question") else "",
    )
    turns = [*state.get("history", []), ("user", state["message"])]
    if state.get("rejection"):
        turns.append(("user", f"(Your last tool call was not accepted: {state['rejection']}. Call the tool again "
                              "with that fixed, call hand_off if this is not for that tool, or call ask_user if "
                              "the message does not say.)"))
    try:
        calls = ask_model(cfg, system, turns, coordinator_tools())
    except Exception:
        logger.exception("coordinator model call failed; handing the turn to the assistant")
        calls = []
    call = calls[0] if calls else None
    update = {"call": call, "attempts": state.get("attempts", 0) + 1}
    name = call["name"] if call else "hand_off"

    if name == "ask_user" and str(call["args"].get("question", "")).strip():
        question = str(call["args"]["question"]).strip()
        _remember_question(cfg, question)
        return {**update, "notes": [question], "route": "respond"}
    if name in {tool.name for tool in DOMAINS.inline_tools()}:
        return {**update, "route": "gate"}
    # hand_off, no call, or a call the graph does not know: the assistant that was there before takes it.
    return {**update, "route": "assistant"}


async def gate(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Check the proposal before anything runs. A proposal that fails is sent back once, then asked about."""
    cfg = _config(config)
    call = state["call"]
    spec, ctx = DOMAINS.tool(call["name"]), _context(state, cfg)
    problem, user_facing = None, True
    try:
        args = spec.args.model_validate(call["args"])
    except ValidationError as exc:
        error = exc.errors()[0]
        problem, user_facing = f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}", False
    else:
        problem = spec.check(args, ctx) if spec.check else None
    if problem:
        if state.get("attempts", 0) < MAX_ATTEMPTS:
            return {"rejection": str(problem), "route": "coordinate"}
        if isinstance(problem, NotThisDomain):
            return {"route": "assistant"}   # the model kept it; the check says it belongs elsewhere
        question = (f"I couldn't work that out safely ({problem}). Could you say it again with the date?"
                    if user_facing else "I couldn't work out what to do with that. Could you say it another way?")
        _remember_question(cfg, question)
        return {"notes": [question], "route": "respond"}

    if spec.kind == "read":
        return {"notes": [spec.read(args, ctx)], "route": "respond"}
    proposal = spec.propose(args, ctx)
    if isinstance(proposal, Answer):
        if proposal.elsewhere and DOMAINS.agent_domains():
            return {"route": "assistant"}   # not in this domain's store: someone with the whole picture answers
        if proposal.asks:
            _remember_question(cfg, proposal.text, proposal.topic)
        return {"notes": [proposal.text], "route": "respond"}
    return {"actions": proposal, "route": "act"}


async def act(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Run each proposed command through the approval policy; read the stores back for whatever ran."""
    cfg = _config(config)
    user, thread = _ids(cfg)
    approvals: list[str] = []
    results: list[str] = []
    with collecting() as made:
        for action in state.get("actions", []):
            _, result = submit_action(action, cfg.pm_config(), user, thread_id=thread, intent=state["call"]["name"])
            (approvals if result.get("pending") else results).append(str(result["message"]))
    return {"receipts": made.receipts, "approvals": approvals, "results": results}


async def assistant(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Hand the whole turn to the assistant that existed before the graph. It answers, remembers and logs it."""
    reply = ""
    async for kind, value in astream_orchestrator(state["message"], _config(config)):
        if kind == "done":
            reply = value
    return {"reply": reply, "handled_by_assistant": True}


async def respond(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """The reply: what was read back from storage, then approval prompts, then anything else to say."""
    lines: list[str] = []
    receipts = state.get("receipts", [])
    if receipts or state.get("results"):
        # A write the stores could not confirm falls back to the executor's own message.
        lines.append(read_back_reply(receipts) or "\n".join(state.get("results", [])))
    lines += state.get("approvals", [])
    lines += state.get("notes", [])
    return {"reply": "\n".join(line for line in lines if line) or "I wasn't able to do that."}


async def persist(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    cfg = _config(config)
    user, thread = _ids(cfg)
    memory = get_working_memory(user, thread, cfg.data_dir)
    receipts = state.get("receipts", [])
    if receipts:
        memory.invalidate_pm_cache()
    memory.add_turn("user", state["message"])
    memory.add_turn("assistant", state["reply"])
    call = state.get("call") or {}
    tool = call.get("name") or ("approval" if state.get("route") == "approval" else "none")
    log_orchestrator_turn(
        thread, cfg.data_dir, user_id=user, message=state["message"], route="GRAPH",
        route_reason=f"tool={tool}; attempts={state.get('attempts', 0)}", intent=tool, confidence=1.0,
        pm_prompt="", is_write=bool(receipts or state.get("approvals")), harness_verdict="n/a", harness_reason="",
        retry_count=max(0, state.get("attempts", 0) - 1), reply=state["reply"],
        duration_ms=int((time.monotonic() - config["configurable"]["started"]) * 1000),
        versions={
            "prompts": prompts_used(),
            "bundles": {"coordinator": bundle_version()},
            "models": {"orchestrator": cfg.model, "pm": cfg.pm_model},
            "receipts": [receipt.status for receipt in receipts],
        },
    )
    return {}
