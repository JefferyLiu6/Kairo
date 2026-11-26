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
  from token validation because no authenticated CSRF token exists yet. They
  require `Content-Type: application/json`; supplied browser Origins must match
  the request origin or an explicit `CORS_ORIGINS` entry. Non-browser clients
  without an Origin header may authenticate using JSON.

CSRF tokens contain a random nonce and an HMAC-SHA256 signature, with the
opaque HttpOnly session cookie as a per-session key and a CSRF-specific domain
separator. Tokens are compared in constant time and validated against a live
server session. They work across workers sharing `DATA_DIR`, survive restarts,
and allow multiple tabs to fetch tokens without invalidating one another.
Logout, session expiry, and account deletion invalidate them. Signing in replaces
and revokes the session presented by that browser, leaving other devices signed in.

Authentication and private API responses use `Cache-Control: no-store`. The UI
only reports a successful logout after the server confirms revocation. Rejected
sessions return to sign-in, and rejected writes are never automatically replayed
into a potentially different account. `/app` redirects unauthenticated
visitors to `/login`; `/demo` is the explicit temporary-account entry point.

Behind a same-origin reverse proxy, preserve the browser Host or configure the
public frontend origin in `CORS_ORIGINS`. Forwarded headers are not used to invent
trusted origins. Mixed wildcard/explicit CORS allowlists are rejected.

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

The public portfolio frontend may default unauthenticated visitors into demo
mode. Backend signup/login endpoints remain available for deployments with
durable storage, but they are not the primary public free-tier flow.

## Credits

Kairo uses a simple account-level credit system to control public-demo cost.

- New registered users start with 10 credits.
- Demo users start with 5 credits.
- Each accepted chat/stream turn consumes one credit.
- Requests with no remaining credits return `402`.

Credits are stored on the user row in `users.db`. This is demo cost control, not
a billing system.

## Approval Gates

State-changing actions are planned as typed `PMAction` objects and passed
through an approval policy before execution.

Actions requiring explicit approval include:

- deleting todos
- removing or updating schedule events
- modifying or cancelling recurring schedule occurrences
- stopping a tracked habit
- private memory export
- private profile patching
- sensitive web-search requests
- adding an event at a time the user did not give (a time the agent suggested,
  or a plan the assistant put on the calendar)

Approval requests are stored with status, risk level, payload summary, decision
timestamps, and audit events. Generic approvals and explicit approval IDs must be
checked against the current authenticated user and current thread.

Several changes proposed together that each need approval are stored as one
approval of type `batch`, whose summary lists every change. Approving it runs
each change through the same executors, with a receipt each; nothing in a batch
skips a check that a single change gets. No model can approve: approval comes
from an `approve` command, a click on the approval card, or the coordinator's
`decide_approval` call, which is only offered right after an approval was put
to the user and only records their yes or no.

A task or event a model picks for removal is checked against the user's own
words before the approval is created: if the message names items, the pick has
to be among them, and if it uses a word that is on no item, nothing is picked.

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
- `GET /personal-manager/memory` (the About me panel) returns the signed-in
  user's remembered facts and the number of private notes. The text of a
  private note is never returned.

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

## Google Calendar Tokens

- Access and refresh tokens are encrypted with `AUTH_ENCRYPTION_KEY` (Fernet)
  before they are written, and bound to the owning user so a copied row does
  not decrypt for anyone else. Without the key, connecting returns 503; tokens
  are never stored unencrypted.
- Rows written before encryption are encrypted the next time the store opens.
  SQLite may keep the old plaintext in freed pages until the file is vacuumed.
- Disconnecting asks Google to revoke the grant, then deletes the tokens, the
  sync token and the events copied from that account. The local copy is deleted
  even when Google cannot be reached.
- If the key is changed or lost, stored tokens read as missing and the user has
  to connect the calendar again.

## Personal Data in Logs

Telemetry records carry no message text. The agent step log (`AGENT_TRACE`, on
by default) does carry message, tool-argument and tool-result text, so it is
redacted before it is written (`kairo/utils/redaction.py`):

- a line on a private topic, by the same check that routes facts to private
  notes, is withheld whole and only its length is logged
- links, email addresses, phone, card and ID numbers, and postal codes are
  replaced by labels such as `[email]`

Names and other free text have no pattern to match and are still logged. Set
`AGENT_TRACE=0` where the log must hold no personal data at all.

## Required Production Posture

For a public portfolio deployment, use this baseline:

```env
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

- Real accounts require verified email. Verification and password-reset links
  expire, are single-use, and are stored as hashes. Password recovery revokes all
  sessions and preserves MFA.
- Authenticator MFA uses encrypted per-user secrets, atomic replay rejection,
  and hashed single-use recovery codes. Enrollment requires password re-entry
  and a valid code; disabling or regenerating recovery codes requires both factors.
- Email delivery (Brevo HTTPS or SMTP) runs after the HTTP response; failed delivery is logged without
  recipients/tokens. Users must resend after an outage; no durable email queue exists.
- A self-service device/session list is not implemented. See
  [deployment and recovery details](docs/AUTHENTICATION.md).
- Rate-limit events are stored in local SQLite for a single backend instance;
  multi-instance deployments need a shared store.
- Local SQLite files are not encrypted at rest by this project. MFA secrets and
  Google Calendar tokens are encrypted individually; schedules, journal entries,
  notes and mirrored calendar events are not.
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
