"""
TakeoffAI — Application configuration via pydantic-settings.
Reads from environment variables and .env file.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    anthropic_api_key: str = ""
    default_overhead_pct: float = 20.0
    default_margin_pct: float = 12.0
    app_env: str = "development"
    api_port: int = 8000


settings = Settings()
