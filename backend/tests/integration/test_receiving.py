"""Transactional receipt, damaged-stock, and idempotency integration tests."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.apis.api import app
from backend.core.models.inventory import InventoryBalance, InventoryMovement
from backend.core.models.receiving import DamagedReturn, InboundReceipt
from backend.core.models.reliability import AuditLog, IdempotencyRecord

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_receipt_posts_only_accepted_stock_once_and_tracks_damage(
    api_client: AsyncClient,
    seeded_context,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Keep draft scans off-ledger, then finalize accepted and damaged units once.

    Args:
        api_client: In-process HTTP client for the real application.
        seeded_context: Seeded users, products, and warehouse state.
        session_factory: Factory for independent PostgreSQL verification sessions.
    """
    created = await api_client.post(
        "/api/v1/inbound-receipts",
        json={
            "tracking_number": "RNO-RECEIPT-001",
            "sender_name": "Receiving Vendor",
            "sender_return_address": "10 Test Lane, Reno, NV",
        },
        headers=seeded_context.headers("staff"),
    )
    assert created.status_code == 201
    receipt_id = created.json()["id"]
    assert created.json()["status"] == "open"

    scan_payload = {
        "product_id": str(seeded_context.product_a_id),
        "quantity_received": 8,
        "quantity_accepted": 5,
        "quantity_damaged": 3,
        "damage_notes": "Crushed cartons",
    }
    scan_headers = seeded_context.headers(
        "staff", idempotency_key="receipt-scan-command-001"
    )
    scanned = await api_client.post(
        f"/api/v1/inbound-receipts/{receipt_id}/items",
        json=scan_payload,
        headers=scan_headers,
    )
    assert scanned.status_code == 200
    assert scanned.json()["status"] == "receiving"
    assert scanned.json()["accepted"] == 5
    assert scanned.json()["damaged"] == 3

    replayed_scan = await api_client.post(
        f"/api/v1/inbound-receipts/{receipt_id}/items",
        json=scan_payload,
        headers=scan_headers,
    )
    assert replayed_scan.status_code == 200
    assert replayed_scan.json() == scanned.json()

    mismatched_payload = dict(scan_payload)
    mismatched_payload.update(
        quantity_received=9,
        quantity_accepted=6,
        quantity_damaged=3,
    )
    mismatched = await api_client.post(
        f"/api/v1/inbound-receipts/{receipt_id}/items",
        json=mismatched_payload,
        headers=scan_headers,
    )
    assert mismatched.status_code == 409
    assert mismatched.json()["code"] == "IDEMPOTENCY_MISMATCH"

    async with session_factory() as session:
        draft_balance = (
            await session.execute(
                select(InventoryBalance).where(
                    InventoryBalance.warehouse_id == seeded_context.reno_id,
                    InventoryBalance.product_id == seeded_context.product_a_id,
                )
            )
        ).scalar_one()
        damaged_count = await session.scalar(select(func.count(DamagedReturn.id)))
        receipt_movement_count = await session.scalar(
            select(func.count(InventoryMovement.id)).where(
                InventoryMovement.reference_type == "inbound_receipts"
            )
        )
        assert (draft_balance.on_hand, draft_balance.reserved) == (10, 0)
        assert damaged_count == 0
        assert receipt_movement_count == 0

    finalize_headers = seeded_context.headers(
        "staff", idempotency_key="receipt-finalize-command-001"
    )
    finalized = await api_client.post(
        f"/api/v1/inbound-receipts/{receipt_id}/receive",
        headers=finalize_headers,
    )
    assert finalized.status_code == 200
    assert finalized.json()["status"] == "received"

    replayed_finalize = await api_client.post(
        f"/api/v1/inbound-receipts/{receipt_id}/receive",
        headers=finalize_headers,
    )
    assert replayed_finalize.status_code == 200
    assert replayed_finalize.json() == finalized.json()

    async with session_factory() as session:
        posted_balance = (
            await session.execute(
                select(InventoryBalance).where(
                    InventoryBalance.warehouse_id == seeded_context.reno_id,
                    InventoryBalance.product_id == seeded_context.product_a_id,
                )
            )
        ).scalar_one()
        damaged = (await session.scalars(select(DamagedReturn))).one()
        receipt_movement_count = await session.scalar(
            select(func.count(InventoryMovement.id)).where(
                InventoryMovement.reference_type == "inbound_receipts",
                InventoryMovement.reference_id == receipt_id,
            )
        )
        assert (posted_balance.on_hand, posted_balance.reserved) == (15, 0)
        assert damaged.quantity == 3
        assert damaged.status.value == "pending_return"
        assert receipt_movement_count == 1
        damaged_return_id = damaged.id

    completed = await api_client.post(
        f"/api/v1/damaged-returns/{damaged_return_id}/complete",
        json={
            "return_tracking_number": "RTS-RECEIPT-001",
            "notes": "Carrier collected damaged cartons",
        },
        headers=seeded_context.headers(
            "manager", idempotency_key="damage-complete-command-001"
        ),
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "returned_to_sender"

    async with session_factory() as session:
        unchanged_balance = (
            await session.execute(
                select(InventoryBalance).where(
                    InventoryBalance.warehouse_id == seeded_context.reno_id,
                    InventoryBalance.product_id == seeded_context.product_a_id,
                )
            )
        ).scalar_one()
        assert (unchanged_balance.on_hand, unchanged_balance.reserved) == (15, 0)


async def test_simultaneous_same_key_finalize_posts_one_receipt_ledger_entry(
    api_client: AsyncClient,
    seeded_context,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Replay one concurrent receipt finalization after exactly one stock posting.

    Args:
        api_client: In-process HTTP client for setup through the real application.
        seeded_context: Seeded users, products, and warehouse state.
        session_factory: Factory for independent PostgreSQL verification sessions.
    """
    created = await api_client.post(
        "/api/v1/inbound-receipts",
        json={
            "tracking_number": "RNO-CONCURRENT-FINALIZE-001",
            "sender_name": "Concurrent Receiving Vendor",
            "sender_return_address": "11 Transaction Lane, Reno, NV",
        },
        headers=seeded_context.headers("staff"),
    )
    assert created.status_code == 201
    receipt_id = created.json()["id"]

    scanned = await api_client.post(
        f"/api/v1/inbound-receipts/{receipt_id}/items",
        json={
            "product_id": str(seeded_context.product_a_id),
            "quantity_received": 7,
            "quantity_accepted": 5,
            "quantity_damaged": 2,
            "damage_notes": "Two cartons failed inspection",
        },
        headers=seeded_context.headers(
            "staff", idempotency_key="concurrent-finalize-scan-command"
        ),
    )
    assert scanned.status_code == 200

    command_headers = seeded_context.headers(
        "staff", idempotency_key="concurrent-finalize-same-key"
    )
    first_transport = ASGITransport(app=app, raise_app_exceptions=False)
    second_transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with (
        AsyncClient(
            transport=first_transport, base_url="http://receipt-finalize-one"
        ) as first,
        AsyncClient(
            transport=second_transport, base_url="http://receipt-finalize-two"
        ) as second,
    ):
        responses = await asyncio.wait_for(
            asyncio.gather(
                first.post(
                    f"/api/v1/inbound-receipts/{receipt_id}/receive",
                    headers=command_headers,
                ),
                second.post(
                    f"/api/v1/inbound-receipts/{receipt_id}/receive",
                    headers=command_headers,
                ),
            ),
            timeout=10,
        )

    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert responses[0].json()["status"] == "received"

    async with session_factory() as session:
        receipt = await session.get(InboundReceipt, receipt_id)
        balance = (
            await session.execute(
                select(InventoryBalance).where(
                    InventoryBalance.warehouse_id == seeded_context.reno_id,
                    InventoryBalance.product_id == seeded_context.product_a_id,
                )
            )
        ).scalar_one()
        movements = list(
            (
                await session.scalars(
                    select(InventoryMovement).where(
                        InventoryMovement.reference_type == "inbound_receipts",
                        InventoryMovement.reference_id == receipt_id,
                    )
                )
            ).all()
        )
        damaged_returns = list(
            (
                await session.scalars(
                    select(DamagedReturn).where(DamagedReturn.receipt_id == receipt_id)
                )
            ).all()
        )
        finalize_audits = list(
            (
                await session.scalars(
                    select(AuditLog).where(
                        AuditLog.table_name == "inbound_receipts",
                        AuditLog.record_id == receipt_id,
                        AuditLog.action == "RECEIPT_FINALIZED",
                    )
                )
            ).all()
        )
        idempotency_records = list(
            (
                await session.scalars(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.user_id == seeded_context.staff_id,
                        IdempotencyRecord.operation == f"receipt:{receipt_id}:finalize",
                        IdempotencyRecord.idempotency_key
                        == "concurrent-finalize-same-key",
                    )
                )
            ).all()
        )

        assert receipt is not None
        assert receipt.status.value == "received"
        assert (balance.on_hand, balance.reserved) == (15, 0)
        assert len(movements) == 1
        assert (movements[0].on_hand_delta, movements[0].reserved_delta) == (5, 0)
        assert (movements[0].on_hand_after, movements[0].reserved_after) == (15, 0)
        assert len(damaged_returns) == 1
        assert damaged_returns[0].quantity == 2
        assert len(finalize_audits) == 1
        assert len(idempotency_records) == 1
        assert idempotency_records[0].response_status == 200
        assert idempotency_records[0].resource_id == receipt.id
        response_body = idempotency_records[0].response_body
        assert isinstance(response_body, dict)
        assert response_body["id"] == receipt_id
        assert response_body["status"] == "received"
