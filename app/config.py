import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_auth_token: str
    anthropic_api_key: str
    conversations_db_path: Path
    anthropic_model: str | None


def load_settings() -> Settings:
    token = (os.environ.get("APP_AUTH_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("APP_AUTH_TOKEN is required but is unset or empty")

    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required but is unset or empty")

    db_path_value = (os.environ.get("CONVERSATIONS_DB_PATH") or "conversations.db").strip()
    db_path = Path(db_path_value or "conversations.db")

    model_value = (os.environ.get("ANTHROPIC_MODEL") or "").strip()
    model: str | None = model_value or None

    return Settings(
        app_auth_token=token,
        anthropic_api_key=api_key,
        conversations_db_path=db_path,
        anthropic_model=model,
    )
