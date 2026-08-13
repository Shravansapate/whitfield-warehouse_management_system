"""Request-correlation boundary regressions."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from backend.core.apis.api import app


@pytest.mark.asyncio(loop_scope="session")
async def test_request_id_preserves_safe_correlation_value() -> None:
    """Return a safe caller correlation value unchanged."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/health/live", headers={"X-Request-ID": "scanner:reno-1048"}
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "scanner:reno-1048"


@pytest.mark.asyncio(loop_scope="session")
async def test_request_id_replaces_log_injection_characters() -> None:
    """Replace unsafe correlation text instead of allowing log injection."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/health/live", headers={"X-Request-ID": "unsafe value"}
        )

    assert response.status_code == 200
    generated = response.headers["X-Request-ID"]
    assert uuid.UUID(generated)
    assert generated != "unsafe value"
