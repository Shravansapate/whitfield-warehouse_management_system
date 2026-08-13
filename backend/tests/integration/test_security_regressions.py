"""Audit-source, replay-scope, and owner-safety regression tests."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.commons.auth import CurrentUser, get_current_user
from backend.core.apis.api import app
from backend.core.models.access import User
from backend.core.models.enums import UserRole
from backend.core.models.receiving import InboundReceipt
from backend.core.models.reliability import AuditLog

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.parametrize("spoofed_source", ["system", "voice", "automation"])
async def test_ordinary_http_routes_reject_privileged_audit_sources(
    spoofed_source: str,
    api_client: AsyncClient,
    seeded_context,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reject client attempts to claim a privileged internal audit source.

    Args:
        spoofed_source: Internal-only source supplied by the untrusted client.
        api_client: In-process HTTP client for the real application.
        seeded_context: Seeded users and warehouse state.
        session_factory: Factory for independent PostgreSQL verification sessions.
    """
    headers = seeded_context.headers("staff")
    headers["X-Source"] = spoofed_source
    response = await api_client.post(
        "/api/v1/inbound-receipts",
        json={
            "tracking_number": f"SOURCE-SPOOF-{spoofed_source}",
            "sender_name": "Untrusted Source Client",
            "sender_return_address": "1 Spoofing Way, Reno, NV",
        },
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    async with session_factory() as session:
        receipt_count = await session.scalar(select(func.count(InboundReceipt.id)))
        audit_count = await session.scalar(select(func.count(AuditLog.id)))
        assert receipt_count == 0
        assert audit_count == 0


async def test_receipt_idempotent_replay_rechecks_current_warehouse_scope(
    api_client: AsyncClient,
    seeded_context,
) -> None:
    """Deny a stored receipt replay after its staff actor moves warehouses.

    Args:
        api_client: In-process HTTP client for the real application.
        seeded_context: Seeded users, products, tokens, and warehouse state.
    """
    receipt = await api_client.post(
        "/api/v1/inbound-receipts",
        json={
            "tracking_number": "REPLAY-SCOPE-RECEIPT-001",
            "sender_name": "Replay Scope Vendor",
            "sender_return_address": "20 Scope Lane, Reno, NV",
        },
        headers=seeded_context.headers("staff"),
    )
    assert receipt.status_code == 201
    receipt_id = receipt.json()["id"]
    payload = {
        "product_id": str(seeded_context.product_a_id),
        "quantity_received": 2,
        "quantity_accepted": 2,
        "quantity_damaged": 0,
    }
    command_headers = seeded_context.headers(
        "staff", idempotency_key="receipt-replay-before-reassignment"
    )
    first = await api_client.post(
        f"/api/v1/inbound-receipts/{receipt_id}/items",
        json=payload,
        headers=command_headers,
    )
    assert first.status_code == 200

    reassigned = await api_client.put(
        f"/api/v1/users/{seeded_context.staff_id}/warehouse-assignment",
        json={"warehouse_id": str(seeded_context.columbus_id)},
        headers=seeded_context.headers("owner"),
    )
    assert reassigned.status_code == 200
    assert reassigned.json()["warehouse_id"] == str(seeded_context.columbus_id)

    replay = await api_client.post(
        f"/api/v1/inbound-receipts/{receipt_id}/items",
        json=payload,
        headers=command_headers,
    )
    assert replay.status_code == 403
    assert replay.json()["code"] == "WAREHOUSE_FORBIDDEN"


async def test_sole_owner_cannot_disable_or_remove_own_owner_access(
    api_client: AsyncClient,
    seeded_context,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Keep the sole active owner active and globally authorized.

    Args:
        api_client: In-process HTTP client for the real application.
        seeded_context: Seeded sole-owner identity and token.
        session_factory: Factory for independent PostgreSQL verification sessions.
    """
    self_disable = await api_client.patch(
        f"/api/v1/users/{seeded_context.owner_id}",
        json={"is_active": False},
        headers=seeded_context.headers("owner"),
    )
    assert self_disable.status_code == 409
    assert self_disable.json()["code"] == "SELF_DISABLE_FORBIDDEN"

    self_demote = await api_client.patch(
        f"/api/v1/users/{seeded_context.owner_id}",
        json={"role": "manager"},
        headers=seeded_context.headers("owner"),
    )
    assert self_demote.status_code == 409
    assert self_demote.json()["code"] == "SELF_OWNER_CHANGE_FORBIDDEN"

    async with session_factory() as session:
        owner = await session.get(User, seeded_context.owner_id)
        active_owner_count = await session.scalar(
            select(func.count(User.id)).where(
                User.role == UserRole.OWNER,
                User.is_active.is_(True),
            )
        )
        assert owner is not None
        assert owner.role == UserRole.OWNER
        assert owner.is_active is True
        assert active_owner_count == 1


async def test_concurrent_removal_of_two_last_owners_keeps_one_active(
    seeded_context,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Serialize different-row owner removals around the global owner invariant.

    The test-only authenticated principal uses an existing non-owner database row
    for audit foreign keys, keeping exactly the two targets in the active-owner
    count. Two independent HTTP requests therefore exercise the advisory lock
    instead of passing trivially because an untouched third owner remains.

    Args:
        seeded_context: Seeded actor identity and baseline access state.
        session_factory: Factory for independent PostgreSQL setup and checks.
    """
    owner_a = User(
        name="Concurrent Owner A",
        email="concurrent-owner-a@test.whitfieldwms.com",
        hashed_password="unused-concurrent-owner-test-hash",
        role=UserRole.OWNER,
        is_active=True,
    )
    owner_b = User(
        name="Concurrent Owner B",
        email="concurrent-owner-b@test.whitfieldwms.com",
        hashed_password="unused-concurrent-owner-test-hash",
        role=UserRole.OWNER,
        is_active=True,
    )
    async with session_factory() as session, session.begin():
        baseline_owner = await session.get(User, seeded_context.owner_id)
        assert baseline_owner is not None
        baseline_owner.is_active = False
        session.add_all([owner_a, owner_b])
        await session.flush()

    async def control_owner() -> CurrentUser:
        """Return a test-only owner principal excluded from the owner count.

        Returns:
            Synthetic authorized principal backed by an existing audit actor row.
        """
        return CurrentUser(
            id=seeded_context.manager_id,
            name="Owner Invariant Test Actor",
            email="manager@test.whitfieldwms.com",
            role=UserRole.OWNER,
            is_active=True,
            warehouse_id=None,
            warehouse_name=None,
        )

    app.dependency_overrides[get_current_user] = control_owner
    first_transport = ASGITransport(app=app, raise_app_exceptions=False)
    second_transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with (
            AsyncClient(
                transport=first_transport, base_url="http://owner-race-one"
            ) as first,
            AsyncClient(
                transport=second_transport, base_url="http://owner-race-two"
            ) as second,
        ):
            responses = await asyncio.gather(
                first.patch(f"/api/v1/users/{owner_a.id}", json={"is_active": False}),
                second.patch(f"/api/v1/users/{owner_b.id}", json={"is_active": False}),
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["code"] == "LAST_OWNER_REQUIRED"

    async with session_factory() as session:
        targets = list(
            (
                await session.scalars(
                    select(User)
                    .where(User.id.in_([owner_a.id, owner_b.id]))
                    .order_by(User.email)
                )
            ).all()
        )
        active_owner_count = await session.scalar(
            select(func.count(User.id)).where(
                User.role == UserRole.OWNER,
                User.is_active.is_(True),
            )
        )
        assert [owner.is_active for owner in targets].count(True) == 1
        assert [owner.is_active for owner in targets].count(False) == 1
        assert active_owner_count == 1
