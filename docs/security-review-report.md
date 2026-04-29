# Kairo Security and Product Hardening Report

Last updated: 2026-04-29

## Scope

This report documents the account-system, data-isolation, demo-mode, and deployment-safety issues found during the recent Kairo review. It is written as an internal before/after record so the project can show a clear engineering progression from a single-user demo toward a safer multi-user AI assistant.

The reviewed areas were:

- Email/password authentication and HTTP-only cookie sessions.
- Per-user data ownership and per-thread conversation isolation.
- Approval flow safety for destructive personal-manager actions.
- CSRF, CORS, rate limiting, and public deployment hardening.
- Demo account behavior and legacy demo router exposure.
- PM conversation history restore behavior.
- Frontend product polish that could make the project look unfinished.

## Executive Summary

Kairo originally looked like a strong AI-agent prototype, but the first account-system pass introduced several common multi-user bugs: user-level keys were reused for thread-level state, cookie-auth write routes were not consistently protected by CSRF, legacy demo routes remained reachable, and PM history was only partially wired.

Most major issues have now been fixed. The app is in a much better state for a 2026 AI-engineer portfolio demo because it now shows real ownership boundaries, safer approval flows, a controlled demo mode, generic production errors, and authenticated PM thread history.

The remaining concerns are mostly production-hardening and polish items, not core architecture blockers:

- CSRF tokens and rate-limit buckets are still process-local.
- SQLite user data is not encrypted at rest.
- `SECURITY.md` is stale and should be updated before publishing.
- Legacy demo routes should stay disabled in public deployments.

## Before and After


| Area                           | Before                                                                                                                       | After                                                                                                             | Status            |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | ----------------- |
| User identity                  | Data was scoped to anonymous `session_id` values stored in localStorage. There was no real account boundary.                 | Email/password accounts, HTTP-only `kairo_session` cookies, and `user_id` ownership now exist.                    | Fixed             |
| Data ownership                 | Schedule, todos, memory, calendar, and profile data could be treated as session/demo data instead of account-owned data.     | User-level data is stored under `data/users/<user_id>/`; thread-level state is scoped under the user.             | Fixed             |
| Thread state isolation         | Typed PM workflow state used `user_id` as the state key, so one chat could continue another chat's pending clarification.    | Thread-scoped state uses the current `thread_id` instead of the user id.                                          | Fixed             |
| Generic approval commands      | A bare `approve` or `reject` in thread B could execute a pending approval created in thread A.                               | Pending approvals are filtered by the current thread before generic approval or rejection.                        | Fixed             |
| Explicit approval IDs          | `approve <id>` or `reject <id>` could bypass thread isolation and execute an approval from another chat.                     | Explicit approval IDs are checked against the current thread before execution.                                    | Fixed             |
| Decision trace and audit views | Trace and audit endpoints accepted a thread id but queried by `user.id`, mixing events from multiple chats.                  | Endpoints query by the requested thread id inside the authenticated user boundary.                                | Fixed             |
| PM history routes              | The frontend still used legacy `/sessions`, `/sessions/{id}`, and `/master/sessions/{id}` routes.                            | The frontend uses `/personal-manager/sessions` and PM-specific message endpoints.                                 | Fixed             |
| PM transcript restore          | `fetchSession()` returned an empty message list, then later loaded only LangGraph checkpoints, missing typed workflow turns. | Typed PM turns are written to a conversation log and restored through `/personal-manager/sessions/{id}/messages`. | Fixed             |
| Demo turn cap                  | Authenticated users were still blocked by a six-turn "demo credits" frontend limit.                                          | The demo cap language and client-side hard stop were removed for real accounts.                                   | Fixed             |
| Mutating PM routes             | Cookie-authenticated POST/PUT/PATCH/DELETE routes lacked CSRF enforcement.                                                   | A CSRF middleware protects mutating PM routes.                                                                    | Fixed             |
| Logout CSRF                    | `/auth/logout` was exempt from CSRF because all `/auth/*` routes were exempt.                                                | Logout now requires a valid CSRF token before invalidating the session.                                           | Fixed             |
| CSRF token isolation           | CSRF tokens lived in a process-global set and were valid for any session.                                                    | Tokens are bound to the SHA-256 hash of the current session cookie.                                               | Fixed with caveat |
| CORS                           | `CORS_ORIGINS='*'` with credentials allowed arbitrary origins to call cookie-auth routes in cross-origin deployments.        | Wildcard origins do not allow credentials; credentialed CORS requires an explicit origin list.                    | Fixed             |
| Login/signup throttling        | Auth endpoints allowed unlimited attempts.                                                                                   | Per-IP sliding-window rate limiting is applied to login, signup, and demo account creation.                       | Fixed             |
| Signup enumeration             | Signup leaked existing accounts through different `409` vs `201` behavior.                                                   | Duplicate and successful signup use a neutral response path; infrastructure errors still return server errors.    | Fixed             |
| Signup infrastructure failures | The anti-enumeration path originally caught all exceptions and hid real failures as success.                                 | Only duplicate-email integrity errors are neutralized; unexpected failures return a generic 500.                  | Fixed             |
| Public demo account creation   | `/auth/demo` was unauthenticated and initially unthrottled while creating users and seeded files.                            | Demo creation is rate-limited and demo users expire.                                                              | Fixed             |
| Public demo cost control       | Public users could keep sending accepted turns until rate-limited.                                                          | New users receive 10 credits, demo users receive 5 credits, and each accepted chat turn consumes one credit.       | Fixed             |
| Demo seed failure              | A failed demo seed still returned a live session with an empty or partial demo account.                                      | Seed failures clean up the user row and data directory, then return a generic 500.                                | Fixed             |
| Legacy demo router             | `demo_web` exposed workspace and terminal routes through optional `GATEWAY_TOKEN`; blank token meant no auth.                | The router is disabled by default and fails closed if enabled without a token.                                    | Fixed by default  |
| Upcoming calendar feed         | Disabling the legacy demo router removed the `/personal-manager/upcoming` endpoint used by the visible calendar panel.       | `pm_app.py` now exposes an authenticated `/personal-manager/upcoming` route.                                      | Fixed             |
| Internal error leakage         | Some handlers returned `str(exc)` to clients, exposing internal paths and upstream error text.                               | Handlers log exceptions server-side and return generic client-facing errors.                                      | Fixed             |
| Deleted session transcripts    | Deleting a PM session removed thread metadata but left transcript data fetchable.                                            | Session message fetch verifies the thread exists, and deletion removes thread-scoped data.                        | Fixed             |
| New chat behavior              | The top-nav "New chat" button cleared UI state but reused the same backend thread id.                                        | The button creates a real new PM thread in personal-manager mode.                                                 | Fixed             |


## Remaining Follow-Ups


| Follow-up                         | Why it matters                                                                                                                                | Suggested action                                                                                        |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Update `SECURITY.md`              | It still reads like the old single-user/demo architecture and can make the project look inconsistent.                                         | Rewrite it to describe current cookie auth, CSRF, CORS, demo mode, and remaining limitations.           |
| Shared CSRF/rate-limit storage    | CSRF tokens are process-local, and rate-limit events are local SQLite state for one backend instance. This is fine for a single Render process but weak for multi-worker deployments. | Use Redis or another shared store before scaling beyond one backend process.                            |
| SQLite at-rest protection         | User data is stored in local SQLite/filesystem paths without encryption at rest.                                                              | Document this limitation or add encrypted storage for sensitive production use.                         |
| Legacy demo router cleanup        | The router is safe by default, but it still contains terminal/workspace features that are risky if misconfigured.                             | Keep `ENABLE_DEMO_WEB_ROUTES=0` in production, or remove the router entirely if it is no longer needed. |
| Google Calendar production polish | Demo users are blocked, but OAuth/calendar edge cases still need production-grade handling.                                                   | Add clearer reconnect flows, token refresh tests, and conflict-resolution UX.                           |
| Frontend legacy API cleanup       | Some legacy demo/master/session API helpers and Vite proxy entries may remain unused.                                                         | Remove dead helpers and dev proxies after confirming no UI path depends on them.                        |
| Public portfolio docs             | The project now has stronger architecture, but docs should explain the before/after decisions clearly.                                        | Add a short architecture section to the README and link to `SECURITY.md`.                               |


## Current Safety Position

For a portfolio demo, the current direction is solid: account isolation, thread isolation, approval isolation, CSRF enforcement, safe demo accounts, and generic error handling are all meaningful signals for an AI-engineer project.

For a real public production deployment, the project should still be treated as a v1 system. The highest-value next hardening work is shared auth/CSRF/rate-limit state, stricter deployment documentation, and cleanup of legacy demo/workspace code paths.

## Recommended Demo Configuration

Use this posture for a public portfolio demo:

```env
ENABLE_DEMO_WEB_ROUTES=0
COOKIE_SECURE=true
COOKIE_SAMESITE=none
CORS_ORIGINS=https://your-frontend-domain.example
```

Do not deploy with credentialed wildcard CORS. Do not enable legacy demo workspace routes unless a strong `GATEWAY_TOKEN` is set and the deployment is intentionally private.



The remaining polish items should be handled before sharing widely, especially the stale `SECURITY.md` and any unused legacy demo/frontend routes.
