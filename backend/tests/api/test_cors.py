"""CORS security tests: credentialed wildcard must never be allowed."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from assistant.http.pm_app import _cors_config


# ── Unit tests for the config logic ──────────────────────────────────────────

@pytest.mark.parametrize("raw", ["", "*", "  *  ", "  "])
def test_wildcard_never_credentialed(raw: str):
    origins, credentials = _cors_config(raw)
    assert not credentials, (
        f"_cors_config({raw!r}) must not set allow_credentials=True with wildcard input"
    )
    assert origins == ["*"]


@pytest.mark.parametrize("raw,expected", [
    ("https://app.example.com", ["https://app.example.com"]),
    ("https://a.com, https://b.com", ["https://a.com", "https://b.com"]),
    ("https://a.com,https://b.com", ["https://a.com", "https://b.com"]),
])
def test_explicit_origins_are_credentialed(raw: str, expected: list[str]):
    origins, credentials = _cors_config(raw)
    assert credentials, f"_cors_config({raw!r}) must set allow_credentials=True for explicit origins"
    assert origins == expected


# ── Integration smoke tests using a minimal app (no pm_app reload) ───────────

def _app_with_cors(cors_raw: str) -> TestClient:
    origins, credentials = _cors_config(cors_raw)
    mini = FastAPI()
    mini.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @mini.get("/ping")
    def ping():
        return {"ok": True}

    return TestClient(mini)


@pytest.mark.parametrize("cors_raw", ["", "*"])
def test_wildcard_preflight_does_not_allow_credentials(cors_raw: str):
    client = _app_with_cors(cors_raw)
    res = client.options(
        "/ping",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    allow_credentials = res.headers.get("access-control-allow-credentials", "false")
    assert allow_credentials.lower() != "true", (
        f"CORS_ORIGINS={cors_raw!r}: cross-origin credentials must not be allowed "
        f"with wildcard origins"
    )


def test_explicit_origin_preflight_allows_credentials():
    client = _app_with_cors("https://app.example.com")
    res = client.options(
        "/ping",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.headers.get("access-control-allow-origin") == "https://app.example.com"
    assert res.headers.get("access-control-allow-credentials", "").lower() == "true"


def test_unlisted_origin_not_reflected():
    client = _app_with_cors("https://app.example.com")
    res = client.options(
        "/ping",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    allow_origin = res.headers.get("access-control-allow-origin", "")
    assert allow_origin != "https://evil.example", (
        "Unlisted origin must not receive Access-Control-Allow-Origin"
    )
