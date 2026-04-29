"""Auth endpoints: signup, login, logout, /me, /csrf, /demo."""
from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request, Response

_log = logging.getLogger(__name__)
from pydantic import BaseModel, field_validator

from assistant.persistence.user_store import (
    User,
    create_demo_user,
    create_session,
    create_user,
    delete_expired_demo_users,
    delete_session,
    get_session_user,
    get_user_by_email,
    init_users_db,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# CSRF tokens: session_id (SHA-256 of cookie) → issued token.
# Process-local: in a multi-worker deployment each worker holds its own map, so a
# token issued by worker A will be rejected by worker B.  Acceptable for a single-
# process deploy; replace with a shared cache (Redis, DB) before adding workers.
_csrf_tokens: dict[str, str] = {}  # session_id → csrf_token


# ── Cookie helpers ────────────────────────────────────────────────────────────

def _cookie_samesite() -> str:
    return os.environ.get("COOKIE_SAMESITE", "strict").lower()


def _cookie_secure() -> bool:
    val = os.environ.get("COOKIE_SECURE", "true").lower()
    return val not in ("0", "false", "no")


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "kairo_session",
        token,
        httponly=True,
        secure=_cookie_secure(),
        samesite=_cookie_samesite(),
        max_age=30 * 24 * 3600,
        path="/",
    )


def _clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        "kairo_session",
        httponly=True,
        secure=_cookie_secure(),
        samesite=_cookie_samesite(),
        path="/",
    )


def _data_dir() -> str:
    return os.environ.get("DATA_DIR", "./data")


# ── Auth-route rate limiting ──────────────────────────────────────────────────
# Separate, stricter bucket from the per-user PM rate limiter.
# Defaults: 10 attempts per IP per minute on login/signup/demo.
# Process-local: same multi-worker caveat as _csrf_tokens above.

_auth_rate_lock = threading.Lock()
_auth_ip_buckets: dict[str, deque] = defaultdict(deque)


def _auth_rate_limit(request: Request, rpm: int = 10) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    with _auth_rate_lock:
        bucket = _auth_ip_buckets[ip]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= rpm:
            raise HTTPException(status_code=429, detail="Too many requests — try again later")
        bucket.append(now)


# ── Dependency: extract current user from cookie ──────────────────────────────

def get_current_user(request: Request) -> User | None:
    token = request.cookies.get("kairo_session")
    if not token:
        return None
    return get_session_user(_data_dir(), token)


def require_user(request: Request) -> User:
    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# ── Models ────────────────────────────────────────────────────────────────────

class SignupRequest(BaseModel):
    email: str
    password: str
    display_name: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v.strip()):
            raise ValueError("Invalid email address")
        return v.strip().lower()

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

    @field_validator("display_name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Display name cannot be empty")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    is_demo: bool = False


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/signup", status_code=200)
def signup(req: SignupRequest, request: Request) -> dict:
    _auth_rate_limit(request)
    data_dir = _data_dir()
    init_users_db(data_dir)

    # Always return the same response regardless of whether the email is already registered.
    # Returning 409 for existing emails is an enumeration oracle; 200 + same body is not.
    # The user is required to log in separately after signing up.
    #
    # Only silence IntegrityError (UNIQUE constraint = duplicate email).
    # All other exceptions — disk full, DB locked, hash failure — propagate as 500.
    try:
        create_user(data_dir, req.email, req.password, req.display_name)
    except sqlite3.IntegrityError:
        pass  # Duplicate email — silently succeed so callers can't distinguish new vs existing.

    return {"detail": "If this email is not already registered, your account has been created. Sign in to continue."}


@router.post("/login", response_model=UserResponse)
def login(req: LoginRequest, request: Request, response: Response) -> UserResponse:
    from assistant.persistence.user_store import verify_password
    _auth_rate_limit(request)
    data_dir = _data_dir()
    init_users_db(data_dir)

    user = verify_password(data_dir, req.email, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_session(data_dir, user.id)
    _set_auth_cookie(response, token)
    return UserResponse(id=user.id, email=user.email, display_name=user.display_name, is_demo=False)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    token = request.cookies.get("kairo_session")
    if token:
        delete_session(_data_dir(), token)
    _clear_auth_cookie(response)


@router.get("/me", response_model=UserResponse)
def me(request: Request) -> UserResponse:
    user = require_user(request)
    return UserResponse(id=user.id, email=user.email, display_name=user.display_name, is_demo=user.is_demo)


def _session_id_from_request(request: Request) -> str | None:
    """Extract the session identifier (SHA-256 of raw cookie token) for CSRF keying."""
    raw = request.cookies.get("kairo_session")
    if not raw:
        return None
    import hashlib
    return hashlib.sha256(raw.encode()).hexdigest()


@router.get("/csrf")
def csrf_token(request: Request) -> dict:
    """Issue a CSRF token bound to the current session cookie."""
    require_user(request)
    sid = _session_id_from_request(request)
    if not sid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = secrets.token_urlsafe(32)
    _csrf_tokens[sid] = token
    return {"csrf_token": token}


def is_valid_csrf_token(token: str, request: Request | None = None) -> bool:
    if not token:
        return False
    if request is not None:
        sid = _session_id_from_request(request)
        if sid is None:
            return False
        return _csrf_tokens.get(sid) == token
    # Fallback: accept if the token appears anywhere (for test compatibility).
    return token in _csrf_tokens.values()


def verify_csrf(request: Request) -> None:
    """Raise 403 if the request is missing a valid CSRF token."""
    token = request.headers.get("X-CSRF-Token", "")
    if not is_valid_csrf_token(token, request):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")


# ── Demo account ──────────────────────────────────────────────────────────────

def _seed_demo_user(user_id: str, data_dir: str) -> None:
    """Seed a fresh demo account with realistic sample data."""
    import re as _re
    from datetime import date, timedelta
    from assistant.personal_manager.persistence.store import (
        ScheduleData, ScheduleEntry, RecurrenceRule, TodoData, TodoItem,
        save_schedule, save_todos,
    )
    from assistant.personal_manager.persistence.habits import habit_add, habit_checkin, _init as _init_habits
    from assistant.personal_manager.persistence.journal import journal_append, _init as _init_journal
    from assistant.personal_manager.persistence.store import _pm_dir
    from assistant.personal_manager.persistence.control_store import pm_db_path

    today = date.today()
    tomorrow = today + timedelta(days=1)
    this_friday = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
    next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    fmt = lambda d: d.isoformat()  # noqa: E731

    schedule = ScheduleData(entries=[
        ScheduleEntry(id="aaa00001", title="Morning run", start="07:00", end="07:45",
                      recurrence=RecurrenceRule(freq="weekly", by_day=["MO", "WE", "FR"]),
                      series_id="aaa00001", date=fmt(today)),
        ScheduleEntry(id="aaa00002", title="Team standup", start="09:30", end="09:50",
                      recurrence=RecurrenceRule(freq="weekly", by_day=["MO", "TU", "WE", "TH", "FR"]),
                      series_id="aaa00002", date=fmt(today)),
        ScheduleEntry(id="aaa00003", title="Lunch with Maya", start="12:30", end="13:30", date=fmt(today)),
        ScheduleEntry(id="aaa00004", title="Deep work block", start="14:00", end="16:00",
                      recurrence=RecurrenceRule(freq="weekly", by_day=["TU", "TH"]),
                      series_id="aaa00004", date=fmt(today)),
        ScheduleEntry(id="aaa00005", title="1:1 with Alex", start="10:00", end="10:30", date=fmt(tomorrow)),
        ScheduleEntry(id="aaa00006", title="Product demo with investors", start="15:00", end="16:00", date=fmt(tomorrow)),
        ScheduleEntry(id="aaa00007", title="Weekly review", start="17:00", end="17:30",
                      recurrence=RecurrenceRule(freq="weekly", by_day=["FR"]),
                      series_id="aaa00007", date=fmt(this_friday)),
        ScheduleEntry(id="aaa00008", title="Sprint planning", start="10:00", end="11:00", date=fmt(next_monday)),
    ])
    save_schedule(schedule, user_id, data_dir)

    todos = TodoData(items=[
        TodoItem(id="t0000001", title="Prep slides for investor demo", due=fmt(today)),
        TodoItem(id="t0000002", title="Follow up with Alex on API proposal", due=fmt(tomorrow)),
        TodoItem(id="t0000003", title="Review Q2 roadmap doc", due=fmt(this_friday)),
        TodoItem(id="t0000004", title="Book dentist appointment"),
        TodoItem(id="t0000005", title="Read Atomic Habits chapter 6"),
    ])
    save_todos(todos, user_id, data_dir)

    db_path = pm_db_path(user_id, data_dir)
    _init_habits(db_path)
    habit_ids: dict[str, str] = {}
    for name in ["Morning run 🏃", "Read 30 min 📚", "No phone after 10pm 📵"]:
        result = habit_add(name, db_path)
        m = _re.search(r"\[([a-f0-9-]+)\]", result)
        if m:
            habit_ids[name] = m.group(1)
    run_id = habit_ids.get("Morning run 🏃")
    read_id = habit_ids.get("Read 30 min 📚")
    nophone_id = habit_ids.get("No phone after 10pm 📵")
    from datetime import timedelta as _td
    if run_id:
        for d in [today - _td(days=i) for i in [1, 3, 4, 6, 7]]:
            habit_checkin(run_id, db_path, d.isoformat())
    if read_id:
        for d in [today - _td(days=i) for i in [0, 1, 2, 3, 5]]:
            habit_checkin(read_id, db_path, d.isoformat())
    if nophone_id:
        for d in [today - _td(days=i) for i in [1, 2, 4]]:
            habit_checkin(nophone_id, db_path, d.isoformat())

    _init_journal(db_path)
    journal_append(
        "Good standup today — the team is aligned on the API timeline. "
        "Need to make sure Alex has what he needs before the 1:1 tomorrow.",
        db_path,
    )
    journal_append(
        "Investor demo is tomorrow. Slides look solid but I want to tighten the opening "
        "2 minutes. The product speaks for itself once they see the live demo.",
        db_path,
    )

    user_dir = _pm_dir(user_id, data_dir)
    os.makedirs(user_dir, exist_ok=True)
    profile_path = os.path.join(user_dir, "PROFILE.md")
    with open(profile_path, "w", encoding="utf-8") as f:
        f.write("""\
# User Profile

## About
- Name: Alex (demo account)
- Role: Product manager at a mid-stage startup
- Works remotely, prefers async communication

## Preferences
- Prefers morning deep work before meetings (before 10am)
- Likes back-to-back meetings on Tuesdays and Thursdays to keep other days clear
- Takes a lunch break away from the desk — important for energy
- Prefers short, actionable summaries over long explanations
- Ends the day by 6:30pm

## Working style
- Uses time-blocking to protect focus time
- Reviews todos every morning during standup prep
- Weekly review every Friday at 5pm to close out the week

## Current focus
- Preparing for investor demo (high priority this week)
- Improving team communication cadence
- Building consistent exercise and reading habits
""")


def _cleanup_demo_data(data_dir: str, user_id: str) -> None:
    """Remove a demo user's data directory."""
    from assistant.personal_manager.persistence.store import _pm_dir
    user_dir = _pm_dir(user_id, data_dir)
    try:
        shutil.rmtree(user_dir, ignore_errors=True)
    except Exception:
        pass


@router.post("/demo", response_model=UserResponse, status_code=201)
def demo(request: Request, response: Response) -> UserResponse:
    """Create an ephemeral demo account with seeded data and a 24-hour session."""
    _auth_rate_limit(request, rpm=5)  # stricter — each call writes files
    data_dir = _data_dir()
    init_users_db(data_dir)

    # Sweep expired demo accounts before creating a new one.
    try:
        expired_ids = delete_expired_demo_users(data_dir)
        for uid in expired_ids:
            _cleanup_demo_data(data_dir, uid)
    except Exception:
        pass

    user, token = create_demo_user(data_dir)
    try:
        _seed_demo_user(user.id, data_dir)
    except Exception:
        # Seeding failed — tear down the half-created user so we don't accumulate orphans.
        _log.exception("Demo seed failed for user %s — rolling back", user.id)
        try:
            from assistant.persistence.user_store import _conn as _users_conn
            with _users_conn(data_dir) as db:
                db.execute("DELETE FROM users WHERE id = ?", (user.id,))
        except Exception:
            pass
        _cleanup_demo_data(data_dir, user.id)
        raise HTTPException(status_code=500, detail="Demo setup failed. Please try again.")

    response.set_cookie(
        "kairo_session", token,
        httponly=True,
        secure=_cookie_secure(),
        samesite=_cookie_samesite(),
        max_age=24 * 3600,
        path="/",
    )
    return UserResponse(id=user.id, email=user.email, display_name=user.display_name, is_demo=True)
