"""GET /personal-manager/memory: what Kairo remembers, beside what it was asked to remember."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from kairo.agents.personal_manager.agent import PMConfig
from kairo.agents.personal_manager.application.approval_flow import submit_action
from kairo.api.app import app
from kairo.core.domain.types import PMAction
from kairo.storage.control_store import pm_db_path
from kairo.storage.profile import profile_path
from kairo.storage.store import load_private, save_private
from kairo.storage.user_store import init_users_db
from kairo.tools.executors import memory as memory_executor


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_CALENDAR_BACKGROUND_SYNC_SECONDS", "0")
    monkeypatch.setenv("COOKIE_SECURE", "false")   # TestClient uses http://
    monkeypatch.setenv("COOKIE_SAMESITE", "lax")
    monkeypatch.setattr(memory_executor, "maybe_reconcile_async", lambda *_a, **_k: None)   # no background model call
    init_users_db(str(tmp_path))
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _remember(user_id: str, data_dir: str, fact: str) -> None:
    """Store a fact the way the memory agent's `remember` tool does."""
    vault = os.path.dirname(pm_db_path(user_id, data_dir))
    config = PMConfig(user_id=user_id, provider="test", model="unused", data_dir=data_dir, vault_dir=vault,
                      session_id="pm-memory")
    submit_action(PMAction("remember", {"fact": fact}, summary=f"Remember: {fact}"), config, user_id,
                  thread_id="pm-memory", intent="remember")


def test_memory_requires_a_signed_in_user(client):
    assert client.get("/personal-manager/memory").status_code == 401


def test_a_new_demo_account_holds_its_sample_profile_and_was_asked_nothing(client):
    client.post("/auth/demo")
    body = client.get("/personal-manager/memory").json()
    assert "Name: Alex (demo account)" in body["facts"]
    assert body["privateNotes"] == 0 and body["asked"] == []


def test_memory_lists_what_is_held_and_checks_it_against_what_was_asked(client, tmp_path):
    user_id = client.post("/auth/demo").json()["id"]
    data_dir = str(tmp_path)
    _remember(user_id, data_dir, "I prefer meetings after 10am")
    _remember(user_id, data_dir, "Maya's mum prefers texts over calls")
    # The profile is tidied in the background. Here it rewords the second fact, as the reconciler can.
    path = profile_path(os.path.dirname(pm_db_path(user_id, data_dir)))
    with open(path, encoding="utf-8") as f:
        tidied = f.read().replace("Maya's mum prefers", "User's mum prefers")
    with open(path, "w", encoding="utf-8") as f:
        f.write(tidied)
    private = load_private(user_id, data_dir)
    private.notes_private.append("passport number X1234567")
    save_private(private, user_id, data_dir)

    body = client.get("/personal-manager/memory").json()

    assert body["facts"][-2:] == ["I prefer meetings after 10am", "User's mum prefers texts over calls"]
    assert body["privateNotes"] == 1 and "X1234567" not in str(body)          # counted, never returned
    assert [(a["text"], a["kept"]) for a in body["asked"]] == [               # newest first
        ("Maya's mum prefers texts over calls", False),
        ("I prefer meetings after 10am", True),
    ]
    assert all(a["at"] for a in body["asked"])
