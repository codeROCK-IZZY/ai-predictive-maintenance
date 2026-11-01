"""Application configuration.

All settings come from environment variables prefixed with ``PDM_`` (or a local
``.env`` file, which is git-ignored). Nothing secret has a default value: the
app refuses to start without ``PDM_JWT_SECRET``.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PDM_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://pdm:pdm@localhost:5432/pdm"

    # --- security ---
    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    login_rate_limit_per_minute: int = 10
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # --- storage ---
    artifact_dir: Path = Path("./var/artifacts")
    artifact_s3_bucket: str | None = None
    upload_dir: Path = Path("./var/uploads")
    max_upload_mb: int = 50
    max_batch_readings: int = 10_000

    # --- ML ---
    prediction_horizon_days: int = 7
    model_name: str = "failure-risk"

    # --- logging ---
    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("jwt_secret")
    @classmethod
    def _secret_strength(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("PDM_JWT_SECRET must be at least 32 characters")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
