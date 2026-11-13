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

from kairo.agents.graph.prompts import COORDINATOR, LEFTOVER, VOICE
from kairo.agents.graph.state import GraphState
from kairo.agents.graph.voice import VOICE_TIMEOUT, changed_the_facts, sent_as_written, voice_enabled, voice_input
from kairo.agents.orchestrator.agent import OrchestratorConfig
from kairo.agents.orchestrator.memory import get_working_memory
from kairo.agents.personal_manager.application.approval_flow import approve_from_chat, queue_for_approval, reject_from_chat, split_by_approval, stored_read_back, submit_action, submit_actions
from kairo.agents.personal_manager.application.pending_reply import approval_verb, route_message
from kairo.agents.personal_manager.extractors.intent import _looks_like_injection
from kairo.core.domain.pending import GraphQuestion
from kairo.core.domain.types import PMAction
from kairo.core.domain.session import normalize_pm_session_id
from kairo.core.llm_env import build_llm, message_text
from kairo.core.observability.telemetry import emit
from kairo.core.prompt import Prompt, prompts_used, record_prompt
from kairo.domains.catalog import DOMAINS
from kairo.domains.checks import calendar_hint, named_in
from kairo.domains.registry import Answer, DomainSpec, NotThisDomain, Proposed, ToolContext
from kairo.storage.control_store import append_conversation_turn, list_approval_requests
from kairo.storage.decision_log import log_orchestrator_turn
from kairo.storage.working_memory import clear_active_working_memory, load_active_working_memory, save_working_memory
from kairo.tools.receipts import collecting, read_back_reply
from kairo.utils.clock import local_now

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2          # each model gets one retry after its call is sent back
MAX_STEPS = 4             # parts of one message the coordinator may plan
MAX_ROUNDS = 2            # questions in a row about one request before the user is left to restate it
HISTORY_TURNS = 6
COORDINATOR_NAME = "coordinator"
_GRAPH_QUESTION = "graph"  # working-memory source for a question this graph asked
_REFUSAL = "I can't take instructions embedded inside a user message. Please rephrase in your own words."
_NOTHING_CHANGED = "I haven't changed anything. Could you say what you'd like me to do, with the day and time?"
_LOST = "I couldn't finish that. Could you say it another way?"
_FOCUS = "(Do only this part of the message now: “{part}”. Its other parts are handled separately.)"
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


SPLIT = "do_several"      # the coordinator's tool for a message with several parts. Not called "plan":
                          # "plan my day" is a request for advice, and the name invited the model to call it.


def _plan_tool() -> dict[str, Any]:
    """do_several: a message that asks for several separate things, split into the part each area handles."""
    areas = [domain.name for domain in (*DOMAINS.inline_domains(), *DOMAINS.hand_off_domains())]
    step = {"type": "object", "additionalProperties": False, "required": ["domain", "request"], "properties": {
        "domain": {"type": "string", "enum": areas, "description": "The area that handles this part."},
        "request": {"type": "string", "description": "The user's own words for this part, with the day and time "
                                                     "words that belong to it."}}}
    return _function(SPLIT, "Split a message that asks for two or more separate things into steps, one for each "
                             "thing. Each step is then done on its own, and the user gets one reply.",
                     {"steps": {"type": "array", "items": step}})


def coordinator_tools(*, approval_offered: bool = False, planning: bool = True) -> list[dict[str, Any]]:
    """Inline domains' tools, then hand_off, plan and ask_user; decide_approval only while an approval
    is on offer. A part of a plan is not planned again."""
    domains = [domain.name for domain in DOMAINS.hand_off_domains()]
    tools = [tool.schema() for tool in DOMAINS.inline_tools()] + [
        _function("hand_off", "Pass the message to the part of Kairo that handles it.",
                  {"domain": {"type": "string", "enum": domains}}),
        *([_plan_tool()] if planning else []),
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
    return _small_model(config, system, shown)


def leftover(config: OrchestratorConfig, system: Prompt, shown: str) -> str:
    """The sweep's one model call, on the same small model and short timeout: the part of the
    message the turn left undone, in the model's words for "none" or the user's for the part."""
    return _small_model(config, system, shown)


def _small_model(config: OrchestratorConfig, system: Prompt, shown: str) -> str:
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
                       message=_wording(state), vault_dir=cfg.vault_dir, request=request)


_WHEN_WORD = re.compile(r"\d+|\b(?:mon|tue|wed|thu|fri|sat|sun)\w*|\b(?:today|tonight|tomorrow|tmrw|morning|afternoon"
                        r"|evening|night|noon|midnight|week|month|daily|every|each)\b", re.I)


def _wording(state: GraphState) -> str:
    """The words a proposal is checked against. For one part of a planned message that is the part,
    so a time that belongs to another part is not held against it ("move the standup to 10 and add a
    task to email Sam" asks for no task at 10). The part was written by a model, so it is only
    trusted if it adds no day, time or repeat word of its own; otherwise the whole message is used."""
    message, part = state["message"], state.get("focus") or ""
    if not part:
        return message
    said = {word.lower() for word in _WHEN_WORD.findall(message)}
    return part if all(word.lower() in said for word in _WHEN_WORD.findall(part)) else message


def _clock(state: GraphState) -> str:
    now = state["now"]
    return f"Current date and time: {now:%A} {now:%Y-%m-%d %H:%M}.\nThe next two weeks:\n{calendar_hint(now)}"


def _turns(state: GraphState, *, other: str = "") -> list[tuple[str, str]]:
    turns = [*state.get("history", []), ("user", state["message"])]
    if state.get("focus"):
        # One part of a message with several. The whole message stays in view: its own words are
        # what a proposal is checked against.
        turns.append(("user", _FOCUS.format(part=state["focus"])))
    turns += [("user", finding) for finding in _findings(state)]
    if state.get("rejection"):
        turns.append(("user", _RETRY.format(reason=state["rejection"], other=other)))
    return turns


def _findings(state: GraphState) -> list[str]:
    """What was looked up for the part of the message being done now."""
    return state.get("findings", [])[state.get("findings_from", 0):]


def _planning(state: GraphState) -> bool:
    return state.get("plan") is not None


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
    if _planning(state) and state.get("asked"):
        # An earlier part of this message already left the user a question, and only one is kept
        # open. This part is named as not done, so it is not lost and not claimed.
        return {"notes": [f"Not done yet: “{state.get('focus') or state['message']}”. Ask me for it again "
                          "once the question above is settled."], "handler": domain, "route": "respond"}
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
    part = bool(state.get("focus"))
    call = _first_call(cfg, system, _turns(state, other="call hand_off if this is not for that tool, "),
                       coordinator_tools(approval_offered=bool(offer) and not part, planning=not part))
    update = {"call": call, "attempts": state.get("attempts", 0) + 1, "rejection": None,
              "proposer": COORDINATOR_NAME}
    name, args = (call["name"], call.get("args") or {}) if call else ("", {})

    if name == SPLIT and not part:
        steps = [{"domain": str(s.get("domain", "")), "request": str(s.get("request", "")).strip()}
                 for s in args.get("steps") or [] if isinstance(s, dict)]
        steps = [s for s in steps if DOMAINS.domain(s["domain"]) is not None and s["request"]][:MAX_STEPS]
        problem = _split_problem(state["message"], steps)
        if problem is None:
            return {**update, "plan": steps, "step_index": 0, "handler": COORDINATOR_NAME, "route": "step"}
        if update["attempts"] < MAX_ATTEMPTS:
            return {**update, "rejection": problem, "route": "coordinate"}       # once; then the message is not split
        only = DOMAINS.domain(steps[0]["domain"]) if steps else None
        if only is not None and only.mode == "agent":
            return {**update, "domain": only.name, "route": "agent"}
        return {**update, **_to_catch_all()}

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
        question_text = str(args["question"]).strip()
        refused = spec.check_question(question_text, _context(state, cfg, spec.name)) if spec.check_question else None
        if refused and attempts < MAX_ATTEMPTS:
            return {**update, "route": "agent", "rejection": refused}      # once; a second time it is asked
        return {**update, **_ask(state, cfg, spec.name, question_text)}
    if name == "answer" and str(args.get("text", "")).strip():
        text = str(args["text"]).strip()
        if not _CLAIMS_A_CHANGE.search(text):
            return {**update, "notes": [text], "route": "respond"}
        if attempts < MAX_ATTEMPTS + (1 if _findings(state) else 0):
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
    # A lookup is not an attempt at the answer, so each one an agent made earns it one more call.
    # The domain's own budget still caps the turn.
    back = "agent" if by_agent else "coordinate"
    spec, owner = DOMAINS.tool(call["name"]), DOMAINS.domain_of(call["name"])
    budget = owner.max_calls if by_agent else MAX_ATTEMPTS
    limit = min(MAX_ATTEMPTS + len(_findings(state)), budget)
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
        return _ask(state, cfg, owner.name, owner.restate.format(message=state.get("focus") or state["message"]),
                    because=f"Nothing was saved, because I couldn't settle the details ({problem}).")

    if spec.kind == "read":
        return {"notes": [spec.read(args, ctx)], "route": "respond"}
    if spec.kind == "lookup":
        # The result goes back to the agent that asked, which then acts on it. Its last call of the
        # turn cannot be another lookup: there would be nothing left to act with.
        if not by_agent or attempts >= budget:
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


def _split_problem(message: str, steps: list[dict[str, str]]) -> Optional[str]:
    """Why a message is not to be split into these steps, or None if it may be.

    A split that is wrong stores things nobody asked for, so it has to earn its place: the steps go
    to at least two different areas (one area takes a whole message as it is), and each step is the
    user's own words (a step with a word the message does not have was thought up, not asked for).
    """
    if len({step["domain"] for step in steps}) < 2:
        return ("this message is for one area, so it is not split: call that area's tool, or hand_off, with the "
                "whole message")
    said = set(re.findall(r"\w+", message.casefold()))
    for step in steps:
        added = [word for word in re.findall(r"\w+", step["request"].casefold()) if word not in said]
        if added:
            return (f"the step “{step['request']}” is not the user's own words (the message does not say "
                    f"“{added[0]}”): split a message only into parts it actually has, or do not split it")
    return None


_JOINS = re.compile(r"\b(and|then|also|plus)\b|[,;]", re.I)


def sweepable(state: GraphState) -> bool:
    """Whether a turn that is about to reply should first be checked for a part left undone: once,
    only for a message that joins things together, and not when it was split, refused, an approval
    command, or already ended in a question."""
    return (not state.get("swept") and state.get("plan") is None and not state.get("asked")
            and state.get("handler") not in (None, "approval") and bool(_JOINS.search(state["message"])))


async def sweep(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Look for a request in the message that the turn did not touch, and do it.

    The coordinator does not always split a message that asks for two things: it hands the whole
    message to one area, and the other request is dropped without a word. Here the small model is
    shown the message beside what was done and names the part left out. It is only believed if
    that part is the user's own words, is not most of the message, and shares no word with what
    was done; then it is done like a part of a plan, through the same gate.
    """
    cfg = _config(config)
    message, done = state["message"], "\n".join(_lines(state))
    try:
        part = leftover(cfg, LEFTOVER, json.dumps({"message": message, "done": done}, ensure_ascii=False)).strip(" \"'“”.")
    except Exception:
        logger.exception("the sweep model failed; the turn replies with what it has")
        return {"swept": True, "route": "respond"}
    at = message.casefold().find(part.casefold())
    if (at < 0 or len(part.split()) < 2 or len(part) > 0.8 * len(message) or named_in(part, (done,))):
        return {"swept": True, "route": "respond"}
    return {"swept": True, "plan": [{"domain": "", "request": message[at:at + len(part)]}], "step_index": 0,
            "route": "step"}


async def step(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Start the next part of a planned message, or finish the plan.

    Each part is done like a message of its own: the coordinator calls an inline domain's tool for
    it, or the domain's agent takes it, and the gate checks what is proposed. What needs approval
    is held and asked for once, when every part has had its turn.
    """
    cfg = _config(config)
    user, thread = _ids(cfg)
    steps, index = state["plan"], state.get("step_index", 0)
    spent = state.get("attempts", 0) + state.get("agent_attempts", 0)
    if index >= len(steps):
        done = {"plan_done": True, "focus": "", "spent": spent, "attempts": 0, "agent_attempts": 0, "route": "respond"}
        held = state.get("held", [])
        if held and state.get("approvals"):
            # A part found afterwards needs approval, and the turn has already asked for one. Two
            # approvals in one reply cannot both be answered, so this part waits and is named.
            return {**done, "notes": [f"Not done yet: “{steps[-1]['request']}”. Ask me for it again once the "
                                      "approval above is settled."]}
        asked = queue_for_approval(held, cfg.pm_config(), user, thread_id=thread, intent="plan")
        return {**done, "approvals": [str(result["message"]) for result in asked]}
    part = steps[index]
    fresh = {"step_index": index + 1, "focus": part["request"], "spent": spent, "attempts": 0, "agent_attempts": 0,
             "rejection": None, "call": None, "actions": [], "findings_from": len(state.get("findings", []))}
    area = DOMAINS.domain(part["domain"])      # a part found afterwards has no area yet: the coordinator places it
    if area is not None and area.mode == "agent":
        return {**fresh, "domain": part["domain"], "route": "agent"}
    return {**fresh, "route": "coordinate"}


async def act(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """Run the proposed commands through the approval policy; read the stores back for whatever ran.
    Several that each need approval are asked for once, as one batch. In a plan, what needs approval
    is held until every part is done, so the user is asked once for the whole message."""
    cfg = _config(config)
    user, thread = _ids(cfg)
    approvals: list[str] = []
    results: list[str] = []
    held: list[PMAction] = []
    intent = state["call"]["name"]
    with collecting() as made:
        if _planning(state):
            run_now, held = split_by_approval(state.get("actions", []))
            outcomes = [submit_action(action, cfg.pm_config(), user, thread_id=thread, intent=intent)[1]
                        for action in run_now]
        else:
            outcomes = submit_actions(state.get("actions", []), cfg.pm_config(), user, thread_id=thread, intent=intent)
        for result in outcomes:
            (approvals if result.get("pending") else results).append(str(result["message"]))
    return {"receipts": made.receipts, "approvals": approvals, "results": results, "held": held}


def _lines(state: GraphState) -> list[str]:
    """What the turn has to say so far: what was read back from storage, approval prompts, notes."""
    lines: list[str] = []
    receipts = state.get("receipts", [])
    if receipts or state.get("results"):
        # A write the stores could not confirm falls back to the executor's own message.
        lines.append(read_back_reply(receipts) or "\n".join(state.get("results", [])))
    return [line for line in (*lines, *state.get("approvals", []), *state.get("notes", [])) if line]


async def respond(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """The reply as the records give it: what was read back from storage, then approval prompts, then
    anything else to say."""
    lines = [*_lines(state), _park(state, _config(config)) or ""]
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
    attempts = state.get("spent", 0) + state.get("attempts", 0) + state.get("agent_attempts", 0)
    plan = state.get("plan")
    if plan:
        tool = (f"{tool}+swept" if state.get("swept")
                else "plan(" + ", ".join(part["domain"] for part in plan) + ")")
    used = bundle_versions()
    log_orchestrator_turn(
        thread, cfg.data_dir, user_id=user, message=state["message"], route="GRAPH",
        route_reason=f"tool={tool}; by={by}; model_calls={attempts}; voice={state['wording']}", intent=tool,
        confidence=1.0,
        pm_prompt="", is_write=bool(receipts or state.get("approvals")), harness_verdict="n/a", harness_reason="",
        retry_count=0 if plan else max(0, attempts - (2 if state.get("agent_attempts") else 1)), reply=state["reply"],
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
