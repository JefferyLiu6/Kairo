"""Pytest bootstrap: load .env so tests pick up LLM credentials when present."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent
# The app dates things in KAIRO_TIMEZONE; tests compare with date.today(), which
# follows the machine clock (UTC on CI). Pin both to one zone so they agree on
# what "today" is at every hour, not just during the day.
os.environ["KAIRO_TIMEZONE"] = "America/Toronto"
os.environ["TZ"] = "America/Toronto"
time.tzset()
# Tests never send LangSmith traces, even when .env turns tracing on.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ.pop("LANGCHAIN_TRACING_V2", None)
load_dotenv(_REPO_ROOT / ".env", override=False)


# ── Auth test helpers ─────────────────────────────────────────────────────────

class _MockUser:
    """Minimal user object for bypassing auth in HTTP route tests."""
    id = "test-user-id"
    email = "test@example.com"
    display_name = "Test User"


@pytest.fixture
def authed_client(monkeypatch, tmp_path):
    """Return a TestClient with require_user and CSRF patched out."""
    from fastapi.testclient import TestClient
    from assistant.http.pm_app import app
    import assistant.http.pm_app as _pm_app_mod

    monkeypatch.setattr(_pm_app_mod, "require_user", lambda request: _MockUser())
    monkeypatch.setattr(_pm_app_mod, "is_valid_csrf_token", lambda token, request=None: True)
    # Use an isolated data dir and disable the background calendar sync so the
    # startup event doesn't scan the real ./data/users.db.
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_CALENDAR_BACKGROUND_SYNC_SECONDS", "0")
    with TestClient(app) as client:
        yield client
