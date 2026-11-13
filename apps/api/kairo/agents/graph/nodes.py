"""
The graph's nodes.

`coordinate` and `agent` are the only nodes whose model decides anything, and a
model can only propose: it picks one tool and fills in its arguments. `gate`
checks the proposal and sends a bad one back to whoever made it, `act` runs it
through the same approval policy and executors as before and reads the stores
back, and `respond` builds the reply from what was read back. `voice` has a
small model reword that reply and keeps the rewording only if it says the same
thing (kairo/agents/graph/voice.py).

A question is asked once. It is offered to the user's next message; if that
message is about something else the question is parked for one more message
with a reminder, and then it is dropped. It never captures a message.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError

from kairo.agents.graph.prompts import COORDINATOR, VOICE
from kairo.agents.graph.state import GraphState
from kairo.agents.graph.voice import VOICE_TIMEOUT, changed_the_facts, sent_as_written, voice_enabled, voice_input
from kairo.agents.orchestrator.agent import OrchestratorConfig
from kairo.agents.orchestrator.memory import get_working_memory
from kairo.agents.personal_manager.application.approval_flow import approve_from_chat, reject_from_chat, stored_read_back, submit_action
from kairo.agents.personal_manager.application.pending_reply import approval_verb, route_message
from kairo.agents.personal_manager.extractors.intent import _looks_like_injection
from kairo.core.domain.pending import GraphQuestion
from kairo.core.domain.session import normalize_pm_session_id
from kairo.core.llm_env import build_llm, message_text
from kairo.core.observability.telemetry import emit
from kairo.core.prompt import Prompt, prompts_used, record_prompt
from kairo.domains.catalog import DOMAINS
from kairo.domains.checks import calendar_hint
from kairo.domains.registry import Answer, DomainSpec, NotThisDomain, Proposed, ToolContext
from kairo.storage.control_store import append_conversation_turn, list_approval_requests
from kairo.storage.decision_log import log_orchestrator_turn
from kairo.storage.working_memory import clear_active_working_memory, load_active_working_memory, save_working_memory
from kairo.tools.receipts import collecting, read_back_reply
from kairo.utils.clock import local_now

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2          # each model gets one retry after its call is sent back
MAX_AGENT_CALLS = 3       # an agent that looks something up may be called this often in one turn
MAX_ROUNDS = 2            # questions in a row about one request before the user is left to restate it
HISTORY_TURNS = 6
COORDINATOR_NAME = "coordinator"
_GRAPH_QUESTION = "graph"  # working-memory source for a question this graph asked
_REFUSAL = "I can't take instructions embedded inside a user message. Please rephrase in your own words."
_NOTHING_CHANGED = "I haven't changed anything. Could you say what you'd like me to do, with the day and time?"
_LOST = "I couldn't finish that. Could you say it another way?"
_RETRY = ("(Your last tool call was not accepted: {reason}. Call a tool again with that fixed, {other}"
          "or call ask_user if the message does not say.)")

# An answer in words may describe the calendar; only a receipt may say that something changed.
_CLAIMS_A_CHANGE = re.compile(
    r"\b(i['’]ve|i have|i['’]ll|i will|i['’]m|i am|let me)\s+(now\s+|just\s+|go ahead and\s+)?"
    r"(add|schedul|creat|book|mov|updat|chang|cancel|remov|delet|set|sav|block|reschedul)\w*"
    r"|\b(has|have) been (added|scheduled|created|booked|moved|updated|changed|cancell?ed|removed|deleted|saved|rescheduled)\b"
    r"|^(added|scheduled|booked|moved|updated|cancell?ed|removed|deleted|saved|done)\b", re.I)


# ── What each model can call ──────────────────────────────────────────────────

def _function(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": name, "description": description, "strict": True,
        "parameters": {"type": "object", "properties": properties, "required": list(properties),
                       "additionalProperties": False}}}


_ASK_USER = _function("ask_user", "Ask the user one question when you cannot tell what they want done, or the "
                                  "one thing that is missing.", {"question": {"type": "string"}})
_DECIDE_APPROVAL = _function(
    "decide_approval", "Record the user's yes or no to the approval they were just asked for.",
    {"decision": {"type": "string", "enum": ["approve", "reject"]}})
_ANSWER = _function("answer", "Answer in words, from what you were shown. Never say that something was added, "
                              "changed or removed.", {"text": {"type": "string"}})
_HAND_BACK = _function("hand_back", "Give the message back when it is not about your domain.", {})


def coordinator_tools(*, approval_offered: bool = False) -> list[dict[str, Any]]:
    """Inline domains' tools, then hand_off and ask_user; decide_approval only while an approval is on offer."""
    domains = [domain.name for domain in DOMAINS.hand_off_domains()]
    tools = [tool.schema() for tool in DOMAINS.inline_tools()] + [
        _function("hand_off", "Pass the message to the part of Kairo that handles it.",
                  {"domain": {"type": "string", "enum": domains}}),
        _ASK_USER,
    ]
    return tools + ([_DECIDE_APPROVAL] if approval_offered else [])


def agent_tools(domain: DomainSpec) -> list[dict[str, Any]]:
    """A domain agent's own tools, then the ones every agent has: answer, ask_user and hand_back.
    The catch-all agent has nobody to hand back to."""
    return [tool.schema() for tool in domain.tools] + [_ANSWER, _ASK_USER] + ([] if domain.catch_all else [_HAND_BACK])


def _hash(*shown: Any) -> str:
    return hashlib.sha256(json.dumps(shown, sort_keys=True).encode("utf-8")).hexdigest()[:8]


def bundle_versions() -> dict[str, str]:
    """A version for everything each model is told: its prompt and every tool definition it is shown."""
    versions = {COORDINATOR_NAME: _hash(COORDINATOR.version, [d.guidance.version for d in DOMAINS.inline_domains()],
                                        coordinator_tools(approval_offered=True))}
    for domain in DOMAINS.agent_domains():
        versions[domain.name] = _hash(domain.bundle(), agent_tools(domain)[len(domain.tools):])
    return versions


def bundle_version() -> str:
    """One version for the whole graph, for comparing eval runs."""
    return _hash(bundle_versions())


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


def reword(config: OrchestratorConfig, system: Prompt, shown: str) -> str:
    """The voice pass's one model call. Returns the model's text.

    It runs on the small model (PM_MODEL), on a short timeout and with no retry: the reply
    is already written, so a rewording that is slow to arrive is not waited for.
    """
    llm = build_llm(config.pm_provider, config.pm_model, config.pm_api_key, config.pm_base_url,
                    timeout=VOICE_TIMEOUT, max_retries=0)
    record_prompt(system)
    response = llm.invoke([SystemMessage(content=str(system)), HumanMessage(content=shown)])
    usage = getattr(response, "usage_metadata", None) or {}
    emit("llm_usage", input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
         prompt=system.id, model=config.pm_model)
    return re.sub(r"[ \t]+\n", "\n", message_text(response)).strip()      # no trailing spaces left on a line


# ── Helpers ───────────────────────────────────────────────────────────────────

def _config(config: RunnableConfig) -> OrchestratorConfig:
    return config["configurable"]["kairo"]


def _ids(cfg: OrchestratorConfig) -> tuple[str, str]:
    thread = normalize_pm_session_id(cfg.session_id)
    return cfg.user_id or thread, thread


def _context(state: GraphState, cfg: OrchestratorConfig, domain: str) -> ToolContext:
    """The turn as a tool sees it. When `domain` is answering its own question, the request it was about comes too."""
    user, thread = _ids(cfg)
    question = state.get("question")
    request = question["request"] if question and question["domain"] in (domain, COORDINATOR_NAME) else ""
    return ToolContext(user_id=user, thread_id=thread, data_dir=cfg.data_dir, now=state["now"],
                       message=state["message"], vault_dir=cfg.vault_dir, request=request)


def _clock(state: GraphState) -> str:
    now = state["now"]
    return f"Current date and time: {now:%A} {now:%Y-%m-%d %H:%M}.\nThe next two weeks:\n{calendar_hint(now)}"


def _turns(state: GraphState, *, other: str = "") -> list[tuple[str, str]]:
    turns = [*state.get("history", []), ("user", state["message"])]
    turns += [("user", finding) for finding in state.get("findings", [])]
    if state.get("rejection"):
        turns.append(("user", _RETRY.format(reason=state["rejection"], other=other)))
    return turns


def _first_call(cfg: OrchestratorConfig, system: Prompt, turns: list[tuple[str, str]],
                tools: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    try:
        calls = ask_model(cfg, system, turns, tools)
    except Exception:
        logger.exception("model call failed (%s); the turn falls back to the assistant", system.name)
        return None
    return calls[0] if calls else None


# ── Questions ─────────────────────────────────────────────────────────────────

def _save_question(cfg: OrchestratorConfig, question: GraphQuestion) -> None:
    user, thread = _ids(cfg)
    save_working_memory(thread, cfg.data_dir, user_id=user, mode="awaiting_freeform", source=_GRAPH_QUESTION,
                        expected_reply="freeform_answer", payload=question.model_dump(mode="json"),
                        summary=question.prompt)


def _ask(state: GraphState, cfg: OrchestratorConfig, domain: str, prompt: str, topic: str = "",
         *, because: str = "") -> dict[str, Any]:
    """Ask the user `prompt` on behalf of `domain` and keep it for their next message. `because` is
    said before it this once and is not kept.

    A question that follows up on this domain's own open question belongs to the same request.
    After MAX_ROUNDS in a row it is still asked but no longer kept, so a request cannot turn
    into an endless exchange.
    """
    said = f"{because} {prompt}".strip()
    if "?" not in prompt and "？" not in prompt:
        # A statement sent as a question (a model sometimes refuses this way). It is said; nothing is open.
        return {"notes": [said], "handler": domain, "route": "respond"}
    previous = state.get("question")
    follows_up = bool(previous) and previous["domain"] == domain
    rounds = previous["round"] + 1 if follows_up else 1
    if rounds <= MAX_ROUNDS:
        _save_question(cfg, GraphQuestion(domain=domain, prompt=prompt, topic=(topic or prompt).casefold(),
                                          request=previous["request"] if follows_up else state["message"],
                                          round=rounds))
    return {"notes": [said], "asked": True, "handler": domain, "route": "respond"}


def _park(state: GraphState, cfg: OrchestratorConfig) -> Optional[str]:
    """If the question that was open went unanswered this turn, park it once. Returns the reminder line."""
    question = state.get("question")
    if not question or state.get("asked") or question["parked"]:
        return None      # nothing was open, a new question took its place, or it has had its second chance
    handler = state.get("handler")
    took_up = handler == question["domain"] or (
        question["domain"] == COORDINATOR_NAME and handler not in (None, "approval"))
    if took_up:
        return None
    _save_question(cfg, GraphQuestion.model_validate({**question, "parked": True}))
    return f"Still open: {question['prompt']}"


def _coordinator_notes(state: GraphState) -> str:
    """What is waiting on the user, as the coordinator is told it."""
    notes = []
    question = state.get("question")
    if question and DOMAINS.domain(question["domain"]) in DOMAINS.agent_domains():
        about = f" (about their request: \"{question['request']}\")" if question["request"] else ""
        notes.append(
            f"The {question['domain']} agent {'asked earlier' if question['parked'] else 'just asked'} the user: "
            f"\"{question['prompt']}\"{about}. If this message answers that, hand off to {question['domain']}. "
            "If it is about something else, handle it as a new request.")
    elif question:
        notes.append(f"You {'asked the user earlier' if question['parked'] else 'just asked the user'}: "
                     f"\"{question['prompt']}\" Their message may be the answer.")
    if state.get("offer"):
        notes.append(f"The user was just asked to approve this: \"{state['offer']['summary']}\". If this message "
                     "says yes or no to that, call decide_approval. Anything else is a new request.")
    return ("\n" + "\n".join(notes)) if notes else ""


def _approval_on_offer(cfg: OrchestratorConfig, history: list[tuple[str, str]]) -> Optional[dict[str, str]]:
    """The approval a yes or no refers to: the one whose prompt is the last thing Kairo said.

    Only while that prompt is the last thing said, and only if it asked for exactly one approval.
    An older approval the user never answered stays pending and does not get in the way.
    """
    if not history or history[-1][0] != "assistant":
        return None
    user, thread = _ids(cfg)
    asked = [a for a in list_approval_requests(user, cfg.data_dir, status="pending", limit=20)
             if a.payload.get("_thread_id") == thread and f"[{a.id}]" in history[-1][1]]
    return {"id": asked[0].id, "summary": asked[0].summary} if len(asked) == 1 else None


# ── Nodes ─────────────────────────────────────────────────────────────────────

async def ingest(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    cfg = _config(config)
    user, thread = _ids(cfg)
    message = state["message"]
    update: dict[str, Any] = {"now": local_now(), "attempts": 0, "agent_attempts": 0, "rejection": None,
                              "question": None, "offer": None, "history": []}
    if _looks_like_injection(message):
        return {**update, "notes": [_REFUSAL], "route": "respond"}   # refused before anything else is touched

    # A question this graph asked is taken off the table now and decided on at the end of the turn.
    # One left open by the assistant that ran before the graph is closed: nobody here can resume it,
    # and it must not capture a later message.
    record = load_active_working_memory(thread, cfg.data_dir, user_id=user)
    if record is not None:
        mine = record.source == _GRAPH_QUESTION
        clear_active_working_memory(thread, cfg.data_dir, user_id=user, status="resolved" if mine else "replaced")
        if mine:
            try:
                update["question"] = GraphQuestion.model_validate(record.payload).model_dump(mode="json")
            except ValidationError:
                logger.warning("dropped an unreadable graph question in thread %s", thread)

    turns = get_working_memory(user, thread, cfg.data_dir).turns[-HISTORY_TURNS:]
    update["history"] = [(turn["role"], turn["content"]) for turn in turns]
    update["offer"] = _approval_on_offer(cfg, update["history"])
    update["route"] = "coordinate"
    if route_message(message, None) == "approval_command":
        update["route"] = "approval"
        if update["offer"] and " " not in message.strip():
            # A bare approve or reject right after a prompt answers that prompt.
            update["decision"] = f"{approval_verb(message)} {update['offer']['id']}"
    return update


async def approval(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """approve / reject, typed as a command or decided in plain words. The approval service owns
    execute-once and thread scope."""
    cfg = _config(config)
    command = state.get("decision") or state["message"].strip().lower()
    handler = approve_from_chat if approval_verb(command) == "approve" else reject_from_chat
    try:
        with collecting() as made:
            reply = handler(command, cfg)
        if read_back_reply(made.receipts):
            return {"receipts": made.receipts, "handler": "approval"}   # respond describes the change from storage
        # Nothing ran now (a reject, or an approval sent again): say what the approval service says.
        return {"notes": [stored_read_back(command, cfg) or reply], "handler": "approval"}
    except Exception:
        logger.exception("approval command failed")
        return {"notes": ["I couldn't verify the approval result. Check your calendar or task list before trying again."],
                "handler": "approval"}


async def coordinate(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    cfg = _config(config)
    offer = state.get("offer")
    areas = []
    for domain in DOMAINS.inline_domains():
        record_prompt(domain.guidance)
        shown = domain.context(_context(state, cfg, domain.name)) if domain.context else ""
        areas.append("\n".join(part for part in (f"\n{domain.name}: {domain.summary}", str(domain.guidance), shown) if part))
    system = COORDINATOR.render(clock=_clock(state), inline="\n".join(areas), domains=DOMAINS.routing_lines(),
                                open_question=_coordinator_notes(state))
    call = _first_call(cfg, system, _turns(state, other="call hand_off if this is not for that tool, "),
                       coordinator_tools(approval_offered=bool(offer)))
    update = {"call": call, "attempts": state.get("attempts", 0) + 1, "rejection": None,
              "proposer": COORDINATOR_NAME}
    name, args = (call["name"], call.get("args") or {}) if call else ("", {})

    if name == "ask_user" and str(args.get("question", "")).strip():
        return {**update, **_ask(state, cfg, COORDINATOR_NAME, str(args["question"]).strip())}
    if name == "decide_approval" and offer and args.get("decision") in ("approve", "reject"):
        return {**update, "decision": f"{args['decision']} {offer['id']}", "route": "approval"}
    if name in {tool.name for tool in DOMAINS.inline_tools()}:
        return {**update, "handler": DOMAINS.domain_of(name).name, "route": "gate"}
    target = DOMAINS.domain(str(args.get("domain", ""))) if name == "hand_off" else None
    if target is not None and target.mode == "agent":
        return {**update, "domain": target.name, "route": "agent"}
    # No call, or a call the graph did not offer: the catch-all agent takes the message.
    return {**update, **_to_catch_all()}


async def agent(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """A domain's own agent: one model call with that domain's prompt, tools and view of its store."""
    cfg = _config(config)
    spec = DOMAINS.domain(state["domain"])
    question = state.get("question")
    note = ""
    if question and question["domain"] == spec.name:
        note = (f"\nYou {'asked the user earlier' if question['parked'] else 'just asked the user'}: "
                f"\"{question['prompt']}\" about their request: \"{question['request']}\". If this message answers "
                "that, finish the request with it; what they said in the request still holds. If it is about "
                "something else, treat it as a new request.")
    system = spec.prompt.render(clock=_clock(state), open_question=note,
                                context=spec.context(_context(state, cfg, spec.name)) if spec.context else "")
    call = _first_call(cfg, system, _turns(state), agent_tools(spec))
    attempts = state.get("agent_attempts", 0) + 1
    update = {"call": call, "agent_attempts": attempts, "rejection": None, "proposer": spec.name,
              "handler": spec.name}
    name, args = (call["name"], call.get("args") or {}) if call else ("", {})

    if name == "ask_user" and str(args.get("question", "")).strip():
        return {**update, **_ask(state, cfg, spec.name, str(args["question"]).strip())}
    if name == "answer" and str(args.get("text", "")).strip():
        text = str(args["text"]).strip()
        if not _CLAIMS_A_CHANGE.search(text):
            return {**update, "notes": [text], "route": "respond"}
        if attempts < MAX_ATTEMPTS + (1 if state.get("findings") else 0):
            return {**update, "route": "agent",
                    "rejection": "an answer cannot say that something was or will be added, changed or removed; "
                                 "to change anything, call the tool that proposes it"}
        return {**update, "notes": [_NOTHING_CHANGED], "route": "respond"}
    if name in {tool.name for tool in spec.tools}:
        return {**update, "route": "gate"}
    # hand_back, no call, or a call this agent was not offered: the catch-all agent takes the message,
    # unless this is the catch-all, in which case there is nothing left but to say so.
    if spec.catch_all:
        return {**update, "notes": [_LOST], "route": "respond"}
    return {**update, **_to_catch_all()}


async def gate(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Check the proposal before anything runs. One that fails goes back to whoever made it, once; after
    that nothing is stored and the user is asked."""
    cfg = _config(config)
    call = state["call"]
    by_agent = state.get("proposer", COORDINATOR_NAME) != COORDINATOR_NAME
    attempts = state.get("agent_attempts" if by_agent else "attempts", 0)
    limit = MAX_ATTEMPTS + (1 if state.get("findings") else 0)   # an agent that looked something up gets one more call
    back = "agent" if by_agent else "coordinate"
    spec, owner = DOMAINS.tool(call["name"]), DOMAINS.domain_of(call["name"])
    ctx = _context(state, cfg, owner.name)
    problem, user_facing = None, True
    try:
        args = spec.args.model_validate(call["args"])
    except ValidationError as exc:
        error = exc.errors()[0]
        problem, user_facing = f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}", False
    else:
        problem = spec.check(args, ctx) if spec.check else None
    if problem:
        if attempts < limit:
            return {"rejection": str(problem), "route": back}
        if isinstance(problem, NotThisDomain):
            return _send_elsewhere(problem.to, owner.name)   # the model kept it; the check says where it belongs
        if not user_facing:
            return _ask(state, cfg, owner.name, "Could you say that another way?",
                        because="I couldn't work out what to do with that, so nothing was saved.")
        return _ask(state, cfg, owner.name, owner.restate.format(message=state["message"]),
                    because=f"Nothing was saved, because I couldn't settle the details ({problem}).")

    if spec.kind == "read":
        return {"notes": [spec.read(args, ctx)], "route": "respond"}
    if spec.kind == "lookup":
        # The result goes back to the agent that asked, which then answers. It cannot look things up forever.
        if not by_agent or attempts >= MAX_AGENT_CALLS:
            return {"notes": [_LOST], "route": "respond"}
        return {"findings": [f"(Result of {spec.name}: {spec.read(args, ctx)})"], "route": "agent"}
    proposal = spec.propose(args, ctx)
    if isinstance(proposal, Answer):
        if proposal.elsewhere:
            # Not in this domain's store. It may live in another one, so the model is told once;
            # if it insists, the assistant with the whole picture answers.
            if attempts < limit:
                return {"rejection": f"{proposal.text} If it is something else (a habit, a calendar event), use "
                                     "the tool or hand-off for that instead", "route": back}
            return _send_elsewhere("", owner.name)
        if proposal.asks:
            return _ask(state, cfg, owner.name, proposal.text, proposal.topic)
        return {"notes": [proposal.text], "route": "respond"}
    actions, notes = (proposal.actions, list(proposal.notes)) if isinstance(proposal, Proposed) else (proposal, [])
    return {"actions": actions, "notes": notes, "route": "act"}


def _send_elsewhere(to: str, sender: str) -> dict[str, Any]:
    """Route a request its domain turned away: to the named domain's agent if there is one, else the catch-all."""
    target = DOMAINS.domain(to)
    if target is not None and target.mode == "agent" and target.name != sender:
        return {"domain": target.name, "agent_attempts": 0, "rejection": None, "route": "agent"}
    return _to_catch_all()


def _to_catch_all() -> dict[str, Any]:
    """Send the message to the agent that takes whatever nobody else can place."""
    catch_all = DOMAINS.fallback()
    if catch_all is None:
        return {"notes": [_LOST], "route": "respond"}
    return {"domain": catch_all.name, "agent_attempts": 0, "rejection": None, "route": "agent"}


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


async def respond(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """The reply as the records give it: what was read back from storage, then approval prompts, then
    anything else to say."""
    lines: list[str] = []
    receipts = state.get("receipts", [])
    if receipts or state.get("results"):
        # A write the stores could not confirm falls back to the executor's own message.
        lines.append(read_back_reply(receipts) or "\n".join(state.get("results", [])))
    lines += state.get("approvals", [])
    lines += state.get("notes", [])
    lines.append(_park(state, _config(config)) or "")
    return {"draft": "\n".join(line for line in lines if line) or "I wasn't able to do that."}


async def voice(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Reword the draft so it sounds like an assistant. The draft is sent as written if it is one of
    the replies that are never reworded, if the model fails, or if the rewording changed what it says."""
    cfg = _config(config)
    draft, receipts = state["draft"], state.get("receipts", [])
    kept = "voice is off" if not voice_enabled() else sent_as_written(receipts, state.get("approvals", []))
    if kept is None and _looks_like_injection(state["message"]):
        kept = "refusal"           # a message that was refused is not shown to another model
    if kept is None:
        try:
            reworded = reword(cfg, VOICE, voice_input(state["message"], draft))
        except Exception:
            logger.exception("the voice model failed; the reply is sent as written")
            kept = "model failed"
        else:
            kept = changed_the_facts(draft, reworded, receipts)
            if kept is None:
                return {"reply": reworded, "wording": "reworded"}
            logger.warning("rewording refused (%s); the reply is sent as written", kept)
    return {"reply": draft, "wording": f"as written ({kept})"}


async def persist(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    cfg = _config(config)
    user, thread = _ids(cfg)
    memory = get_working_memory(user, thread, cfg.data_dir)
    receipts = state.get("receipts", [])
    if receipts:
        memory.invalidate_pm_cache()
    memory.add_turn("user", state["message"])
    # The models read the draft next turn and the user reads the reply. The two say the same thing, and
    # keeping the draft here means how a reply was worded can never change what a model decides later.
    memory.add_turn("assistant", state["draft"])
    append_conversation_turn(user, thread, cfg.data_dir, state["message"], state["reply"])   # the history the web app shows
    call = state.get("call") or {}
    by = state.get("handler") or COORDINATOR_NAME
    tool = "approval" if by == "approval" else (call.get("name") or "none")
    attempts = state.get("attempts", 0) + state.get("agent_attempts", 0)
    used = bundle_versions()
    log_orchestrator_turn(
        thread, cfg.data_dir, user_id=user, message=state["message"], route="GRAPH",
        route_reason=f"tool={tool}; by={by}; model_calls={attempts}; voice={state['wording']}", intent=tool,
        confidence=1.0,
        pm_prompt="", is_write=bool(receipts or state.get("approvals")), harness_verdict="n/a", harness_reason="",
        retry_count=max(0, attempts - (2 if state.get("agent_attempts") else 1)), reply=state["reply"],
        duration_ms=int((time.monotonic() - config["configurable"]["started"]) * 1000),
        versions={
            "prompts": prompts_used(),
            "bundles": {name: version for name, version in used.items()
                        if name == COORDINATOR_NAME or (state.get("agent_attempts") and name == state.get("domain"))},
            "models": {"orchestrator": cfg.model, "pm": cfg.pm_model},
            "receipts": [receipt.status for receipt in receipts],
        },
    )
    return {}
