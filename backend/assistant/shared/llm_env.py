"""
Default LLM settings from the environment — aligned with history_version/src/core/config.ts.

Ollama Cloud (same as TS when OLLAMA_API_KEY is set):
  OLLAMA_API_KEY   → provider=ollama, base_url=https://ollama.com, Bearer auth
  MODEL            → defaults to gpt-oss:120b if unset

Local Ollama (TS: OLLAMA=1 or OLLAMA_BASE_URL):
  OLLAMA=1 / OLLAMA_BASE_URL  → provider=ollama, no API key, native port 11434
  MODEL             → defaults to llama3.2 if unset

Anthropic / OpenAI fallbacks match TS ordering.

Timeout / retry (env vars):
  LLM_TIMEOUT     seconds before an LLM call is aborted (default: 120)
  LLM_MAX_RETRIES max attempts on transient errors   (default: 3)
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _truthy(name: str) -> bool:
    v = os.environ.get(name, "").lower()
    return v in ("1", "true", "yes")


def llm_timeout() -> int:
    """LLM request timeout in seconds (LLM_TIMEOUT env var, default 120)."""
    try:
        return max(5, int(os.environ.get("LLM_TIMEOUT", "120")))
    except ValueError:
        return 120


def llm_max_retries() -> int:
    """Maximum LLM retry attempts (LLM_MAX_RETRIES env var, default 3)."""
    try:
        return max(1, int(os.environ.get("LLM_MAX_RETRIES", "3")))
    except ValueError:
        return 3


def with_retry(fn: Callable[[], T], max_attempts: int | None = None) -> T:
    """
    Call fn() up to max_attempts times, retrying on transient errors.
    Delays: 1s, 2s, 4s, … (exponential backoff, capped at 30s).
    Raises the last exception if all attempts fail.
    """
    attempts = max_attempts if max_attempts is not None else llm_max_retries()
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if i < attempts - 1:
                wait = min(2 ** i, 30)
                logger.warning("LLM call failed (attempt %d/%d): %s — retrying in %ds", i + 1, attempts, exc, wait)
                time.sleep(wait)
            else:
                logger.error("LLM call failed after %d attempts: %s", attempts, exc)
    raise last_exc  # type: ignore[misc]


def load_default_llm_from_env() -> dict[str, Any]:
    ollama_cloud_key = os.environ.get("OLLAMA_API_KEY", "").strip()
    use_ollama = _truthy("OLLAMA") or bool(os.environ.get("OLLAMA_BASE_URL", "").strip())

    if ollama_cloud_key:
        base = os.environ.get("OLLAMA_BASE_URL", "https://ollama.com").strip() or "https://ollama.com"
        model = os.environ.get("MODEL", "").strip() or "gpt-oss:120b"
        return {
            "provider": "ollama",
            "api_key": ollama_cloud_key,
            "base_url": base.rstrip("/"),
            "model": model,
        }

    if use_ollama:
        base = (os.environ.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
        model = os.environ.get("MODEL", "").strip() or "llama3.2"
        return {
            "provider": "ollama",
            "api_key": None,
            "base_url": base,
            "model": model,
        }

    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return {
            "provider": "anthropic",
            "api_key": os.environ["ANTHROPIC_API_KEY"].strip(),
            "base_url": None,
            "model": os.environ.get("MODEL", "").strip() or "claude-3-5-haiku-20241022",
        }

    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if openai_key:
        base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        return {
            "provider": "openai",
            "api_key": openai_key,
            "base_url": base or None,
            "model": os.environ.get("MODEL", "").strip() or "gpt-4o-mini",
        }

    # No LLM-related env — HTTP handlers use their own Pydantic defaults.
    return {}


def ollama_chat_kwargs(model: str, base_url: Optional[str], api_key: Optional[str]) -> dict[str, Any]:
    """Build ChatOllama kwargs; Ollama Cloud requires Bearer token via client_kwargs."""
    kwargs: dict[str, Any] = {"model": model}
    if base_url:
        kwargs["base_url"] = base_url.rstrip("/")
    if api_key:
        kwargs["client_kwargs"] = {"headers": {"Authorization": f"Bearer {api_key}"}}
    return kwargs


def has_api_key(provider: str, api_key: Optional[str]) -> bool:
    """Whether a live credential is available for this provider.

    Local ollama works without a key; OpenAI/Anthropic/Ollama-cloud need one
    either passed in or in the environment. Callers use this to skip LLM
    work gracefully when running in a credential-free environment (tests,
    CI, demos) instead of letting the SDK raise on client construction.
    """
    if api_key:
        return True
    if provider == "ollama" and not os.environ.get("OLLAMA_API_KEY"):
        return True
    env_var = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "ollama": "OLLAMA_API_KEY",
    }.get(provider)
    return bool(env_var and os.environ.get(env_var))


def build_llm(provider: str, model: str, api_key: Optional[str], base_url: Optional[str]):
    """
    Build the appropriate LangChain LLM with timeout wired in.
    Centralises LLM construction so all agents get consistent behaviour.
    """
    timeout = llm_timeout()

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        kwargs = ollama_chat_kwargs(model, base_url, api_key)
        kwargs["request_timeout"] = timeout
        return ChatOllama(**kwargs)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        kwargs: dict[str, Any] = {"model": model, "max_tokens": 4096, "timeout": timeout}
        if api_key:
            kwargs["api_key"] = api_key
        return ChatAnthropic(**kwargs)

    # Default: OpenAI-compatible
    from langchain_openai import ChatOpenAI
    kwargs = {"model": model, "timeout": timeout}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)
