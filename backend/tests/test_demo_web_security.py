from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from assistant.http.demo_web import router as _demo_web_router

# Build a minimal app that always has the demo_web routes — independent of
# ENABLE_DEMO_WEB_ROUTES so the GATEWAY_TOKEN auth can always be tested.
_test_app = FastAPI()
_test_app.include_router(_demo_web_router)


# ── HTTP routes ───────────────────────────────────────────────────────────────

def test_http_route_rejects_blank_gateway_token(monkeypatch):
    """Blank GATEWAY_TOKEN must not open legacy HTTP routes to anonymous access."""
    monkeypatch.setenv("GATEWAY_TOKEN", "")
    client = TestClient(_test_app)
    res = client.get("/workspace")
    assert res.status_code == 401


def test_http_route_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "correct-token")
    client = TestClient(_test_app)
    res = client.get("/workspace", headers={"Authorization": "Bearer wrong-token"})
    assert res.status_code == 401


def test_http_route_accepts_correct_token(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "correct-token")
    monkeypatch.delenv("WORKSPACE_DIR", raising=False)
    client = TestClient(_test_app)
    res = client.get("/workspace", headers={"Authorization": "Bearer correct-token"})
    # Auth passes — may fail for missing WORKSPACE_DIR, but not 401.
    assert res.status_code != 401


# ── WebSocket ─────────────────────────────────────────────────────────────────

def test_terminal_websocket_rejects_blank_gateway_token(monkeypatch):
    """Blank GATEWAY_TOKEN must close the WebSocket, not allow the connection."""
    monkeypatch.setenv("GATEWAY_TOKEN", "")
    client = TestClient(_test_app)

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/terminal?path=demo.py"):
            pass

    assert exc.value.code == 1008


def test_terminal_websocket_rejects_missing_gateway_token(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "test-gateway-token")
    monkeypatch.delenv("WORKSPACE_DIR", raising=False)
    client = TestClient(_test_app)

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/terminal?path=demo.py"):
            pass

    assert exc.value.code == 1008


def test_terminal_websocket_accepts_gateway_token(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "test-gateway-token")
    monkeypatch.delenv("WORKSPACE_DIR", raising=False)
    client = TestClient(_test_app)

    with client.websocket_connect("/ws/terminal?path=demo.py&token=test-gateway-token") as ws:
        message = ws.receive_json()

    assert message == {"type": "error", "data": "WORKSPACE_DIR not configured"}
