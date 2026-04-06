"""
TakeoffAI — Application configuration via pydantic-settings.
Reads from environment variables and .env file.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_DATA_DIR = Path(__file__).parent / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    anthropic_api_key: str = ""
    api_key: str = ""
    default_overhead_pct: float = 20.0
    default_margin_pct: float = 12.0
    app_env: str = "development"
    api_port: int = 8000
    claude_model: str = "claude-sonnet-4-6"
    wiki_model: str = "claude-haiku-4-5"
    db_path: str = str(_DATA_DIR / "takeoffai.db")


settings = Settings()
