# Account security setup

Real accounts must verify their email before Kairo issues a session. Temporary
demo accounts remain separate and cannot enroll an authenticator.

## Deployment configuration

Set these backend environment variables in the deployment's secret/configuration
store. Local development can use `apps/api/.env` (never commit it).

| Variable | Purpose |
| --- | --- |
| `AUTH_PUBLIC_URL` | Frontend origin, e.g. `https://app.example.com`. No path, query or fragment. HTTP is accepted only for localhost development. |
| `SMTP_HOST` | Your mail provider's SMTP host. |
| `SMTP_PORT` | Usually `587` for STARTTLS or `465` for implicit TLS. |
| `SMTP_SECURITY` | `starttls` (default) or `ssl`. Certificate validation is mandatory. |
| `SMTP_FROM` | Provider-verified sender address. |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | SMTP credentials, when required by your provider. |
| `AUTH_ENCRYPTION_KEY` | Persistent Fernet key for MFA secrets. Keep it separate from database backups. |

Generate the encryption key once in the installed backend environment:

```bash
cd apps/api
.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Store that value securely; every worker must use the same key. Never regenerate it
on restart. Losing the key prevents decryption of enrolled authenticators. Restoring
the original key restores access; password recovery does not bypass MFA. Key
rotation needs a controlled decrypt/re-encrypt migration, not simply replacement
of the environment variable.

Keep `COOKIE_SECURE=true` on HTTPS deployments, configure exact `CORS_ORIGINS`,
and put `DATA_DIR` on durable storage. All workers need the same `users.db` and
`rate_limits.db`. Independent replicas need a shared database before scaling.

SMTP is not configured by this code change. Provision a sender with your provider,
configure its domain authentication, set the variables above, and restart the backend.
Missing SMTP configuration returns a generic 503 for signup/email requests; missing
or incorrect encryption keys fail MFA operations closed. No secrets are generated
into the repository, and there is no production debug mailbox or token-return endpoint.

## User flows

1. **Signup and verify:** create an account, open the verification email, choose and
   confirm a password, then sign in. Choosing the password at verification prevents
   someone who pre-registered another person's email from keeping a known password.
   Links expire after 60 minutes; requesting another invalidates the previous link.
2. **Password recovery:** choose **Forgot password?**, request a link, and set a new
   password. Links expire after 30 minutes and work once. All existing sessions and
   outstanding account links are invalidated. MFA stays enabled. Recovery also proves
   email ownership for an unverified account.
3. **MFA enrollment:** open **Account security** using the gear in the app navigation,
   enter the current password, and add the setup key to a TOTP authenticator using
   time-based codes (SHA-1, six digits, 30 seconds). An `otpauth` link also opens an
   installed authenticator on the same device. Enter its code within 10 minutes to
   finish enrollment, then save all 10 recovery codes in a password manager.
4. **MFA login:** submit email, password, and an authenticator or unused recovery code.
   No authenticated session is issued until both factors pass. A code accepted for
   enrollment or login cannot be reused; wait for the next code if necessary.
5. **Replace codes / disable MFA:** re-enter the password and a current authenticator
   or unused recovery code in Account security. Replacing codes invalidates the old
   set. Each change revokes other-device sessions and outstanding account links.
6. **Lost authenticator:** sign in with a saved recovery code, disable the old factor,
   and enroll a new one. If both the authenticator and all recovery codes are lost,
   there is no automatic email-only bypass. An operator would need a separately
   designed identity-proofing recovery process; do not manually disable MFA on an
   unverified email request.

Security changes generate notification emails. Account link requests give the same
response for registered and unknown addresses. Delivery runs after that response so
SMTP failures do not disclose whether an account exists. The server logs a generic
failure without the recipient or token; users can resend later. There is no durable
mail queue: a process crash or provider outage can lose an email. Monitor delivery
failures and add an outbox/worker before requiring guaranteed retry delivery.

## Upgrade and rollback

Back up the durable `DATA_DIR` before upgrading. Startup creates additive security
tables without deleting users, passwords, credits, or app data. Existing real
accounts have no previously verified-email evidence, so their existing sessions
are rejected until verification. Direct users to **Resend verification email**;
verification sets their password and removes old sessions. Run SMTP acceptance
tests before directing existing users to the upgrade.

Do not roll back to a version without verification/MFA enforcement after enrollment:
that would bypass these controls. Use a forward fix or disable real-account login
while recovering. Restore both database backups and the matching encryption key.

## Security and validation

Tokens have 256 bits of random entropy; the database stores SHA-256 hashes only.
Recovery codes each contain 128 random bits and are stored as hashes. TOTP seeds
are encrypted with authenticated encryption and bound to the user ID. Code
redemption, credential changes and session creation use SQLite transactions, so
concurrent requests cannot redeem the same token or code twice. TOTP accepts one
30-second step of clock skew in either direction and tracks the last accepted step.
Keep the server clock synchronized.

Email links put tokens in the URL fragment so servers and referrers do not receive
them. The frontend clears the fragment on load, holds the token in memory, and
requires a form submission; simply opening or scanning a link does not redeem it.
Refreshing that page clears the in-memory token; reopen the original email link.
Password/token bodies and authenticator seeds must not be captured by request-body
logging, analytics, tracing, or error-reporting integrations.

Public email/token routes require JSON and trusted browser Origins. Authenticated
MFA operations require CSRF, password re-entry, and (when enabled) a second factor.
Email, login, token redemption, and MFA management have persistent rate limits.

Automated tests cover RFC 6238 vectors, expiration, purpose separation, concurrent
redemption, replay prevention, migration, encrypted/hashed storage, CSRF, origin
checks, rate limits, SMTP TLS, and password resets preserving MFA. Before deployment,
run a real SMTP acceptance test with a controlled inbox and verify delivery, links,
HTTPS cookies, enrollment, logout/login, and recovery on the actual domain.
