"""Kairo FastAPI app for the standalone demo."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Optional

try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=False), override=False)
except ImportError:
    pass

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
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
    list_calendar_account_sessions,
    list_calendar_accounts,
    upsert_calendar_account,
)
from assistant.personal_manager.persistence.control_store import (
    get_approval_request,
    list_approval_requests,
    list_audit_events,
)
from assistant.personal_manager.persistence.decision_log import list_turn_decisions
from assistant.personal_manager.persistence.store import (
    ScheduleData,
    load_schedule as _load_schedule,
    save_schedule as _save_schedule,
)
from assistant.personal_manager.workflow import (
    approve_pm_request,
    normalize_pm_session_id,
    reject_pm_request,
)

app = FastAPI(title="Kairo Demo", version="1.0.0")


def _ensure_demo_seeded(session_id: str) -> None:
    """Seed a demo session on first use if it has no data yet."""
    try:
        import base64
        data_dir = os.environ.get("DATA_DIR", "./data")
        vault_dir = os.environ.get("VAULT_DIR", "./vault")
        encoded = base64.urlsafe_b64encode(f"pm-{session_id}".encode()).decode().rstrip("=")
        db_path = os.path.join(data_dir, "personal-manager", encoded, "pm.db")
        if not os.path.exists(db_path):
            import sys
            scripts_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "../../scripts"))
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            # Also ensure backend root is on path
            backend_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "../.."))
            if backend_root not in sys.path:
                sys.path.insert(0, backend_root)
            from seed_demo import seed_session
            seed_session(session_id, data_dir, vault_dir)
    except Exception as e:
        print(f"[demo seed] failed: {e}")

# ── CORS ──────────────────────────────────────────────────────────────────────

_cors_raw = os.environ.get("CORS_ORIGINS", "*").strip()
_cors_origins = (
    ["*"] if _cors_raw == "*" else [o.strip() for o in _cors_raw.split(",") if o.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=(_cors_raw != "*"),
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth ──────────────────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


def _require_auth(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    token = os.environ.get("GATEWAY_TOKEN", "").strip()
    if not token:
        return
    if credentials is None or not hmac.compare_digest(credentials.credentials, token):
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Rate limiting ─────────────────────────────────────────────────────────────

_rate_lock = threading.Lock()
_rate_buckets: dict[str, deque] = defaultdict(deque)

# ── Demo session call limit ───────────────────────────────────────────────────

_demo_call_counts: dict[str, int] = defaultdict(int)
DEMO_SESSION_LIMIT = int(os.environ.get("DEMO_SESSION_LIMIT", "6"))


def _check_demo_limit(session_id: str) -> None:
    """Raise 429 if this demo session has exhausted its call budget."""
    if not session_id.startswith("demo"):
        return
    with _rate_lock:
        _demo_call_counts[session_id] += 1
        count = _demo_call_counts[session_id]
    if count > DEMO_SESSION_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Demo limit reached ({DEMO_SESSION_LIMIT} messages). Refresh to start a new session.",
        )


def _rate_limit(request: Request) -> None:
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
        bucket = _rate_buckets[ip]
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


def _service_vault_dir() -> Optional[str]:
    return os.environ.get("VAULT_DIR") or None


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
    # Auto-derive from PUBLIC_URL if set (e.g. https://api.example.com)
    public = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")
    if public:
        return f"{public}/personal-manager/google-calendar/callback"
    port = os.environ.get("AGENT_PORT", "8766")
    return f"http://127.0.0.1:{port}/personal-manager/google-calendar/callback"


def _google_state_secret() -> str:
    secret = (
        os.environ.get("GOOGLE_CALENDAR_STATE_SECRET", "").strip()
        or os.environ.get("GATEWAY_TOKEN", "").strip()
        or os.environ.get("GOOGLE_CALENDAR_CLIENT_SECRET", "").strip()
    )
    if not secret:
        raise HTTPException(status_code=503, detail="GOOGLE_CALENDAR_STATE_SECRET is required")
    return secret


def _sign_google_state(session_id: str, *, ttl_seconds: int = 600) -> str:
    payload = {
        "sessionId": normalize_pm_session_id(session_id),
        "exp": int(time.time()) + ttl_seconds,
        "nonce": os.urandom(8).hex(),
    }
    raw = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    sig = hmac.new(_google_state_secret().encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def _verify_google_state(state: str) -> str:
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
    return normalize_pm_session_id(str(payload.get("sessionId", "")))


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
    data_dir = _service_data_dir()
    for sid in list_calendar_account_sessions(data_dir, provider="google"):
        try:
            CalendarService(sid, data_dir).sync_google_accounts_if_stale()
        except Exception:
            continue


def _calendar_auto_sync_loop() -> None:
    interval = _calendar_background_sync_seconds()
    while not _calendar_auto_sync_stop.is_set():
        _run_calendar_auto_sync_once()
        _calendar_auto_sync_stop.wait(interval)


@app.on_event("startup")
def _startup() -> None:
    from assistant.shared.agent_trace import ensure_agent_trace_logging
    ensure_agent_trace_logging()
    interval = _calendar_background_sync_seconds()
    if interval > 0:
        global _calendar_auto_sync_thread
        _calendar_auto_sync_stop.clear()
        _calendar_auto_sync_thread = threading.Thread(
            target=_calendar_auto_sync_loop, daemon=True,
        )
        _calendar_auto_sync_thread.start()
    print("\n" + "=" * 56)
    print("  Kairo — Demo")
    print("  http://localhost:5173  ← open this in your browser")
    print("  Demo sessions auto-seeded on first message")
    print("=" * 56 + "\n")


@app.on_event("shutdown")
def _shutdown() -> None:
    _calendar_auto_sync_stop.set()
    try:
        from assistant.shared.sync_executor import shutdown_sync_workers
        shutdown_sync_workers()
    except Exception:
        pass


# ── Models ────────────────────────────────────────────────────────────────────

class PMChatRequest(BaseModel):
    message: str
    session_id: str = "demo"
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
    return {"status": "ok", "mode": "pm-demo"}


@app.post("/personal-manager/chat", response_model=PMChatResponse)
def pm_chat(req: PMChatRequest, _auth: None = Depends(_require_auth), _rl: None = Depends(_rate_limit)):
    sid = f"pm-{req.session_id}" if not req.session_id.startswith("pm-") else req.session_id
    config = PMConfig(provider=req.provider, model=req.model, api_key=req.api_key,
                      base_url=req.base_url, data_dir=_service_data_dir(),
                      vault_dir=_service_vault_dir(), session_id=sid)
    try:
        reply = run_pm(req.message, config)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return PMChatResponse(session_id=sid, reply=reply)


@app.post("/personal-manager/stream")
async def pm_stream(req: PMChatRequest, _auth: None = Depends(_require_auth), _rl: None = Depends(_rate_limit)):
    sid = f"pm-{req.session_id}" if not req.session_id.startswith("pm-") else req.session_id
    config = PMConfig(provider=req.provider, model=req.model, api_key=req.api_key,
                      base_url=req.base_url, data_dir=_service_data_dir(),
                      vault_dir=_service_vault_dir(), session_id=sid)

    async def _event_stream():
        try:
            async for kind, value in astream_pm(req.message, config):
                payload = json.dumps({"type": kind, "data": value}, ensure_ascii=False)
                yield f"data: {payload}\n\n"
        except Exception as exc:
            payload = json.dumps({"type": "error", "data": str(exc)}, ensure_ascii=False)
            yield f"data: {payload}\n\n"

    return StreamingResponse(_event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/demo/seed")
async def demo_seed(req: dict = Body(default={}), _auth: None = Depends(_require_auth)):
    """Seed a fresh demo session synchronously. Call on new session creation."""
    session_id = req.get("sessionId", "")
    if not session_id.startswith("demo"):
        return {"ok": False, "reason": "not a demo session"}
    _ensure_demo_seeded(session_id)
    return {"ok": True}


@app.post("/orchestrator/stream")
async def orchestrator_stream(req: PMChatRequest, _auth: None = Depends(_require_auth), _rl: None = Depends(_rate_limit)):
    _check_demo_limit(req.session_id)
    if req.session_id.startswith("demo"):
        _ensure_demo_seeded(req.session_id)
    from assistant.orchestrator.agent import OrchestratorConfig, astream_orchestrator
    sid = f"pm-{req.session_id}" if not req.session_id.startswith("pm-") else req.session_id
    env = load_default_llm_from_env()
    config = OrchestratorConfig(
        session_id=sid,
        data_dir=_service_data_dir(),
        vault_dir=_service_vault_dir(),
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
        except Exception as exc:
            payload = json.dumps({"type": "error", "data": str(exc)}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/personal-manager/sessions")
def list_pm_sessions(_auth: None = Depends(_require_auth)) -> dict:
    import sqlite3 as _sqlite3
    data_dir = _service_data_dir()
    sessions: dict[str, dict] = {}
    db_path = os.path.join(data_dir, "personal-manager", "checkpoints.db")
    if os.path.exists(db_path):
        try:
            conn = _sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE 'pm-%' ORDER BY thread_id"
            ).fetchall()
            conn.close()
            for r in rows:
                sessions[r[0]] = {"sessionId": r[0]}
        except Exception:
            pass
    return {"sessions": list(sessions.values())}


@app.get("/personal-manager/sessions/{session_id}")
def get_pm_session(session_id: str, _auth: None = Depends(_require_auth)) -> dict:
    import sqlite3 as _sqlite3
    data_dir = _service_data_dir()
    sid = f"pm-{session_id}" if not session_id.startswith("pm-") else session_id
    db_path = os.path.join(data_dir, "personal-manager", "checkpoints.db")
    if not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        conn = _sqlite3.connect(db_path)
        row = conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", (sid,)).fetchone()
        conn.close()
        if not row or row[0] == 0:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"sessionId": sid, "checkpointCount": row[0]}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/personal-manager/sessions/{session_id}", status_code=204)
def delete_pm_session(session_id: str, _auth: None = Depends(_require_auth)):
    pass  # No-op for demo


@app.get("/personal-manager/schedule/{session_id}")
def get_pm_schedule(session_id: str, _auth: None = Depends(_require_auth)):
    sid = f"pm-{session_id}" if not session_id.startswith("pm-") else session_id
    return _load_schedule(sid, _service_data_dir()).model_dump()


@app.put("/personal-manager/schedule/{session_id}", status_code=200)
def put_pm_schedule(session_id: str, body: ScheduleData, _auth: None = Depends(_require_auth)):
    sid = f"pm-{session_id}" if not session_id.startswith("pm-") else session_id
    _save_schedule(body, sid, _service_data_dir())
    return {"ok": True}


@app.get("/personal-manager/google-calendar/connect")
def connect_google_calendar(sessionId: str = "demo", _auth: None = Depends(_require_auth)):
    sid = normalize_pm_session_id(sessionId)
    scopes = _google_calendar_scope().split()
    flow = _build_google_oauth_flow(scopes)
    authorization_url, _ = flow.authorization_url(
        access_type="offline", include_granted_scopes="true",
        prompt="consent", state=_sign_google_state(sid),
    )
    return RedirectResponse(authorization_url)


@app.get("/personal-manager/google-calendar/callback")
def google_calendar_callback(request: Request) -> dict:
    state = request.query_params.get("state", "")
    if not state:
        raise HTTPException(status_code=400, detail="Missing OAuth state")
    sid = _verify_google_state(state)
    scopes = _google_calendar_scope().split()
    flow = _build_google_oauth_flow(scopes)
    try:
        flow.fetch_token(authorization_response=str(request.url))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"OAuth callback failed: {exc}")
    credentials = flow.credentials
    account = upsert_calendar_account(
        sid, _service_data_dir(), provider="google", account_email="",
        calendar_id=os.environ.get("GOOGLE_CALENDAR_ID", "primary").strip() or "primary",
        access_token=credentials.token or "",
        refresh_token=credentials.refresh_token or "",
        token_expiry=credentials.expiry.isoformat() if credentials.expiry else None,
        scopes=list(credentials.scopes or scopes),
    )
    results = CalendarService(sid, _service_data_dir()).sync_google_accounts()
    return {"ok": True, "account": account.model_dump(),
            "sync": [_sync_result_response(r) for r in results]}


@app.get("/personal-manager/google-calendar/accounts")
def get_google_calendar_accounts(sessionId: str, _auth: None = Depends(_require_auth)) -> dict:
    sid = normalize_pm_session_id(sessionId)
    accounts = list_calendar_accounts(sid, _service_data_dir(), provider="google")
    return {"accounts": [a.model_dump() for a in accounts]}


@app.post("/personal-manager/google-calendar/sync")
def sync_google_calendar(sessionId: str, _auth: None = Depends(_require_auth)) -> dict:
    sid = normalize_pm_session_id(sessionId)
    try:
        results = CalendarService(sid, _service_data_dir()).sync_google_accounts()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True, "sync": [_sync_result_response(r) for r in results]}


@app.post("/personal-manager/google-calendar/auto-sync")
def auto_sync_google_calendar(sessionId: str, staleSeconds: int = 60, _auth: None = Depends(_require_auth)) -> dict:
    sid = normalize_pm_session_id(sessionId)
    data_dir = _service_data_dir()
    results = CalendarService(sid, data_dir).sync_google_accounts_if_stale(
        stale_after_seconds=max(0, min(staleSeconds, 3600))
    )
    accounts = list_calendar_accounts(sid, data_dir, provider="google")
    return {"ok": True, "skipped": not results,
            "sync": [_sync_result_response(r) for r in results],
            "accounts": [a.model_dump() for a in accounts]}


@app.get("/personal-manager/google-calendar/events")
def get_google_calendar_events(sessionId: str, start: Optional[str] = None,
                                end: Optional[str] = None, limit: int = 200,
                                _auth: None = Depends(_require_auth)) -> dict:
    sid = normalize_pm_session_id(sessionId)
    events = CalendarService(sid, _service_data_dir()).list_events(
        start=_parse_calendar_date(start), end=_parse_calendar_date(end), limit=limit,
    )
    return {"events": [_calendar_event_response(e) for e in events]}


@app.post("/personal-manager/google-calendar/events")
def create_google_calendar_event(body: GoogleCalendarEventWriteRequest, sessionId: str,
                                  _auth: None = Depends(_require_auth)) -> dict:
    sid = normalize_pm_session_id(sessionId)
    try:
        event = CalendarService(sid, _service_data_dir()).create_google_event_from_entry(body.model_dump())
    except CalendarWriteUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"event": _calendar_event_response_from_event(event)}


@app.patch("/personal-manager/google-calendar/events/{provider_event_id}")
def update_google_calendar_event(provider_event_id: str, body: GoogleCalendarEventPatchRequest,
                                  sessionId: str, _auth: None = Depends(_require_auth)) -> dict:
    sid = normalize_pm_session_id(sessionId)
    try:
        event = CalendarService(sid, _service_data_dir()).update_google_event(
            provider_event_id, body.model_dump(exclude_none=True),
        )
    except CalendarWriteUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"event": _calendar_event_response_from_event(event)}


@app.delete("/personal-manager/google-calendar/events/{provider_event_id}")
def delete_google_calendar_event(provider_event_id: str, sessionId: str,
                                  _auth: None = Depends(_require_auth)) -> dict:
    sid = normalize_pm_session_id(sessionId)
    try:
        CalendarService(sid, _service_data_dir()).delete_google_event(provider_event_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True}


@app.delete("/personal-manager/google-calendar/accounts/{account_id}")
def disconnect_google_calendar_account(account_id: str, sessionId: str,
                                        _auth: None = Depends(_require_auth)) -> dict:
    sid = normalize_pm_session_id(sessionId)
    disconnected = disconnect_calendar_account(sid, _service_data_dir(), account_id)
    return {"ok": disconnected}


@app.get("/personal-manager/approvals")
def get_pm_approvals(sessionId: str, _auth: None = Depends(_require_auth)) -> dict:
    data_dir = _service_data_dir()
    sid = normalize_pm_session_id(sessionId)
    approvals = list_approval_requests(sid, data_dir, limit=50)
    return {"approvals": [approval_response(a, data_dir) for a in approvals]}


@app.get("/personal-manager/approvals/{approval_id}")
def get_pm_approval_detail(approval_id: str, sessionId: str,
                            _auth: None = Depends(_require_auth)) -> dict:
    data_dir = _service_data_dir()
    sid = normalize_pm_session_id(sessionId)
    approval = get_approval_request(sid, data_dir, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return approval_response(approval, data_dir, include_preview=True)


@app.post("/personal-manager/approvals/{approval_id}/approve")
def approve_pm_approval_route(approval_id: str, sessionId: Optional[str] = None,
                               _auth: None = Depends(_require_auth)) -> dict:
    sid = normalize_pm_session_id(sessionId) if sessionId else None
    result = approve_pm_request(approval_id, _service_data_dir(),
                                 vault_dir=_service_vault_dir(), session_id=sid)
    return {"ok": not result.lower().startswith(("error:", "no approval")), "reply": result}


@app.post("/personal-manager/approvals/{approval_id}/reject")
def reject_pm_approval_route(approval_id: str, sessionId: Optional[str] = None,
                              _auth: None = Depends(_require_auth)) -> dict:
    sid = normalize_pm_session_id(sessionId) if sessionId else None
    result = reject_pm_request(approval_id, _service_data_dir(), session_id=sid)
    return {"ok": not result.lower().startswith("no approval"), "reply": result}


@app.get("/personal-manager/audit")
def get_pm_audit(sessionId: str, limit: int = 50, _auth: None = Depends(_require_auth)) -> dict:
    sid = normalize_pm_session_id(sessionId)
    return {"events": list_audit_events(sid, _service_data_dir(), limit=limit)}


@app.get("/personal-manager/decisions")
def get_pm_decisions(sessionId: str, limit: int = 20, _auth: None = Depends(_require_auth)) -> dict:
    sid = normalize_pm_session_id(sessionId)
    return {"decisions": list_turn_decisions(sid, _service_data_dir(), limit=limit)}


# ── Demo web router ───────────────────────────────────────────────────────────
# Chat/stream is Kairo PM-only; workspace routes stay disabled unless configured.
from assistant.http.demo_web import router as _demo_web_router  # noqa: E402
app.include_router(_demo_web_router)
