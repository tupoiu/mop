from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.config import Settings, load_settings


def _clean_env(monkeypatch):
    for var in ("APP_AUTH_TOKEN", "ANTHROPIC_API_KEY", "CONVERSATIONS_DB_PATH", "ANTHROPIC_MODEL"):
        monkeypatch.delenv(var, raising=False)


def test_load_settings_with_required_vars(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("APP_AUTH_TOKEN", "tok")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

    settings = load_settings()

    assert settings.app_auth_token == "tok"
    assert settings.anthropic_api_key == "key"
    assert settings.conversations_db_path == Path("conversations.db")
    assert settings.anthropic_model is None


def test_load_settings_with_optional_overrides(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("APP_AUTH_TOKEN", "tok")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("CONVERSATIONS_DB_PATH", "/data/conv.db")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    settings = load_settings()

    assert settings.conversations_db_path == Path("/data/conv.db")
    assert settings.anthropic_model == "claude-sonnet-4-6"


def test_load_settings_missing_token_raises(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    with pytest.raises(RuntimeError, match="APP_AUTH_TOKEN"):
        load_settings()


def test_load_settings_empty_token_raises(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("APP_AUTH_TOKEN", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    with pytest.raises(RuntimeError, match="APP_AUTH_TOKEN"):
        load_settings()


def test_load_settings_missing_api_key_raises(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("APP_AUTH_TOKEN", "tok")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        load_settings()


def test_settings_is_frozen():
    settings = Settings(
        app_auth_token="a",
        anthropic_api_key="b",
        conversations_db_path=Path("x"),
        anthropic_model=None,
    )
    with pytest.raises(FrozenInstanceError):
        settings.app_auth_token = "c"  # type: ignore[misc]
