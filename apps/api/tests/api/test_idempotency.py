"""A chat message sent again with the same Idempotency-Key is carried out once."""
from __future__ import annotations

import asyncio
import json
import threading

import pytest

import kairo.agents.graph as graph_module
import kairo.api.app as app_module
import kairo.storage.user_store as user_store
from kairo.api import turns
from kairo.storage import idempotency


def tokens(body: str) -> str:
    """The reply text of a server-sent event stream."""
    text = ""
    for line in body.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            text += json.loads(line[6:]).get("token", "")
    return text


@pytest.fixture
def chat(authed_client, monkeypatch):
    """The web chat endpoint with a turn that counts how often it runs and changes nothing real."""
    runs: list[str] = []
    credits: list[str] = []

    async def graph(message, _config):
        runs.append(message)
        yield ("progress", "thinking")
        yield ("token", f"Added task: {message}")

    monkeypatch.setattr(graph_module, "astream_graph", graph)
    monkeypatch.setattr(app_module, "_rate_limit", lambda *_a, **_k: None)
    monkeypatch.setattr(app_module, "_consume_credit_or_402", lambda user: credits.append(user.id))
    monkeypatch.setattr(user_store, "ensure_thread", lambda *_a, **_k: None)

    def send(message: str, key: str | None = None, session_id: str = "pm-idem"):
        headers = {"Idempotency-Key": key} if key else {}
        return authed_client.post("/orchestrator/stream", json={"message": message, "session_id": session_id},
                                  headers=headers)

    send.runs, send.credits = runs, credits
    return send


def test_a_retry_with_the_same_key_gets_the_same_reply_without_running_again(chat):
    first = chat("buy milk", key="k-1")
    again = chat("buy milk", key="k-1")

    assert first.status_code == again.status_code == 200
    assert tokens(first.text) == tokens(again.text) == "Added task: buy milk"
    assert again.text.endswith("data: [DONE]\n\n")
    assert chat.runs == ["buy milk"] and len(chat.credits) == 1          # ran once, one credit


def test_each_new_key_is_a_new_message(chat):
    chat("buy milk", key="k-1")
    chat("buy milk", key="k-2")
    assert chat.runs == ["buy milk", "buy milk"] and len(chat.credits) == 2


def test_without_a_key_every_request_runs_as_before(chat):
    chat("buy milk")
    chat("buy milk")
    assert chat.runs == ["buy milk", "buy milk"]


def test_a_key_reused_for_a_different_message_is_refused(chat):
    chat("buy milk", key="k-1")
    other = chat("delete everything", key="k-1")
    assert other.status_code == 422 and "different message" in other.json()["detail"]
    assert chat.runs == ["buy milk"]


def test_the_same_key_in_another_chat_is_a_different_request(chat):
    chat("buy milk", key="k-1", session_id="pm-one")
    assert chat("buy milk", key="k-1", session_id="pm-two").status_code == 422
    assert chat.runs == ["buy milk"]


def test_an_unusable_key_is_refused(chat):
    assert chat("buy milk", key="x" * 201).status_code == 400
    assert chat.runs == []


def test_a_refused_request_frees_its_key_for_the_retry(chat, monkeypatch):
    def no_credits(_user):
        raise app_module.HTTPException(status_code=402, detail="No credits remaining.")

    monkeypatch.setattr(app_module, "_consume_credit_or_402", no_credits)
    assert chat("buy milk", key="k-1").status_code == 402
    monkeypatch.setattr(app_module, "_consume_credit_or_402", lambda user: None)
    assert tokens(chat("buy milk", key="k-1").text) == "Added task: buy milk"
    assert chat.runs == ["buy milk"]


def test_a_key_left_running_by_a_restart_is_answered_as_interrupted(chat, tmp_path):
    from tests.conftest import _MockUser
    user = _MockUser()
    idempotency.claim(str(tmp_path), user.id, "k-1",
                      idempotency.fingerprint("orchestrator/stream", "pm-idem", "buy milk"))   # never finished

    reply = chat("buy milk", key="k-1")
    assert tokens(reply.text) == turns.INTERRUPTED_REPLY
    assert chat.runs == []                                       # not run a second time
    assert tokens(chat("buy milk", key="k-1").text) == turns.INTERRUPTED_REPLY


def test_a_turn_that_fails_says_it_was_interrupted_now_and_on_retry(chat, monkeypatch):
    async def failing(message, _config):
        chat.runs.append(message)
        yield ("token", "Added task: buy milk.")
        raise RuntimeError("model went away")

    monkeypatch.setattr(graph_module, "astream_graph", failing)
    first = tokens(chat("buy milk and call mom", key="k-1").text)
    assert first == "Added task: buy milk.\n\n" + turns.INTERRUPTED_REPLY
    assert tokens(chat("buy milk and call mom", key="k-1").text) == first
    assert chat.runs == ["buy milk and call mom"]


# ── The store on its own ──────────────────────────────────────────────────────

def test_claims_are_per_user_and_report_what_happened(tmp_path):
    data_dir, fp = str(tmp_path), idempotency.fingerprint("a")
    assert idempotency.claim(data_dir, "u1", "k", fp).outcome == "new"
    assert idempotency.claim(data_dir, "u1", "k", fp).outcome == "running"
    assert idempotency.claim(data_dir, "u2", "k", fp).outcome == "new"          # another user's key
    idempotency.finish(data_dir, "u1", "k", "done!")
    assert idempotency.claim(data_dir, "u1", "k", fp) == idempotency.Claim("done", "done!")
    assert idempotency.claim(data_dir, "u1", "k", idempotency.fingerprint("b")).outcome == "mismatch"


def test_only_one_of_many_simultaneous_claims_wins(tmp_path):
    data_dir, fp, outcomes = str(tmp_path), idempotency.fingerprint("a"), []
    idempotency.claim(data_dir, "warm-up", "k", fp)                              # create the table first
    threads = [threading.Thread(target=lambda: outcomes.append(idempotency.claim(data_dir, "u1", "k", fp).outcome))
               for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["new"] + ["running"] * 7


def test_keys_are_forgotten_after_a_day(tmp_path):
    import time_machine
    from datetime import datetime, timedelta, timezone

    data_dir, fp = str(tmp_path), idempotency.fingerprint("a")
    with time_machine.travel(datetime(2026, 11, 12, 10, tzinfo=timezone.utc), tick=False) as clock:
        idempotency.claim(data_dir, "u1", "k", fp)
        idempotency.finish(data_dir, "u1", "k", "old")
        clock.shift(idempotency.KEY_TTL + timedelta(minutes=1))
        assert idempotency.claim(data_dir, "u1", "k", fp).outcome == "new"


# ── A retry while the turn is still running ───────────────────────────────────

def test_a_follower_that_joins_late_or_leaves_early_never_changes_the_turn():
    async def scenario():
        release = asyncio.Event()
        stored: list[str] = []

        async def events():
            yield turns.sse({"token": "Added "}), "Added "
            await release.wait()
            yield turns.sse({"token": "task."}), "task."

        run = turns.start("u1", "k-late", events(), stored.append)
        first = run.follow()
        assert tokens(await first.__anext__()) == "Added "
        await first.aclose()                                   # the first connection drops mid-turn
        assert turns.running("u1", "k-late") is run           # the turn goes on

        late = [line async for line in _follow_after(run, release)]
        assert tokens("".join(late)) == "Added task." and late[-1] == turns.DONE
        assert stored == ["Added task."] and turns.running("u1", "k-late") is None

    asyncio.run(scenario())


async def _follow_after(run, release):
    """A retry that attaches while the turn is still running, and lets it finish."""
    follower = run.follow()
    first = await follower.__anext__()
    release.set()
    yield first
    async for line in follower:
        yield line
