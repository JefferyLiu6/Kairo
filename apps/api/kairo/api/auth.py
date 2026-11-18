"""Auth endpoints: signup, login, logout, /me, /csrf, /demo."""
from __future__ import annotations
from kairo.utils.clock import local_today

import logging
import hashlib
import hmac
import os
import re
import secrets
import shutil
import sqlite3

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from kairo.api.rate_limit import RateLimitRule, bucket, email_bucket, enforce_rate_limit, env_limit, request_ip_bucket
from kairo.storage import identity
from kairo.api import account_mail as mail
from kairo.storage.user_store import User, create_demo_user, create_user, delete_expired_demo_users, delete_session, get_session_user, init_users_db

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# The opaque HttpOnly session token is a per-session signing key. A random nonce
# allows concurrent tabs to fetch tokens without invalidating one another.
_CSRF_CONTEXT = b"kairo.csrf.v1:"


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
# SQLite-backed so public demo abuse controls survive process restarts.

def _auth_rate_limit(
    request: Request,
    rules: list[RateLimitRule],
    *extra_buckets: str,
    include_ip: bool = True,
    ip_kind: str = "auth_ip",
) -> None:
    buckets = [*extra_buckets]
    if include_ip:
        buckets.insert(0, request_ip_bucket(request, ip_kind))
    enforce_rate_limit(
        _data_dir(),
        buckets,
        rules,
        detail="Too many requests. Please try again later.",
    )


def _signup_rules() -> list[RateLimitRule]:
    return [
        RateLimitRule(env_limit("AUTH_SIGNUP_RATE_LIMIT_RPM", 5), 60, "signup/minute"),
        RateLimitRule(env_limit("AUTH_SIGNUP_RATE_LIMIT_HOURLY", 20), 3600, "signup/hour"),
        RateLimitRule(env_limit("AUTH_SIGNUP_RATE_LIMIT_DAILY", 50), 86400, "signup/day"),
    ]


def _login_ip_rules() -> list[RateLimitRule]:
    return [
        RateLimitRule(env_limit("AUTH_LOGIN_RATE_LIMIT_RPM", 10), 60, "login/minute"),
        RateLimitRule(env_limit("AUTH_LOGIN_RATE_LIMIT_HOURLY", 50), 3600, "login/hour"),
    ]


def _login_email_rules() -> list[RateLimitRule]:
    return [
        RateLimitRule(env_limit("AUTH_LOGIN_EMAIL_LIMIT_15M", 5), 15 * 60, "login/email/15m"),
    ]


def _csrf_rules() -> list[RateLimitRule]:
    return [RateLimitRule(env_limit("AUTH_CSRF_RATE_LIMIT_RPM", 30), 60, "csrf/minute")]


def _demo_rules() -> list[RateLimitRule]:
    return [
        RateLimitRule(env_limit("AUTH_DEMO_RATE_LIMIT_RPM", 2), 60, "demo/minute"),
        RateLimitRule(env_limit("AUTH_DEMO_RATE_LIMIT_HOURLY", 10), 3600, "demo/hour"),
        RateLimitRule(env_limit("AUTH_DEMO_RATE_LIMIT_DAILY", 25), 86400, "demo/day"),
    ]


def _demo_global_rules() -> list[RateLimitRule]:
    # Service-wide cap on demo creation, independent of client IP. IP attribution
    # behind proxies is best-effort, so this bounds worst-case LLM spend even if
    # per-IP buckets are evaded.
    return [
        RateLimitRule(env_limit("AUTH_DEMO_GLOBAL_LIMIT_HOURLY", 30), 3600, "demo/global/hour"),
        RateLimitRule(env_limit("AUTH_DEMO_GLOBAL_LIMIT_DAILY", 150), 86400, "demo/global/day"),
    ]


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
    email: str = Field(max_length=254)
    password: str = Field(max_length=1024)
    display_name: str = Field(max_length=100)

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
    email: str = Field(max_length=254)
    password: str = Field(max_length=1024)
    code: str = Field(default="", max_length=64)


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    is_demo: bool = False
    credits_remaining: int = 0


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/signup", status_code=200)
def signup(req: SignupRequest, request: Request, background: BackgroundTasks) -> dict:
    _auth_rate_limit(request, _signup_rules(), ip_kind="auth_signup_ip")
    data_dir = _data_dir()
    init_users_db(data_dir)

    _mail_ready()
    _email_limit(request, req.email)

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

    background.add_task(mail.send_account_link, data_dir, req.email, "verify")
    return {"detail": "If this account needs verification, an email will arrive shortly. Check your inbox or request a new link."}


@router.post("/login", response_model=UserResponse)
def login(req: LoginRequest, request: Request, response: Response) -> UserResponse:
    _auth_rate_limit(request, _login_ip_rules(), ip_kind="auth_login_ip")
    _auth_rate_limit(
        request,
        _login_email_rules(),
        email_bucket(req.email, "auth_login_email"),
        include_ip=False,
    )
    data_dir = _data_dir()
    init_users_db(data_dir)

    try:
        token = identity.login_session(data_dir, req.email, req.password, req.code,
                                       request.cookies.get("kairo_session"))
    except identity.IdentityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except identity.IdentityUnavailable as exc:
        raise HTTPException(status_code=503, detail="Account security is temporarily unavailable") from exc
    user = get_session_user(data_dir, token)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in again")
    _set_auth_cookie(response, token)
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_demo=False,
        credits_remaining=user.credits_remaining,
    )


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    token = request.cookies.get("kairo_session")
    if token:
        delete_session(_data_dir(), token)
    _clear_auth_cookie(response)


@router.get("/me", response_model=UserResponse)
def me(request: Request) -> UserResponse:
    user = require_user(request)
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_demo=user.is_demo,
        credits_remaining=user.credits_remaining,
    )


def _csrf_signature(raw_session: str, nonce: str) -> str:
    return hmac.new(
        raw_session.encode(), _CSRF_CONTEXT + nonce.encode(), hashlib.sha256,
    ).hexdigest()


@router.get("/csrf")
def csrf_token(request: Request) -> dict:
    """Issue a CSRF token bound to the current session cookie."""
    require_user(request)
    _auth_rate_limit(request, _csrf_rules(), ip_kind="auth_csrf_ip")
    nonce = secrets.token_hex(32)
    signature = _csrf_signature(request.cookies["kairo_session"], nonce)
    return {"csrf_token": f"{nonce}.{signature}"}


def is_valid_csrf_token(token: str, request: Request | None = None) -> bool:
    if request is None or not re.fullmatch(r"[0-9a-f]{64}\.[0-9a-f]{64}", token):
        return False
    raw = request.cookies.get("kairo_session")
    if not raw:
        return False
    nonce, signature = token.split(".")
    if not hmac.compare_digest(signature, _csrf_signature(raw, nonce)):
        return False
    # Logout, expiry and account deletion invalidate every token for the session.
    return get_session_user(_data_dir(), raw) is not None


def verify_csrf(request: Request) -> None:
    """Raise 403 if the request is missing a valid CSRF token."""
    token = request.headers.get("X-CSRF-Token", "")
    if not is_valid_csrf_token(token, request):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")


# ── Verification, recovery and multifactor authentication ────────────────────

class EmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class TokenPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=128)
    password: str = Field(min_length=8, max_length=1024)


class FactorRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)
    code: str = Field(default="", max_length=64)


def _mail_ready() -> None:
    try:
        mail.configuration()
    except identity.IdentityUnavailable as exc:
        raise HTTPException(status_code=503, detail="Account email is temporarily unavailable") from exc


def _email_limit(request: Request, email: str) -> None:
    _auth_rate_limit(request, [RateLimitRule(5, 3600, "email/hour")],
                     email_bucket(email, "account_email"), ip_kind="account_email_ip")


@router.post("/verification/request")
@router.post("/password/forgot")
def request_account_link(req: EmailRequest, request: Request, background: BackgroundTasks) -> dict:
    _mail_ready()
    _email_limit(request, req.email)
    purpose = "verify" if request.url.path.endswith("verification/request") else "reset"
    background.add_task(mail.send_account_link, _data_dir(), req.email, purpose)
    return {"detail": "If this account is eligible, an email will arrive shortly. Check your inbox and spam folder. You can request another link later."}


@router.post("/verification/confirm")
@router.post("/password/reset")
def confirm_account_link(req: TokenPasswordRequest, request: Request, response: Response,
                         background: BackgroundTasks) -> dict:
    _auth_rate_limit(request, [RateLimitRule(10, 900, "account_token/15m")], ip_kind="account_token_ip")
    purpose = "verify" if request.url.path.endswith("verification/confirm") else "reset"
    # Find the notification recipient without ever exposing it to callers.
    from kairo.storage.user_store import _conn, _hash_token
    with _conn(_data_dir()) as db:
        recipient = db.execute("SELECT u.email FROM users u JOIN account_tokens t ON t.user_id = u.id "
                               "WHERE t.token_hash = ? AND t.purpose = ?", (_hash_token(req.token), purpose)).fetchone()
    try:
        identity.change_password_with_token(_data_dir(), req.token, purpose, req.password)
    except identity.IdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _clear_auth_cookie(response)
    if recipient:
        background.add_task(mail.send_security_notice, recipient[0],
                            "Your Kairo password was set. All existing sessions were revoked. "
                            "If you did not do this, use Forgot password to secure your account. "
                            "Any enabled authenticator remains required.")
    return {"detail": "Your password is saved and email verified. Sign in to continue; MFA remains required if enabled."}


@router.get("/security")
def account_security(request: Request) -> dict:
    user = require_user(request)
    if user.is_demo:
        raise HTTPException(status_code=403, detail="Account security is unavailable for temporary demos")
    return identity.status(_data_dir(), user.id)


def _factor_user(request: Request) -> User:
    user = require_user(request)
    if user.is_demo:
        raise HTTPException(status_code=403, detail="Create a real account to use MFA")
    _auth_rate_limit(request, [RateLimitRule(5, 900, "factor/15m")],
                     bucket("account_factor", user.id), ip_kind="account_factor_ip")
    return user


@router.post("/mfa/setup")
def setup_mfa(req: FactorRequest, request: Request) -> dict:
    user = _factor_user(request)
    try:
        return identity.setup_mfa(_data_dir(), user.id, request.cookies["kairo_session"], req.password)
    except identity.IdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except identity.IdentityUnavailable as exc:
        raise HTTPException(status_code=503, detail="Account security is temporarily unavailable") from exc


@router.post("/mfa/enable")
@router.post("/mfa/disable")
@router.post("/mfa/recovery")
def update_mfa(req: FactorRequest, request: Request, background: BackgroundTasks) -> dict:
    user = _factor_user(request)
    action = request.url.path.rsplit("/", 1)[-1]
    try:
        result = identity.update_mfa(_data_dir(), user.id, request.cookies["kairo_session"],
                                     req.password, req.code, action)
    except identity.IdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except identity.IdentityUnavailable as exc:
        raise HTTPException(status_code=503, detail="Account security is temporarily unavailable") from exc
    background.add_task(mail.send_security_notice, user.email,
                        "Your Kairo MFA settings or recovery codes changed. Other devices were signed out. "
                        "If you did not do this, reset your password immediately.")
    return result


# ── Demo account ──────────────────────────────────────────────────────────────

def _seed_demo_user(user_id: str, data_dir: str) -> None:
    """Seed a fresh demo account with realistic sample data."""
    import re as _re
    from datetime import timedelta
    from kairo.storage.store import ScheduleData, ScheduleEntry, RecurrenceRule, TodoData, TodoItem, save_schedule, save_todos
    from kairo.storage.habits import habit_add, habit_checkin, _init as _init_habits
    from kairo.storage.journal import journal_append, _init as _init_journal
    from kairo.storage.store import _pm_dir
    from kairo.storage.control_store import pm_db_path

    today = local_today()
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
    from kairo.storage.store import _pm_dir
    user_dir = _pm_dir(user_id, data_dir)
    try:
        shutil.rmtree(user_dir, ignore_errors=True)
    except Exception:
        pass


@router.post("/demo", response_model=UserResponse, status_code=201)
def demo(request: Request, response: Response) -> UserResponse:
    """Create an ephemeral demo account with seeded data and a 24-hour session."""
    _auth_rate_limit(request, _demo_rules(), ip_kind="auth_demo_ip")
    _auth_rate_limit(
        request, _demo_global_rules(), bucket("auth_demo_global", "all"), include_ip=False
    )
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
            from kairo.storage.user_store import _conn as _users_conn
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
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_demo=True,
        credits_remaining=user.credits_remaining,
    )
