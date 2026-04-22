from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from assistant.http.pm_app import app


def test_terminal_websocket_rejects_missing_gateway_token(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "test-gateway-token")
    monkeypatch.delenv("WORKSPACE_DIR", raising=False)
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/terminal?path=demo.py"):
            pass

    assert exc.value.code == 1008


def test_terminal_websocket_accepts_gateway_token(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "test-gateway-token")
    monkeypatch.delenv("WORKSPACE_DIR", raising=False)
    client = TestClient(app)

    with client.websocket_connect("/ws/terminal?path=demo.py&token=test-gateway-token") as ws:
        message = ws.receive_json()

    assert message == {"type": "error", "data": "WORKSPACE_DIR not configured"}
