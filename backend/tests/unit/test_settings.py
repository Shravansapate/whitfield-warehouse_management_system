"""Deployment configuration safety tests."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from backend.core.config.settings import Settings


def production_settings(**overrides: Any) -> Settings:
    """Build a production candidate with every required secure baseline.

    Args:
        overrides: Individual settings used by a negative test.

    Returns:
        Validated production settings.
    """
    values: dict[str, Any] = {
        "environment": "production",
        "database_url": "postgresql+asyncpg://wms_runtime:secret@db/whitfield_wms",
        "jwt_secret_key": "production-test-secret-that-is-not-deployed",
        "cors_origins": "https://wms.example.com",
        "carrier_provider": "unconfigured",
    }
    values.update(overrides)
    return Settings(**values)


def test_secure_production_configuration_is_accepted() -> None:
    """Allow explicit secrets, CORS, PostgreSQL, and a fail-closed carrier."""
    settings = production_settings()

    assert settings.environment == "production"
    assert settings.allowed_origins == ["https://wms.example.com"]
    assert settings.carrier_provider == "unconfigured"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"jwt_secret_key": "development-only-change-this-secret-key"},
            "non-default JWT_SECRET_KEY",
        ),
        (
            {"database_url": "postgresql+asyncpg://wms:change-me@db/whitfield_wms"},
            "explicit DATABASE_URL credential",
        ),
        ({"cors_origins": "*"}, "cannot contain a wildcard"),
        ({"carrier_provider": "fake"}, "cannot use the fake carrier"),
    ],
)
def test_production_rejects_insecure_defaults(
    overrides: dict[str, object], message: str
) -> None:
    """Fail startup instead of silently accepting an unsafe production value.

    Args:
        overrides: Unsafe production value under test.
        message: Expected validation explanation.
    """
    with pytest.raises(ValidationError, match=message):
        production_settings(**overrides)


def test_non_async_postgresql_database_is_rejected() -> None:
    """Prevent SQLite or a synchronous SQLAlchemy driver from reaching startup."""
    with pytest.raises(ValidationError, match="postgresql\\+asyncpg"):
        Settings(database_url="sqlite:///whitfield.db")
