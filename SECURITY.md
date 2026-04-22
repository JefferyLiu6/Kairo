# Security Policy

Kairo handles schedules, todos, habits, journal entries, private notes, approval
records, and long-term memory. Treat every deployment as personal-data handling
software, not as a generic chatbot.

## Sensitive Memory Policy

The assistant separates lower-risk shared memory from private Kairo memory.

- Sensitive facts, including medical, financial, legal, relationship, private
  goals/fears, daily routines, and journal-like reflections, should be stored in
  private Kairo storage rather than shared profile memory.
- Non-sensitive preferences, such as communication style or product preferences,
  may be stored as shared memory when useful for future conversations.
- The fallback prompt explicitly instructs the assistant not to expose raw
  private context unless the user specifically asks for it.
- Sensitive memory writes are routed through typed actions instead of relying on
  free-form model output.

## Approval Gates

State-changing actions are planned as typed `PMAction` objects and passed through
an approval policy before execution.

Actions requiring explicit approval include:

- deleting todos
- removing or updating schedule events
- modifying or cancelling recurring schedule occurrences
- private memory export
- private profile patching
- sensitive web-search requests

Approval requests are stored in SQLite with status, risk level, payload summary,
decision timestamps, and audit events. This keeps destructive or privacy-sensitive
actions out of the direct execution path.

## Prompt-Injection Handling

The deterministic workflow checks for common prompt-injection markers before
intent classification. Messages that look like embedded system/developer
instructions are refused instead of being passed to the agent fallback.

Examples of blocked patterns include:

- "ignore previous instructions"
- explicit `system:` / `assistant:` role markers
- XML-like role tags such as `<system>`
- "new instructions:" style prompt replacement attempts

This guard is intentionally conservative and is backed by adversarial regression
tests.

## Web-Search Privacy Guard

The assistant treats web search as a privacy boundary. Requests that may expose
private user context, such as health, finance, identity, location history,
immigration, or similarly sensitive details, are blocked behind a high-risk
approval action instead of being sent directly to an external search provider.

Non-sensitive web-search requests may fall through to safe coaching/search
behavior depending on configuration, but private details should never be placed
into external search queries without explicit user approval.

## Deployment Controls

The demo server supports:

- bearer-token auth via `GATEWAY_TOKEN`
- CORS origin restriction via `CORS_ORIGINS`
- per-IP rate limiting via `RATE_LIMIT_RPM`
- demo-session call limits via `DEMO_SESSION_LIMIT`
- signed Google OAuth state for calendar connection
- workspace file/run endpoints that stay disabled unless `WORKSPACE_DIR` is set
- terminal WebSocket auth using the same gateway token when auth is enabled

For public deployments, do not expose a privileged `GATEWAY_TOKEN` in a public
frontend bundle. Use a backend proxy, demo-only public endpoints, or another
server-side auth boundary.

Do not enable `WORKSPACE_DIR` or mount a writable workspace in an internet-facing
portfolio demo. Those endpoints are intended for local/private development
environments, not public untrusted traffic.

## Known Limitations

This project is portfolio/demo software, not a production multi-tenant service.

- There are no real user accounts or tenant isolation beyond session IDs.
- Local SQLite files are not encrypted at rest by this project.
- Demo sessions are suitable for sample data, not real private user data.
- Google Calendar sync is limited; full two-way conflict resolution is not
  implemented.
- The prompt-injection and privacy guards are regression-tested, but no static
  rule set can catch every adversarial input.
- The eval harness checks workflow behavior and safety policy shape; it does not
  fully prove prose quality or all model-fallback behavior.
- Browser build-time environment variables are public once bundled. Do not put
  backend secrets in `VITE_*` variables for a real public app.

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
