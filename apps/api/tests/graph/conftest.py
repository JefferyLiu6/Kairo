"""Shared by the graph tests: real graph and stores, with the model scripted."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import time_machine

from kairo.agents.graph import nodes, run_graph
from kairo.agents.orchestrator import memory as orch_memory
from kairo.agents.orchestrator.agent import OrchestratorConfig
from kairo.storage.store import TodoData, TodoItem, load_todos, save_todos
from kairo.storage.working_memory import load_active_working_memory

THREAD, USER = "pm-graph", "graph-user"
THURSDAY = datetime(2026, 11, 12, 10, 0, tzinfo=ZoneInfo("America/Toronto"))


def item(title, due_date=None, due_time=None):
    return {"title": title, "due_date": due_date, "due_time": due_time}


def call(tool, /, **args):
    return {"name": tool, "args": args}


def say(text):
    """The assistant agent answering in words."""
    return call("answer", text=text)


@pytest.fixture(autouse=True)
def fresh_turn_memory():
    orch_memory._sessions.clear()
    yield
    orch_memory._sessions.clear()


@pytest.fixture
def chat(tmp_path, monkeypatch):
    """Send messages through the real graph and stores. The model is scripted.

    `chat.model` is a list of answers the coordinator's model gives, in order: each is one
    tool call, or a list of them. `chat.asked` records what the model was shown each time.

    The voice model is scripted too. It returns each draft unchanged unless a test sets
    `chat.voice` to a function of the draft; `chat.voiced` records what it was shown.
    """
    config = OrchestratorConfig(session_id=THREAD, user_id=USER, data_dir=str(tmp_path), provider="test",
                                model="small-model", pm_provider="test", pm_model="unused")
    script: list = []
    asked: list[dict] = []

    def ask_model(_config, system, turns, tools):
        asked.append({"prompt": getattr(system, "name", ""), "system": str(system), "turns": list(turns),
                      "tools": [t["function"]["name"] for t in tools]})
        assert script, f"the model was asked more often than the test scripted: {turns[-1]}"
        answer = script.pop(0)
        return answer if isinstance(answer, list) else [answer]

    def reword(_config, _system, shown):
        voiced.append(json.loads(shown))
        return send.voice(voiced[-1]["draft"])

    voiced: list[dict] = []
    monkeypatch.setattr(nodes, "ask_model", ask_model)
    monkeypatch.setattr(nodes, "reword", reword)
    # The sweep's model finds nothing left undone unless a test sets `chat.leftover` to a function of the message.
    monkeypatch.setattr(nodes, "leftover", lambda _config, _system, shown: send.leftover(json.loads(shown)))
    monkeypatch.delenv("KAIRO_VOICE", raising=False)

    def send(message: str) -> str:
        with time_machine.travel(THURSDAY, tick=True):
            return run_graph(message, config)

    def question():
        """The open question, read on the same pinned clock it was saved under (it expires after two hours)."""
        with time_machine.travel(THURSDAY, tick=True):
            return load_active_working_memory(THREAD, str(tmp_path), user_id=USER)

    send.model, send.asked, send.question = script, asked, question
    send.voice, send.voiced = (lambda draft: draft), voiced
    send.leftover = lambda shown: "none"
    send.data_dir, send.config = str(tmp_path), config
    return send


def todos(chat):
    return [(t.title, t.due, t.done) for t in load_todos(USER, chat.data_dir).items]


def seed(chat, *titles):
    save_todos(TodoData(items=[TodoItem(id=f"t{i}", title=title) for i, title in enumerate(titles, 1)]),
               USER, chat.data_dir)
