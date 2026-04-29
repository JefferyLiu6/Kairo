# Security Policy

Kairo handles schedules, todos, habits, journal entries, private notes, approval
records, Google Calendar connections, and long-term memory. Treat every
deployment as personal-data handling software, not as a generic chatbot.

## Current Security Model

Kairo now uses real user accounts instead of anonymous demo sessions.

- Users authenticate with email and password.
- Passwords are hashed with Argon2 through `argon2-cffi`.
- Sessions use opaque random tokens stored in an HTTP-only `kairo_session`
  cookie.
- Only a SHA-256 hash of the session token is stored in `data/users.db`.
- User identity is represented by `user_id`.
- Chat state is represented by `thread_id` under the authenticated user.
- User data lives under `data/users/<user_id>/`.
- Thread metadata lives in the `chat_threads` table.

The old shared `GATEWAY_TOKEN` model is no longer the normal public app
authentication boundary.

## Ownership and Isolation

Kairo separates account-owned data from thread-owned conversation state.

User-scoped data includes:

- schedule
- todos
- habits
- journal entries
- semantic memory
- preferences
- profile
- Google Calendar account records

Thread-scoped data includes:

- working memory
- conversation transcript
- LangGraph checkpoints
- recent context
- decision traces
- audit events
- pending approvals
- in-flight clarification state

Thread-scoped actions must use the current `thread_id`. A user-level key must
not be used as a substitute for a thread key because that can leak pending state,
approvals, or decision history between chats owned by the same account.

## Authentication and Cookies

Session cookies are configured through environment variables:

```env
COOKIE_SECURE=true
COOKIE_SAMESITE=strict
```

Use `COOKIE_SECURE=true` in production. Set `COOKIE_SAMESITE=none` only when the
frontend and backend are intentionally deployed on different origins and CORS is
restricted to the exact frontend origin.

Do not put backend secrets in browser-exposed `VITE_*` variables. Browser
build-time variables are public once bundled.

## CSRF Protection

Kairo uses cookie authentication, so state-changing routes require CSRF
protection.

- `GET /auth/csrf` issues a CSRF token after authentication.
- The frontend sends the token in `X-CSRF-Token`.
- Mutating routes require a valid CSRF token.
- `/auth/logout` also requires CSRF protection.
- Pre-auth routes `/auth/signup`, `/auth/login`, and `/auth/demo` are exempt
  because no authenticated CSRF token exists yet.

CSRF tokens are bound to the current session cookie hash. The in-process token
store is acceptable for a single-process demo deployment, but multi-worker
deployments should move CSRF state to a shared store such as Redis or SQLite.

## CORS Policy

Credentialed wildcard CORS is not allowed.

If `CORS_ORIGINS` is unset or set to `*`, Kairo allows wildcard origins without
credentials. This is the safe default for non-credentialed access and local Vite
proxy development.

If cookies must be sent cross-origin, configure an explicit frontend origin:

```env
CORS_ORIGINS=https://your-frontend-domain.example
COOKIE_SECURE=true
COOKIE_SAMESITE=none
```

Do not deploy a public app with credentialed wildcard CORS.

## Rate Limiting and Abuse Controls

Kairo applies SQLite-backed sliding-window rate limits.

- Auth routes are rate-limited by IP.
- Login is also rate-limited by email hash.
- `/auth/demo` has a stricter limit because each request creates seeded data.
- Personal-manager routes are rate-limited by IP and user id.
- Demo users have stricter PM chat limits than regular accounts.

These limits persist across process restarts on a single backend instance. For
multi-instance or high-traffic production deployments, move rate-limit counters
to a shared store such as Redis.

## Demo Accounts

`POST /auth/demo` creates an ephemeral demo account with seeded sample data.

- Demo users are marked with `is_demo=true`.
- Demo users start with 5 credits.
- Demo accounts expire after 24 hours.
- Expired demo users are rejected at session lookup time.
- Expired demo users are swept when new demo accounts are created.
- Google Calendar connection is disabled for demo users.
- Demo seeding failures roll back the partially-created user and data directory.

Demo accounts are for public portfolio exploration with synthetic data only.
They are not intended for real private user data.

## Credits

Kairo uses a simple account-level credit system to control public-demo cost.

- New registered users start with 10 credits.
- Demo users start with 5 credits.
- Each accepted chat/stream turn consumes one credit.
- Requests with no remaining credits return `402`.

Credits are stored on the user row in `users.db`. This is demo cost control, not
a billing system.

## Legacy Development Routes

The legacy `demo_web` router contains workspace and terminal routes intended for
local/private development. It is disabled by default.

Production deployments should use:

```env
ENABLE_DEMO_WEB_ROUTES=0
```

If the legacy router is explicitly enabled, it requires a non-empty
`GATEWAY_TOKEN` and fails closed when the token is missing. Do not enable these
routes on a public deployment unless the environment is intentionally private and
protected.

## Approval Gates

State-changing actions are planned as typed `PMAction` objects and passed
through an approval policy before execution.

Actions requiring explicit approval include:

- deleting todos
- removing or updating schedule events
- modifying or cancelling recurring schedule occurrences
- private memory export
- private profile patching
- sensitive web-search requests

Approval requests are stored with status, risk level, payload summary, decision
timestamps, and audit events. Generic approvals and explicit approval IDs must be
checked against the current authenticated user and current thread.

## Sensitive Memory Policy

The assistant separates lower-risk shared memory from private Kairo memory.

- Sensitive facts, including medical, financial, legal, relationship, private
  goals or fears, daily routines, and journal-like reflections, should be stored
  in private Kairo storage rather than shared profile memory.
- Non-sensitive preferences, such as communication style or product
  preferences, may be stored as shared memory when useful for future
  conversations.
- The fallback prompt instructs the assistant not to expose raw private context
  unless the user specifically asks for it.
- Sensitive memory writes are routed through typed actions instead of relying on
  free-form model output.

## Prompt-Injection Handling

The deterministic workflow checks for common prompt-injection markers before
intent classification. Messages that look like embedded system/developer
instructions are refused instead of being passed to the agent fallback.

Examples of blocked patterns include:

- `ignore previous instructions`
- explicit `system:` or `assistant:` role markers
- XML-like role tags such as `<system>`
- `new instructions:` style prompt replacement attempts

This guard is intentionally conservative and is backed by adversarial regression
tests. It reduces risk, but no static rule set can catch every adversarial input.

## Web-Search Privacy Guard

The assistant treats web search as a privacy boundary. Requests that may expose
private user context, such as health, finance, identity, location history,
immigration, or similarly sensitive details, are blocked behind a high-risk
approval action instead of being sent directly to an external search provider.

Non-sensitive web-search requests may fall through to safe coaching/search
behavior depending on configuration. Private details should not be placed into
external search queries without explicit user approval.

## Error Handling

Public HTTP and streaming routes should not return raw exception strings to
clients. Unexpected server failures should be logged server-side and returned as
generic client-facing errors.

Acceptable client-facing errors include validation failures, authentication
failures, rate limits, missing configuration, and user-actionable integration
errors. Internal paths, database errors, stack traces, and upstream provider
details should stay out of responses.

## Required Production Posture

For a public portfolio deployment, use this baseline:

```env
ENABLE_DEMO_WEB_ROUTES=0
COOKIE_SECURE=true
COOKIE_SAMESITE=none
CORS_ORIGINS=https://your-frontend-domain.example
SESSION_SECRET=<random-32-byte-secret>
RATE_LIMIT_RPM=60
```

Use `COOKIE_SAMESITE=strict` instead of `none` when the frontend and backend are
served from the same origin.

## Known Limitations

This project is portfolio/demo software, not a fully hardened production
multi-tenant service.

- CSRF tokens are process-local.
- Rate-limit events are stored in local SQLite for a single backend instance;
  multi-instance deployments need a shared store.
- Local SQLite files are not encrypted at rest by this project.
- Demo-account cleanup is opportunistic, not a dedicated scheduled job.
- Credits are fixed at account creation; there is no billing, top-up, or admin
  grant flow yet.
- Google Calendar sync is limited; full two-way conflict resolution is not
  implemented.
- The prompt-injection and privacy guards are regression-tested, but no static
  rule set can catch every adversarial input.
- The eval harness checks workflow behavior and safety policy shape; it does not
  fully prove prose quality or all model-fallback behavior.
- Browser build-time environment variables are public once bundled.
- The legacy development router should remain disabled in public deployments.

## Reporting Vulnerabilities

If you find a security issue, please report it privately.

Responsible disclosure contact: `security@example.com`

Please include:

- affected route or workflow
- reproduction steps
- expected vs actual behavior
- potential impact
- suggested fix, if known

Do not include real personal data in reports. Use synthetic examples whenever
possible.
