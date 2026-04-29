"""Kairo FastAPI app — with user account system."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Optional

_log = logging.getLogger(__name__)

try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=False), override=False)
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from assistant.shared.llm_env import load_default_llm_from_env
from assistant.personal_manager.agent import PMConfig, astream_pm, run_pm
from assistant.personal_manager.presentation.approval_preview import approval_response
from assistant.personal_manager.calendar.google import (
    GOOGLE_CALENDAR_EVENTS_SCOPE,
    GOOGLE_CALENDAR_READONLY_SCOPE,
)
from assistant.personal_manager.calendar.service import CalendarService, CalendarWriteUnavailableError
from assistant.personal_manager.calendar.store import (
    disconnect_calendar_account,
    list_calendar_accounts,
    upsert_calendar_account,
)
from assistant.personal_manager.persistence.control_store import (
    get_approval_request,
    list_approval_requests,
    list_audit_events,
    list_all_audit_events,
)
from assistant.personal_manager.persistence.decision_log import list_turn_decisions, list_all_turn_decisions
from assistant.personal_manager.persistence.store import (
    ScheduleData,
    get_upcoming_events,
    load_schedule as _load_schedule,
    save_schedule as _save_schedule,
)
from assistant.personal_manager.workflow import (
    approve_pm_request,
    normalize_pm_session_id,
    reject_pm_request,
)
from assistant.persistence.user_store import User, init_users_db
from assistant.http.auth import is_valid_csrf_token, require_user, router as auth_router

@contextlib.asynccontextmanager
async def _lifespan(application: FastAPI):
    from assistant.shared.agent_trace import ensure_agent_trace_logging
    ensure_agent_trace_logging()
    init_users_db(_service_data_dir())
    interval = _calendar_background_sync_seconds()
    if interval > 0:
        global _calendar_auto_sync_thread
        _calendar_auto_sync_stop.clear()
        _calendar_auto_sync_thread = threading.Thread(
            target=_calendar_auto_sync_loop, daemon=True,
        )
        _calendar_auto_sync_thread.start()
    print("\n" + "=" * 56)
    print("  Kairo")
    print("  http://localhost:5173  ← open this in your browser")
    print("=" * 56 + "\n")
    yield
    _calendar_auto_sync_stop.set()
    try:
        from assistant.shared.sync_executor import shutdown_sync_workers
        shutdown_sync_workers()
    except Exception:
        pass


app = FastAPI(title="Kairo", version="2.0.0", lifespan=_lifespan)
app.include_router(auth_router)

# ── CORS ──────────────────────────────────────────────────────────────────────
# Credentials (cookies) must never be paired with a wildcard origin — the browser
# CORS spec forbids it, and Starlette works around it by reflecting the Origin header,
# which is equivalent to granting every site credentialed access.
#
# Rules:
#   CORS_ORIGINS unset or "*"  → allow_origins=["*"], allow_credentials=False
#                                (non-credentialed; fine for public read-only APIs;
#                                 local dev uses the Vite proxy so CORS never fires)
#   CORS_ORIGINS=https://…     → allow_origins=[explicit list], allow_credentials=True
#                                (production: set this to your frontend's exact origin)


def _cors_config(raw: str = "") -> tuple[list[str], bool]:
    """Return (allow_origins, allow_credentials) given a raw CORS_ORIGINS string.

    Invariant: allow_credentials is True only when origins is an explicit list.
    Never pair credentials with a wildcard — Starlette reflects the Origin header
    in that case, granting every site credentialed access.
    """
    val = raw.strip()
    if val and val != "*":
        return ([o.strip() for o in val.split(",") if o.strip()], True)
    return (["*"], False)


_cors_origins, _cors_credentials = _cors_config(os.environ.get("CORS_ORIGINS", ""))

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*", "X-CSRF-Token"],
    expose_headers=["*"],
)


_CSRF_EXEMPT_PATHS = {"/auth/signup", "/auth/login", "/auth/demo"}


@app.middleware("http")
async def _csrf_middleware(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        # Signup, login, and demo are pre-auth — no CSRF token exists yet.
        # All other state-changing routes (including /auth/logout) require one.
        if request.url.path not in _CSRF_EXEMPT_PATHS:
            token = request.headers.get("X-CSRF-Token", "")
            if not is_valid_csrf_token(token, request):
                return JSONResponse({"detail": "Invalid or missing CSRF token"}, status_code=403)
    return await call_next(request)

# ── Rate limiting ─────────────────────────────────────────────────────────────
# Process-local sliding-window buckets. Fine for a single-process deploy; a
# multi-worker setup needs a shared counter (e.g. Redis) for correct enforcement.

_rate_lock = threading.Lock()
_ip_buckets: dict[str, deque] = defaultdict(deque)
_user_buckets: dict[str, deque] = defaultdict(deque)


def _rate_limit(request: Request, user: User) -> None:
    rpm_str = os.environ.get("RATE_LIMIT_RPM", "60").strip()
    try:
        rpm = int(rpm_str)
    except ValueError:
        rpm = 60
    if rpm <= 0:
        return
    now = time.time()
    ip = request.client.host if request.client else "unknown"
    with _rate_lock:
        for bucket_map, key in ((_ip_buckets, ip), (_user_buckets, user.id)):
            bucket = bucket_map[key]
            while bucket and now - bucket[0] > 60:
                bucket.popleft()
            if len(bucket) >= rpm:
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            bucket.append(now)


# ── Helpers ───────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _llm_defaults() -> tuple[str, str, Optional[str], Optional[str]]:
    d = load_default_llm_from_env()
    if d:
        return (d["provider"], d["model"], d["api_key"], d["base_url"])
    return ("anthropic", "claude-haiku-4-5-20251001", None, None)


def _service_data_dir() -> str:
    return os.environ.get("DATA_DIR", "./data")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _google_calendar_scope() -> str:
    raw = os.environ.get("GOOGLE_CALENDAR_SCOPE", "").strip()
    if raw:
        return raw
    write_enabled = os.environ.get("GOOGLE_CALENDAR_WRITE_ENABLED", "").strip().lower()
    if write_enabled in {"1", "true", "yes"}:
        return GOOGLE_CALENDAR_EVENTS_SCOPE
    return GOOGLE_CALENDAR_READONLY_SCOPE


def _google_calendar_redirect_uri() -> str:
    explicit = os.environ.get("GOOGLE_CALENDAR_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    public = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")
    if public:
        return f"{public}/personal-manager/google-calendar/callback"
    port = os.environ.get("AGENT_PORT", "8766")
    return f"http://127.0.0.1:{port}/personal-manager/google-calendar/callback"


def _google_state_secret() -> str:
    secret = (
        os.environ.get("SESSION_SECRET", "").strip()
        or os.environ.get("GOOGLE_CALENDAR_STATE_SECRET", "").strip()
        or os.environ.get("GATEWAY_TOKEN", "").strip()
        or os.environ.get("GOOGLE_CALENDAR_CLIENT_SECRET", "").strip()
    )
    if not secret:
        raise HTTPException(status_code=503, detail="SESSION_SECRET is required")
    return secret


def _sign_google_state(user_id: str, *, ttl_seconds: int = 600) -> str:
    payload = {
        "userId": user_id,
        "exp": int(time.time()) + ttl_seconds,
        "nonce": os.urandom(8).hex(),
    }
    raw = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    sig = hmac.new(_google_state_secret().encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def _verify_google_state(state: str) -> str:
    """Returns user_id."""
    try:
        raw, sig = state.split(".", 1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    expected = hmac.new(_google_state_secret().encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    padded = raw + ("=" * (-len(raw) % 4))
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=400, detail="Expired OAuth state")
    return str(payload.get("userId", ""))


def _build_google_oauth_flow(scopes: list[str]):
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="google-auth-oauthlib is not installed") from exc
    client_id = os.environ.get("GOOGLE_CALENDAR_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CALENDAR_CLIENT_SECRET", "").strip()
    redirect_uri = _google_calendar_redirect_uri()
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="GOOGLE_CALENDAR_CLIENT_ID and CLIENT_SECRET required")
    if redirect_uri.startswith(("http://127.0.0.1", "http://localhost")):
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    flow = Flow.from_client_config(
        {"web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }},
        scopes=scopes,
        autogenerate_code_verifier=False,
    )
    flow.redirect_uri = redirect_uri
    return flow


def _sync_result_response(result) -> dict:
    return {"accountId": result.account_id, "provider": result.provider,
            "synced": result.synced, "fullSync": result.full_sync}


def _parse_calendar_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {value}")


def _calendar_event_response(event) -> dict:
    return {"id": event.id, "accountId": event.account_id, "provider": event.provider,
            "providerEventId": event.provider_event_id, "title": event.title,
            "startAt": event.start_at, "endAt": event.end_at, "timezone": event.timezone,
            "status": event.status, "notes": event.notes, "location": event.location}


def _calendar_event_response_from_event(event) -> dict:
    return {"id": event.id, "accountId": event.account_id, "provider": event.provider,
            "providerEventId": event.provider_event_id, "title": event.title,
            "startAt": event.start_at.isoformat() if event.start_at else "",
            "endAt": event.end_at.isoformat() if event.end_at else "",
            "timezone": event.timezone, "status": event.status,
            "notes": event.notes, "location": event.location,
            "updatedAt": datetime.now(timezone.utc).isoformat()}


# ── Background Google Calendar auto-sync ──────────────────────────────────────

_calendar_auto_sync_stop = threading.Event()
_calendar_auto_sync_thread: Optional[threading.Thread] = None


def _calendar_background_sync_seconds() -> int:
    raw = os.environ.get("GOOGLE_CALENDAR_BACKGROUND_SYNC_SECONDS", "300").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 300


def _run_calendar_auto_sync_once() -> None:
    """Sync Google Calendar for all non-expired users in the users DB."""
    data_dir = _service_data_dir()
    try:
        from assistant.persistence.user_store import _conn as _users_conn
        now = datetime.now(timezone.utc).isoformat()
        with _users_conn(data_dir) as db:
            rows = db.execute(
                "SELECT id FROM users WHERE is_demo = 0 "
                "OR demo_expires_at IS NULL OR demo_expires_at > ?",
                (now,),
            ).fetchall()
        user_ids = [r["id"] for r in rows]
    except Exception:
        return
    for uid in user_ids:
        try:
            CalendarService(uid, data_dir).sync_google_accounts_if_stale()
        except Exception:
            continue


def _calendar_auto_sync_loop() -> None:
    interval = _calendar_background_sync_seconds()
    while not _calendar_auto_sync_stop.is_set():
        _run_calendar_auto_sync_once()
        _calendar_auto_sync_stop.wait(interval)


# ── Models ────────────────────────────────────────────────────────────────────

class PMChatRequest(BaseModel):
    message: str
    session_id: str = "default"  # thread ID (chat conversation identifier)
    provider: str = Field(default_factory=lambda: _llm_defaults()[0])
    model: str = Field(default_factory=lambda: _llm_defaults()[1])
    base_url: Optional[str] = Field(default_factory=lambda: _llm_defaults()[3])
    api_key: Optional[str] = Field(default_factory=lambda: _llm_defaults()[2])


class PMChatResponse(BaseModel):
    session_id: str
    reply: str


class GoogleCalendarEventWriteRequest(BaseModel):
    title: str = "Scheduled block"
    date: str
    start: str
    end: str
    notes: str = ""
    location: str = ""


class GoogleCalendarEventPatchRequest(BaseModel):
    title: Optional[str] = None
    date: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    notes: Optional[str] = None
    location: Optional[str] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/personal-manager/chat", response_model=PMChatResponse)
def pm_chat(req: PMChatRequest, request: Request):
    user = require_user(request)
    _rate_limit(request, user)
    thread_id = normalize_pm_session_id(req.session_id)
    config = PMConfig(
        user_id=user.id,
        provider=req.provider, model=req.model, api_key=req.api_key,
        base_url=req.base_url, data_dir=_service_data_dir(), session_id=thread_id,
    )
    try:
        reply = run_pm(req.message, config)
    except Exception as exc:
        _log.exception("pm_chat error for user %s", user.id)
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.")
    return PMChatResponse(session_id=thread_id, reply=reply)


@app.post("/personal-manager/stream")
async def pm_stream(req: PMChatRequest, request: Request):
    user = require_user(request)
    _rate_limit(request, user)
    thread_id = normalize_pm_session_id(req.session_id)
    config = PMConfig(
        user_id=user.id,
        provider=req.provider, model=req.model, api_key=req.api_key,
        base_url=req.base_url, data_dir=_service_data_dir(), session_id=thread_id,
    )

    async def _event_stream():
        try:
            async for kind, value in astream_pm(req.message, config):
                payload = json.dumps({"type": kind, "data": value}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
        except Exception:
            _log.exception("pm_stream error for user %s", user.id)
            payload = json.dumps({"type": "error", "data": "An internal error occurred."}, ensure_ascii=False)
            yield f"data: {payload}\n\n"

    return StreamingResponse(_event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/orchestrator/stream")
async def orchestrator_stream(req: PMChatRequest, request: Request):
    user = require_user(request)
    _rate_limit(request, user)
    from assistant.orchestrator.agent import OrchestratorConfig, astream_orchestrator
    from assistant.persistence.user_store import ensure_thread
    thread_id = normalize_pm_session_id(req.session_id)
    # Register the thread in users.db
    ensure_thread(_service_data_dir(), user.id, thread_id)
    env = load_default_llm_from_env()
    config = OrchestratorConfig(
        user_id=user.id,
        session_id=thread_id,
        data_dir=_service_data_dir(),
        provider=env.get("provider", req.provider),
        model=env.get("model", req.model),
        api_key=env.get("api_key") or req.api_key,
        base_url=env.get("base_url") or req.base_url,
        pm_provider=env.get("provider", req.provider),
        pm_model=os.environ.get("PM_MODEL", env.get("model", req.model)),
        pm_api_key=env.get("api_key") or req.api_key,
        pm_base_url=env.get("base_url") or req.base_url,
    )

    async def _event_stream():
        try:
            async for kind, value in astream_orchestrator(req.message, config):
                if kind == "progress":
                    payload = json.dumps({"progress": value}, ensure_ascii=False)
                elif kind == "token":
                    payload = json.dumps({"token": value}, ensure_ascii=False)
                else:
                    continue
                yield f"data: {payload}\n\n"
        except Exception:
            _log.exception("orchestrator_stream error for user %s", user.id)
            payload = json.dumps({"type": "error", "data": "An internal error occurred."}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/personal-manager/sessions")
def list_pm_sessions(request: Request) -> dict:
    user = require_user(request)
    from assistant.persistence.user_store import list_threads
    threads = list_threads(_service_data_dir(), user.id)
    return {"sessions": [{"sessionId": t.id, "title": t.title, "lastActiveAt": t.last_active_at} for t in threads]}


@app.get("/personal-manager/sessions/{session_id}")
def get_pm_session(session_id: str, request: Request) -> dict:
    user = require_user(request)
    from assistant.persistence.user_store import list_threads
    thread_id = normalize_pm_session_id(session_id)
    threads = list_threads(_service_data_dir(), user.id)
    match = next((t for t in threads if t.id == thread_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"sessionId": match.id, "title": match.title, "lastActiveAt": match.last_active_at}


@app.get("/personal-manager/sessions/{session_id}/messages")
def get_pm_session_messages(session_id: str, request: Request) -> dict:
    user = require_user(request)
    from assistant.personal_manager.agent import get_thread_messages
    from assistant.persistence.user_store import list_threads
    thread_id = normalize_pm_session_id(session_id)
    threads = list_threads(_service_data_dir(), user.id)
    if not any(t.id == thread_id for t in threads):
        raise HTTPException(status_code=404, detail="Session not found")
    messages = get_thread_messages(user.id, thread_id, _service_data_dir())
    return {"messages": messages}


@app.delete("/personal-manager/sessions/{session_id}", status_code=204)
def delete_pm_session(session_id: str, request: Request):
    user = require_user(request)
    from assistant.persistence.user_store import delete_thread
    from assistant.personal_manager.persistence.control_store import delete_thread_data
    thread_id = normalize_pm_session_id(session_id)
    delete_thread(_service_data_dir(), user.id, thread_id)
    delete_thread_data(user.id, thread_id, _service_data_dir())


@app.get("/personal-manager/upcoming")
def get_pm_upcoming(request: Request, sessionId: str = "default", days: int = 1):
    user = require_user(request)
    events = get_upcoming_events(user.id, _service_data_dir(), days=max(1, min(days, 30)))
    return {"events": events}


@app.get("/personal-manager/schedule/{session_id}")
def get_pm_schedule(session_id: str, request: Request):
    user = require_user(request)
    return _load_schedule(user.id, _service_data_dir()).model_dump()


@app.put("/personal-manager/schedule/{session_id}", status_code=200)
def put_pm_schedule(session_id: str, body: ScheduleData, request: Request):
    user = require_user(request)
    _save_schedule(body, user.id, _service_data_dir())
    return {"ok": True}


@app.get("/personal-manager/google-calendar/connect")
def connect_google_calendar(request: Request, sessionId: str = "default"):
    user = require_user(request)
    if user.is_demo:
        raise HTTPException(status_code=403, detail="Google Calendar is not available in demo accounts.")
    scopes = _google_calendar_scope().split()
    flow = _build_google_oauth_flow(scopes)
    authorization_url, _ = flow.authorization_url(
        access_type="offline", include_granted_scopes="true",
        prompt="consent", state=_sign_google_state(user.id),
    )
    return RedirectResponse(authorization_url)


@app.get("/personal-manager/google-calendar/callback")
def google_calendar_callback(request: Request) -> dict:
    state = request.query_params.get("state", "")
    if not state:
        raise HTTPException(status_code=400, detail="Missing OAuth state")
    user_id = _verify_google_state(state)
    scopes = _google_calendar_scope().split()
    flow = _build_google_oauth_flow(scopes)
    try:
        flow.fetch_token(authorization_response=str(request.url))
    except Exception:
        _log.exception("Google OAuth token exchange failed for user %s", user_id)
        raise HTTPException(status_code=400, detail="OAuth token exchange failed. Please try connecting again.")
    credentials = flow.credentials
    account = upsert_calendar_account(
        user_id, _service_data_dir(), provider="google", account_email="",
        calendar_id=os.environ.get("GOOGLE_CALENDAR_ID", "primary").strip() or "primary",
        access_token=credentials.token or "",
        refresh_token=credentials.refresh_token or "",
        token_expiry=credentials.expiry.isoformat() if credentials.expiry else None,
        scopes=list(credentials.scopes or scopes),
    )
    results = CalendarService(user_id, _service_data_dir()).sync_google_accounts()
    return {"ok": True, "account": account.model_dump(),
            "sync": [_sync_result_response(r) for r in results]}


@app.get("/personal-manager/google-calendar/accounts")
def get_google_calendar_accounts(request: Request) -> dict:
    user = require_user(request)
    accounts = list_calendar_accounts(user.id, _service_data_dir(), provider="google")
    return {"accounts": [a.model_dump() for a in accounts]}


@app.post("/personal-manager/google-calendar/sync")
def sync_google_calendar(request: Request) -> dict:
    user = require_user(request)
    try:
        results = CalendarService(user.id, _service_data_dir()).sync_google_accounts()
    except Exception:
        _log.exception("Calendar sync failed for user %s", user.id)
        raise HTTPException(status_code=502, detail="Calendar sync failed. Please try again.")
    return {"ok": True, "sync": [_sync_result_response(r) for r in results]}


@app.post("/personal-manager/google-calendar/auto-sync")
def auto_sync_google_calendar(request: Request, staleSeconds: int = 60) -> dict:
    user = require_user(request)
    data_dir = _service_data_dir()
    results = CalendarService(user.id, data_dir).sync_google_accounts_if_stale(
        stale_after_seconds=max(0, min(staleSeconds, 3600))
    )
    accounts = list_calendar_accounts(user.id, data_dir, provider="google")
    return {"ok": True, "skipped": not results,
            "sync": [_sync_result_response(r) for r in results],
            "accounts": [a.model_dump() for a in accounts]}


@app.get("/personal-manager/google-calendar/events")
def get_google_calendar_events(request: Request, start: Optional[str] = None,
                                end: Optional[str] = None, limit: int = 200) -> dict:
    user = require_user(request)
    events = CalendarService(user.id, _service_data_dir()).list_events(
        start=_parse_calendar_date(start), end=_parse_calendar_date(end), limit=limit,
    )
    return {"events": [_calendar_event_response(e) for e in events]}


@app.post("/personal-manager/google-calendar/events")
def create_google_calendar_event(body: GoogleCalendarEventWriteRequest, request: Request) -> dict:
    user = require_user(request)
    try:
        event = CalendarService(user.id, _service_data_dir()).create_google_event_from_entry(body.model_dump())
    except CalendarWriteUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception:
        _log.exception("Calendar event create failed for user %s", user.id)
        raise HTTPException(status_code=502, detail="Failed to create calendar event. Please try again.")
    return {"event": _calendar_event_response_from_event(event)}


@app.patch("/personal-manager/google-calendar/events/{provider_event_id}")
def update_google_calendar_event(provider_event_id: str, body: GoogleCalendarEventPatchRequest,
                                  request: Request) -> dict:
    user = require_user(request)
    try:
        event = CalendarService(user.id, _service_data_dir()).update_google_event(
            provider_event_id, body.model_dump(exclude_none=True),
        )
    except CalendarWriteUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception:
        _log.exception("Calendar event update failed for user %s, event %s", user.id, provider_event_id)
        raise HTTPException(status_code=502, detail="Failed to update calendar event. Please try again.")
    return {"event": _calendar_event_response_from_event(event)}


@app.delete("/personal-manager/google-calendar/events/{provider_event_id}")
def delete_google_calendar_event(provider_event_id: str, request: Request) -> dict:
    user = require_user(request)
    try:
        CalendarService(user.id, _service_data_dir()).delete_google_event(provider_event_id)
    except Exception:
        _log.exception("Calendar event delete failed for user %s, event %s", user.id, provider_event_id)
        raise HTTPException(status_code=502, detail="Failed to delete calendar event. Please try again.")
    return {"ok": True}


@app.delete("/personal-manager/google-calendar/accounts/{account_id}")
def disconnect_google_calendar_account(account_id: str, request: Request) -> dict:
    user = require_user(request)
    disconnected = disconnect_calendar_account(user.id, _service_data_dir(), account_id)
    return {"ok": disconnected}


@app.get("/personal-manager/approvals")
def get_pm_approvals(request: Request) -> dict:
    user = require_user(request)
    data_dir = _service_data_dir()
    approvals = list_approval_requests(user.id, data_dir, limit=50)
    return {"approvals": [approval_response(a, data_dir) for a in approvals]}


@app.get("/personal-manager/approvals/{approval_id}")
def get_pm_approval_detail(approval_id: str, request: Request) -> dict:
    user = require_user(request)
    data_dir = _service_data_dir()
    approval = get_approval_request(user.id, data_dir, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return approval_response(approval, data_dir, include_preview=True)


@app.post("/personal-manager/approvals/{approval_id}/approve")
def approve_pm_approval_route(approval_id: str, request: Request) -> dict:
    user = require_user(request)
    result = approve_pm_request(approval_id, _service_data_dir(), user_id=user.id)
    return {"ok": not result.lower().startswith(("error:", "no approval")), "reply": result}


@app.post("/personal-manager/approvals/{approval_id}/reject")
def reject_pm_approval_route(approval_id: str, request: Request) -> dict:
    user = require_user(request)
    result = reject_pm_request(approval_id, _service_data_dir(), user_id=user.id)
    return {"ok": not result.lower().startswith("no approval"), "reply": result}


@app.get("/personal-manager/audit")
def get_pm_audit(request: Request, sessionId: str = "", limit: int = 50) -> dict:
    user = require_user(request)
    data_dir = _service_data_dir()
    if sessionId:
        thread_id = normalize_pm_session_id(sessionId)
        events = list_audit_events(thread_id, data_dir, user_id=user.id, limit=limit)
    else:
        events = list_all_audit_events(user.id, data_dir, limit=limit)
    return {"events": events}


@app.get("/personal-manager/decisions")
def get_pm_decisions(request: Request, sessionId: str = "", limit: int = 20) -> dict:
    user = require_user(request)
    data_dir = _service_data_dir()
    if sessionId:
        thread_id = normalize_pm_session_id(sessionId)
        decisions = list_turn_decisions(thread_id, data_dir, user_id=user.id, limit=limit)
    else:
        decisions = list_all_turn_decisions(user.id, data_dir, limit=limit)
    return {"decisions": decisions}


# ── Demo web router (opt-in via env flag for local dev only) ─────────────────
if os.environ.get("ENABLE_DEMO_WEB_ROUTES", "").strip() not in ("", "0", "false", "no"):
    from assistant.http.demo_web import router as _demo_web_router  # noqa: E402
    app.include_router(_demo_web_router)
    if not os.environ.get("GATEWAY_TOKEN", "").strip():
        _log.warning(
            "ENABLE_DEMO_WEB_ROUTES is set but GATEWAY_TOKEN is empty — "
            "all legacy demo routes will reject requests with 401 until a token is configured."
        )
