"""Application settings, read only from environment variables (and an optional .env file)."""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET_KEY = "dev-insecure-secret-key-change-me"  # noqa: S105  (refused when ENV=prod)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./jobtrack.db"
    secret_key: str = DEV_SECRET_KEY
    env: Literal["dev", "test", "prod"] = "dev"
    allow_registration: bool = True

    # Not required by the spec, but handy to tune without code changes.
    parse_rate_limit_per_hour: int = 20
    display_timezone: str = "Europe/Prague"

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        # Neon/Supabase/Render hand out "postgres://" or "postgresql://" URLs;
        # SQLAlchemy needs an explicit driver for psycopg 3.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value.removeprefix(prefix)
        return value

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.is_prod and settings.secret_key == DEV_SECRET_KEY:
        raise RuntimeError("SECRET_KEY must be set to a random value when ENV=prod")
    return settings
