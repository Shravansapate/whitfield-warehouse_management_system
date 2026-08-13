"""Authentication, authorization, and warehouse-boundary integration tests."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_authentication_rejects_missing_disabled_and_underprivileged_users(
    api_client: AsyncClient,
    seeded_context,
) -> None:
    """Require valid active credentials and the role demanded by a route.

    Args:
        api_client: In-process HTTP client for the real application.
        seeded_context: Seeded users, tokens, and warehouse state.
    """
    missing = await api_client.get("/api/v1/orders")
    assert missing.status_code == 401
    assert missing.json()["code"] == "AUTHENTICATION_REQUIRED"

    disabled = await api_client.get(
        "/api/v1/auth/me", headers=seeded_context.headers("disabled")
    )
    assert disabled.status_code == 403
    assert disabled.json()["code"] == "USER_DISABLED"

    role_denied = await api_client.post(
        f"/api/v1/damaged-returns/{uuid.uuid4()}/complete",
        json={"return_tracking_number": "RETURN-DENIED"},
        headers=seeded_context.headers("staff", idempotency_key="role-denied-command"),
    )
    assert role_denied.status_code == 403
    assert role_denied.json()["code"] == "ROLE_FORBIDDEN"


async def test_non_owner_cannot_list_or_read_another_warehouse(
    api_client: AsyncClient,
    seeded_context,
) -> None:
    """Keep another warehouse invisible to a non-owner actor.

    Args:
        api_client: In-process HTTP client for the real application.
        seeded_context: Seeded users, tokens, and warehouse state.
    """
    foreign_receipt = await api_client.post(
        "/api/v1/inbound-receipts",
        json={
            "warehouse_id": str(seeded_context.columbus_id),
            "tracking_number": "CMH-SCOPE-001",
            "sender_name": "Scope Vendor",
            "sender_return_address": "1 Test Way, Columbus, OH",
        },
        headers=seeded_context.headers("owner"),
    )
    assert foreign_receipt.status_code == 201
    foreign_receipt_id = foreign_receipt.json()["id"]

    selected_foreign = await api_client.get(
        "/api/v1/inbound-receipts",
        params={"warehouse_id": str(seeded_context.columbus_id)},
        headers=seeded_context.headers("staff"),
    )
    assert selected_foreign.status_code == 403
    assert selected_foreign.json()["code"] == "WAREHOUSE_FORBIDDEN"

    direct_foreign = await api_client.get(
        f"/api/v1/inbound-receipts/{foreign_receipt_id}",
        headers=seeded_context.headers("staff"),
    )
    assert direct_foreign.status_code == 403
    assert direct_foreign.json()["code"] == "WAREHOUSE_FORBIDDEN"

    scoped_list = await api_client.get(
        "/api/v1/inbound-receipts", headers=seeded_context.headers("staff")
    )
    assert scoped_list.status_code == 200
    assert foreign_receipt_id not in {receipt["id"] for receipt in scoped_list.json()}
