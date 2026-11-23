"""HTTPS or SMTP delivery for account mail. Never log recipients, secrets or message bodies."""
from __future__ import annotations

import logging
import os
import re
from html import escape

import httpx
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlsplit

from kairo.storage.identity import IdentityUnavailable, issue_token, status
from kairo.storage.user_store import get_user_by_email

_log = logging.getLogger(__name__)


def configuration() -> dict:
    origin = os.environ.get("AUTH_PUBLIC_URL", "").strip().rstrip("/")
    provider = os.environ.get("AUTH_EMAIL_PROVIDER", "smtp").strip().lower()
    sender = (os.environ.get("AUTH_EMAIL_FROM") or os.environ.get("SMTP_FROM", "")).strip()
    try:
        parsed = urlsplit(origin)
        valid_origin = (parsed.scheme == "https" or
                        (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")))
        if (not sender or not valid_origin or not parsed.netloc or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path
                or "\n" in sender or "\r" in sender):
            raise ValueError
    except ValueError:
        raise IdentityUnavailable("Account email is not configured") from None
    config = {"origin": origin, "sender": sender, "provider": provider}
    if provider == "brevo":
        key = os.environ.get("BREVO_API_KEY", "").strip()
        if (not key or "\n" in key or "\r" in key
                or not re.fullmatch(r"[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+", sender)):
            raise IdentityUnavailable("Account email is not configured")
        return config
    if provider != "smtp":
        raise IdentityUnavailable("Unsupported account email provider")
    host = os.environ.get("SMTP_HOST", "").strip()
    security = os.environ.get("SMTP_SECURITY", "starttls").strip().lower()
    if not host or security not in ("starttls", "ssl"):
        raise IdentityUnavailable("Account email is not configured")
    try:
        port = int(os.environ.get("SMTP_PORT", "465" if security == "ssl" else "587"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise IdentityUnavailable("Account email is not configured") from None
    return {**config, "host": host, "port": port, "security": security}


def _send_brevo(config: dict, recipient: str, subject: str, body: str) -> None:
    # Fixed HTTPS endpoint, certificate verification, no redirects or implicit retries.
    # A provider error response may contain personal data: never log its body.
    lines = []
    for line in body.splitlines():
        safe = escape(line)
        if line.startswith(config["origin"] + "/") and not any(c.isspace() for c in line):
            safe = f'<a href="{safe}">{safe}</a>'
        lines.append(safe)
    html = "<html><body>" + "<br>".join(lines) + "</body></html>"
    try:
        response = httpx.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": os.environ["BREVO_API_KEY"].strip(), "Accept": "application/json"},
            json={"sender": {"name": "Kairo", "email": config["sender"]},
                  "to": [{"email": recipient}], "subject": subject,
                  "textContent": body, "htmlContent": html},
            timeout=10, follow_redirects=False,
        )
    except httpx.HTTPError:
        raise IdentityUnavailable("Account email delivery failed") from None
    if response.status_code != 201:
        raise IdentityUnavailable(f"Account email provider rejected delivery (HTTP {response.status_code})")


def send_message(recipient: str, subject: str, body: str) -> None:
    config = configuration()
    if config["provider"] == "brevo":
        _send_brevo(config, recipient, subject, body)
        return
    message = EmailMessage()
    message["From"] = config["sender"]
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    context = ssl.create_default_context()
    if config["security"] == "ssl":
        transport = smtplib.SMTP_SSL(config["host"], config["port"], timeout=10, context=context)
    else:
        transport = smtplib.SMTP(config["host"], config["port"], timeout=10)
    with transport as smtp:
        if config["security"] == "starttls":
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        username = os.environ.get("SMTP_USERNAME", "")
        if username:
            smtp.login(username, os.environ.get("SMTP_PASSWORD", ""))
        smtp.send_message(message)


def send_account_link(data_dir: str, email: str, purpose: str) -> None:
    """Run after the uniform HTTP response; users can resend after a delivery failure."""
    try:
        user = get_user_by_email(data_dir, email)
        if user is None or user.is_demo:
            return
        if purpose == "verify" and status(data_dir, user.id)["email_verified"]:
            return
        config = configuration()
        token = issue_token(data_dir, user.id, purpose)
        path = "verify-email" if purpose == "verify" else "reset-password"
        # Fragments never reach proxy/access logs or Referer headers.
        link = f"{config['origin']}/{path}#token={token}"
        title = "Verify your Kairo email" if purpose == "verify" else "Reset your Kairo password"
        duration = "60" if purpose == "verify" else "30"
        send_message(user.email, title,
                     f"{title}\n\n{link}\n\nThis single-use link expires in {duration} minutes. "
                     "Open it and choose a password to continue. If you did not request this, ignore this email. "
                     "An enabled authenticator is still required after a password reset.")
    except Exception:
        # Delivery exceptions can contain addresses or message data: do not print them.
        _log.error("Account email delivery failed; user may request another link")


def send_security_notice(email: str, message: str) -> None:
    try:
        send_message(email, "Kairo account security changed", message)
    except Exception:
        _log.error("Account security notification delivery failed")
