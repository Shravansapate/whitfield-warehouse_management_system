"""Environment-driven application settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, Self

from pydantic import EmailStr, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime configuration for the Whitfield WMS."""

    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Whitfield Fulfillment WMS"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = (
        "postgresql+asyncpg://wms_app:change-me@127.0.0.1:5432/whitfield_wms"
    )
    jwt_secret_key: str = Field(
        default="development-only-change-this-secret-key", min_length=32
    )
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = Field(default=30, ge=5, le=1440)
    refresh_token_days: int = Field(default=7, ge=1, le=90)
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    carrier_provider: Literal["fake", "unconfigured"] = "fake"
    fake_carrier_base_url: str = "http://127.0.0.1:8000/dev-labels"
    seed_owner_name: str = Field(
        default="Whitfield Owner", min_length=2, max_length=160
    )
    seed_owner_email: EmailStr | None = None
    seed_owner_password: SecretStr | None = Field(
        default=None,
        min_length=10,
        max_length=256,
    )
    low_stock_scheduler_enabled: bool = False
    low_stock_scheduler_interval_seconds: int = Field(
        default=900,
        ge=60,
        le=86_400,
    )
    low_stock_scheduler_run_immediately: bool = True
    # Voice assistant (optional — app starts without these; voice degrades gracefully)
    deepgram_api_key: str | None = None
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash"

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        """Require the async PostgreSQL SQLAlchemy URL.

        Rejects accidental SQLite or synchronous database configuration.

        Args:
            value: Candidate SQLAlchemy database URL.

        Returns:
            Validated database URL.

        Raises:
            ValueError: If the URL does not use PostgreSQL with asyncpg.
        """
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use postgresql+asyncpg://")
        return value

    @model_validator(mode="after")
    def reject_insecure_production_defaults(self) -> Self:
        """Reject development credentials and wildcard CORS in production.

        Returns:
            Validated settings instance.

        Raises:
            ValueError: If production is configured with unsafe defaults.
        """
        if self.environment.casefold() != "production":
            return self
        if self.jwt_secret_key == "development-only-change-this-secret-key":
            raise ValueError("Production requires a non-default JWT_SECRET_KEY")
        if "change-me" in self.database_url:
            raise ValueError("Production requires an explicit DATABASE_URL credential")
        if "*" in self.allowed_origins:
            raise ValueError("Production CORS_ORIGINS cannot contain a wildcard")
        if self.carrier_provider == "fake":
            raise ValueError(
                "Production cannot use the fake carrier; set CARRIER_PROVIDER=unconfigured until a real adapter is installed"
            )
        return self

    @property
    def allowed_origins(self) -> list[str]:
        """Return the configured browser CORS allow-list.

        Splits the comma-separated environment value into normalized origins.

        Returns:
            List of allowed origins.
        """
        return [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    """Return the cached validated application settings.

    Reads environment variables once per process.

    Returns:
        Application settings singleton.
    """
    return Settings()
