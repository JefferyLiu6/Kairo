"""GET /personal-manager/tasks: the calendar's read-only list of open tasks."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from kairo.api.app import app
from kairo.storage.store import TodoData, TodoItem, save_todos
from kairo.storage.user_store import init_users_db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_CALENDAR_BACKGROUND_SYNC_SECONDS", "0")
    monkeypatch.setenv("COOKIE_SECURE", "false")   # TestClient uses http://
    monkeypatch.setenv("COOKIE_SAMESITE", "lax")
    init_users_db(str(tmp_path))
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _demo_user_id(client) -> str:
    res = client.post("/auth/demo")
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_tasks_requires_a_signed_in_user(client):
    assert client.get("/personal-manager/tasks").status_code == 401


def test_tasks_lists_open_tasks_only(client, tmp_path):
    user_id = _demo_user_id(client)
    save_todos(TodoData(items=[
        TodoItem(id="t1", title="rotate API keys", due="2026-11-20"),
        TodoItem(id="t2", title="book dentist"),
        TodoItem(id="t3", title="already finished", done=True),
    ]), user_id, str(tmp_path))

    res = client.get("/personal-manager/tasks")

    assert res.status_code == 200
    assert res.json() == {"tasks": [
        {"id": "t1", "title": "rotate API keys", "due": "2026-11-20"},
        {"id": "t2", "title": "book dentist", "due": None},
    ]}


def test_tasks_reports_unreadable_storage_instead_of_an_empty_list(client, tmp_path):
    user_id = _demo_user_id(client)
    path = os.path.join(str(tmp_path), "users", user_id, "todos.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not json")

    res = client.get("/personal-manager/tasks")

    assert res.status_code == 503
