"""Provider contracts, without credentials or network calls."""
import httpx
import pytest

from kairo.api import account_mail as mail
from kairo.storage.identity import IdentityUnavailable


@pytest.fixture
def brevo(monkeypatch):
    monkeypatch.setenv("AUTH_EMAIL_PROVIDER", "brevo")
    monkeypatch.setenv("AUTH_PUBLIC_URL", "https://kairo-mu-two.vercel.app")
    monkeypatch.setenv("AUTH_EMAIL_FROM", "accounts@example.com")
    monkeypatch.setenv("BREVO_API_KEY", "fake-test-key")
    for name in ("SMTP_HOST", "SMTP_FROM", "SMTP_PORT", "SMTP_SECURITY", "SMTP_USERNAME", "SMTP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)


def test_brevo_sends_https_without_smtp(brevo, monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(201, json={"messageId": "test"})
    monkeypatch.setattr(mail.httpx, "post", post)
    def no_smtp(*args, **kwargs):
        raise AssertionError("SMTP must never be used in Brevo mode")
    monkeypatch.setattr(mail.smtplib, "SMTP", no_smtp)
    body = "Verify <this> & continue\nhttps://kairo-mu-two.vercel.app/verify-email#token=test"
    mail.send_message("person@example.com", "Verify", body)
    assert len(calls) == 1
    url, options = calls[0]
    assert url == "https://api.brevo.com/v3/smtp/email"
    assert options["headers"]["api-key"] == "fake-test-key"
    assert options["timeout"] == 10
    assert options["follow_redirects"] is False
    payload = options["json"]
    assert payload["sender"] == {"name": "Kairo", "email": "accounts@example.com"}
    assert payload["to"] == [{"email": "person@example.com"}]
    assert payload["textContent"] == body
    assert "&lt;this&gt; &amp;" in payload["htmlContent"]
    assert "<this>" not in payload["htmlContent"]
    assert 'href="https://kairo-mu-two.vercel.app/verify-email#token=test"' in payload["htmlContent"]
    assert "fake-test-key" not in repr(mail.configuration())


@pytest.mark.parametrize("key", ["AUTH_EMAIL_FROM", "BREVO_API_KEY", "AUTH_PUBLIC_URL"])
def test_brevo_requires_configuration(brevo, monkeypatch, key):
    monkeypatch.delenv(key)
    with pytest.raises(IdentityUnavailable):
        mail.configuration()


@pytest.mark.parametrize("status", [200, 301, 400, 401, 429, 500])
def test_provider_failure_is_sanitized_and_never_retried(brevo, monkeypatch, status):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return httpx.Response(status, text="private-recipient-and-token")
    monkeypatch.setattr(mail.httpx, "post", post)
    with pytest.raises(IdentityUnavailable) as error:
        mail.send_message("person@example.com", "Verify", "private-token")
    assert "private" not in str(error.value)
    assert len(calls) == 1


def test_timeout_does_not_expose_request_or_key(brevo, monkeypatch):
    def post(*args, **kwargs):
        raise httpx.ConnectTimeout("private-key-and-token")
    monkeypatch.setattr(mail.httpx, "post", post)
    with pytest.raises(IdentityUnavailable) as error:
        mail.send_message("person@example.com", "Verify", "private-token")
    assert "private" not in str(error.value)


@pytest.mark.parametrize("value", ["invalid-provider", "resend"])
def test_unknown_provider_fails_closed(brevo, monkeypatch, value):
    monkeypatch.setenv("AUTH_EMAIL_PROVIDER", value)
    with pytest.raises(IdentityUnavailable):
        mail.configuration()


def test_malformed_public_url_is_a_configuration_error(brevo, monkeypatch):
    monkeypatch.setenv("AUTH_PUBLIC_URL", "https://[")
    with pytest.raises(IdentityUnavailable):
        mail.configuration()
