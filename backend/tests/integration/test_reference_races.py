"""Concurrent business-reference uniqueness regressions."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.apis.api import app
from backend.core.models.order import Order
from backend.core.models.receiving import InboundReceipt

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_concurrent_duplicate_receipt_reference_returns_stable_conflict(
    seeded_context,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Serialize a tracking-number race into one create and one HTTP 409.

    Args:
        seeded_context: Seeded users and warehouse identifiers.
        session_factory: Independent PostgreSQL verification sessions.
    """
    payload = {
        "warehouse_id": str(seeded_context.reno_id),
        "tracking_number": "CONCURRENT-TRACKING-REFERENCE-001",
        "sender_name": "Race-safe vendor",
        "sender_return_address": "1 Transaction Lane, Reno, NV",
    }
    async with (
        AsyncClient(
            transport=ASGITransport(app=app), base_url="http://receipt-race-one"
        ) as first,
        AsyncClient(
            transport=ASGITransport(app=app), base_url="http://receipt-race-two"
        ) as second,
    ):
        responses = await asyncio.gather(
            first.post(
                "/api/v1/inbound-receipts",
                json=payload,
                headers=seeded_context.headers("staff"),
            ),
            second.post(
                "/api/v1/inbound-receipts",
                json=payload,
                headers=seeded_context.headers("manager"),
            ),
        )

    assert sorted(response.status_code for response in responses) == [201, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["code"] == "RECEIPT_REFERENCE_EXISTS"
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count(InboundReceipt.id)).where(
                InboundReceipt.tracking_number == payload["tracking_number"]
            )
        )
    assert count == 1


async def test_concurrent_duplicate_order_reference_returns_stable_conflict(
    seeded_context,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Serialize an external-reference race into one order and one HTTP 409.

    Args:
        seeded_context: Seeded users, stock, and warehouse identifiers.
        session_factory: Independent PostgreSQL verification sessions.
    """
    payload = {
        "external_reference": "CONCURRENT-ORDER-REFERENCE-001",
        "warehouse_id": str(seeded_context.reno_id),
        "items": [{"product_id": str(seeded_context.product_a_id), "quantity": 1}],
    }
    async with (
        AsyncClient(
            transport=ASGITransport(app=app), base_url="http://order-race-one"
        ) as first,
        AsyncClient(
            transport=ASGITransport(app=app), base_url="http://order-race-two"
        ) as second,
    ):
        responses = await asyncio.gather(
            first.post(
                "/api/v1/orders",
                json=payload,
                headers=seeded_context.headers(
                    "staff", idempotency_key="reference-race-order-staff"
                ),
            ),
            second.post(
                "/api/v1/orders",
                json=payload,
                headers=seeded_context.headers(
                    "manager", idempotency_key="reference-race-order-manager"
                ),
            ),
        )

    assert sorted(response.status_code for response in responses) == [201, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["code"] == "ORDER_REFERENCE_EXISTS"
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count(Order.id)).where(
                Order.external_reference == payload["external_reference"]
            )
        )
    assert count == 1
