"""Concurrent opening-balance command regression tests."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.apis.api import app
from backend.core.models.enums import MovementType
from backend.core.models.inventory import InventoryBalance, InventoryMovement
from backend.core.models.reliability import AuditLog, IdempotencyRecord

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_concurrent_opening_balances_create_exactly_one_ledger_chain(
    seeded_context,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Allow one of two different-key opening commands and reject the other.

    Args:
        seeded_context: Seeded owner, empty product balance, and warehouse state.
        session_factory: Factory for independent PostgreSQL verification sessions.
    """
    payload = {
        "warehouse_id": str(seeded_context.reno_id),
        "product_id": str(seeded_context.product_c_id),
        "quantity": 17,
        "reason": "Verified physical opening count",
    }
    first_transport = ASGITransport(app=app, raise_app_exceptions=False)
    second_transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with (
        AsyncClient(
            transport=first_transport, base_url="http://opening-session-one"
        ) as first,
        AsyncClient(
            transport=second_transport, base_url="http://opening-session-two"
        ) as second,
    ):
        responses = await asyncio.gather(
            first.post(
                "/api/v1/inventory/opening-balances",
                json=payload,
                headers=seeded_context.headers(
                    "owner", idempotency_key="opening-balance-race-key-one"
                ),
            ),
            second.post(
                "/api/v1/inventory/opening-balances",
                json=payload,
                headers=seeded_context.headers(
                    "owner", idempotency_key="opening-balance-race-key-two"
                ),
            ),
        )

    assert sorted(response.status_code for response in responses) == [201, 409]
    winner = next(response for response in responses if response.status_code == 201)
    loser = next(response for response in responses if response.status_code == 409)
    assert winner.json() == {
        "warehouse_id": str(seeded_context.reno_id),
        "product_id": str(seeded_context.product_c_id),
        "on_hand": 17,
        "reserved": 0,
        "available": 17,
    }
    assert loser.json()["code"] == "OPENING_BALANCE_EXISTS"

    async with session_factory() as session:
        balances = list(
            (
                await session.scalars(
                    select(InventoryBalance).where(
                        InventoryBalance.warehouse_id == seeded_context.reno_id,
                        InventoryBalance.product_id == seeded_context.product_c_id,
                    )
                )
            ).all()
        )
        movements = list(
            (
                await session.scalars(
                    select(InventoryMovement).where(
                        InventoryMovement.warehouse_id == seeded_context.reno_id,
                        InventoryMovement.product_id == seeded_context.product_c_id,
                        InventoryMovement.movement_type == MovementType.OPENING_BALANCE,
                    )
                )
            ).all()
        )
        audits = list(
            (
                await session.scalars(
                    select(AuditLog).where(
                        AuditLog.warehouse_id == seeded_context.reno_id,
                        AuditLog.action == "OPENING_BALANCE_POSTED",
                    )
                )
            ).all()
        )
        records = list(
            (
                await session.scalars(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.user_id == seeded_context.owner_id,
                        IdempotencyRecord.operation == "inventory:opening_balance",
                    )
                )
            ).all()
        )

        assert len(balances) == len(movements) == len(audits) == len(records) == 1
        balance = balances[0]
        movement = movements[0]
        audit = audits[0]
        record = records[0]
        assert (balance.on_hand, balance.reserved) == (17, 0)
        assert movement.reference_type == "opening_balances"
        assert movement.reference_id == balance.id
        assert movement.on_hand_after == 17
        assert audit.table_name == "inventory_balances"
        assert audit.record_id == balance.id
        assert record.resource_type == "inventory_balances"
        assert record.resource_id == balance.id
