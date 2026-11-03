from __future__ import annotations

from assistant.shared.llm_env import load_default_llm_from_env


def _clear_llm_env(monkeypatch) -> None:
    for key in (
        "OLLAMA",
        "OLLAMA_API_KEY",
        "OLLAMA_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_openai_key_beats_incidental_ollama_base_url(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("MODEL", "gpt-4o-mini")

    config = load_default_llm_from_env()

    assert config["provider"] == "openai"
    assert config["api_key"] == "sk-test"
    assert config["base_url"] == "https://api.openai.com/v1"


def test_explicit_ollama_flag_beats_openai_key(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("OLLAMA", "1")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    config = load_default_llm_from_env()

    assert config["provider"] == "ollama"
    assert config["api_key"] is None
    assert config["base_url"] == "http://127.0.0.1:11434"


def test_bare_ollama_base_url_selects_local_ollama(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

    config = load_default_llm_from_env()

    assert config["provider"] == "ollama"
    assert config["api_key"] is None
    assert config["base_url"] == "http://127.0.0.1:11434"
