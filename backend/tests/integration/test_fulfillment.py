"""Cancellation and outbound fulfillment state-machine integration tests."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.apis.api import app
from backend.core.models.enums import MovementType, ReservationStatus
from backend.core.models.inventory import InventoryBalance, InventoryMovement
from backend.core.models.order import (
    InventoryReservation,
    Order,
    OrderItem,
    OutboundPackage,
)
from backend.core.models.reliability import AuditLog

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _create_allocated_order(
    client: AsyncClient,
    seeded_context,
    *,
    reference: str,
    quantity: int,
) -> dict:
    """Create one allocated order through the public API.

    Args:
        client: In-process HTTP client for the real application.
        seeded_context: Seeded users, products, and warehouse state.
        reference: Unique external order reference.
        quantity: Product A units to reserve.

    Returns:
        Created allocated order response.
    """
    response = await client.post(
        "/api/v1/orders",
        json={
            "external_reference": reference,
            "items": [
                {"product_id": str(seeded_context.product_a_id), "quantity": quantity}
            ],
        },
        headers=seeded_context.headers(
            "staff", idempotency_key=f"create-{reference.lower()}"
        ),
    )
    assert response.status_code == 201
    assert response.json()["status"] == "allocated"
    return response.json()


async def test_cancellation_releases_active_reservations_once(
    api_client: AsyncClient,
    seeded_context,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Release reserved units without reducing on-hand when an order is cancelled.

    Args:
        api_client: In-process HTTP client for the real application.
        seeded_context: Seeded users, products, and warehouse state.
        session_factory: Factory for independent PostgreSQL verification sessions.
    """
    order = await _create_allocated_order(
        api_client,
        seeded_context,
        reference="ORDER-CANCEL-001",
        quantity=4,
    )
    cancel_headers = seeded_context.headers(
        "staff", idempotency_key="cancel-order-command-001"
    )
    cancelled = await api_client.post(
        f"/api/v1/orders/{order['id']}/cancel",
        json={"reason": "Customer requested cancellation"},
        headers=cancel_headers,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancel_reason"] == "Customer requested cancellation"

    replayed = await api_client.post(
        f"/api/v1/orders/{order['id']}/cancel",
        json={"reason": "Customer requested cancellation"},
        headers=cancel_headers,
    )
    assert replayed.status_code == 200
    assert replayed.json() == cancelled.json()

    async with session_factory() as session:
        balance = (
            await session.execute(
                select(InventoryBalance).where(
                    InventoryBalance.warehouse_id == seeded_context.reno_id,
                    InventoryBalance.product_id == seeded_context.product_a_id,
                )
            )
        ).scalar_one()
        reservation = (
            await session.scalars(
                select(InventoryReservation).where(
                    InventoryReservation.order_id == order["id"]
                )
            )
        ).one()
        movements = list(
            (
                await session.scalars(
                    select(InventoryMovement)
                    .where(InventoryMovement.reference_id == order["id"])
                    .order_by(InventoryMovement.created_at)
                )
            ).all()
        )
        assert (balance.on_hand, balance.reserved) == (10, 0)
        assert reservation.status == ReservationStatus.RELEASED
        assert [movement.movement_type for movement in movements] == [
            MovementType.RESERVE,
            MovementType.RELEASE,
        ]


async def test_pack_label_and_ship_enforce_legal_state_transitions(
    api_client: AsyncClient,
    seeded_context,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Advance fulfillment in order and consume stock only at shipment.

    Args:
        api_client: In-process HTTP client for the real application.
        seeded_context: Seeded users, products, and warehouse state.
        session_factory: Factory for independent PostgreSQL verification sessions.
    """
    order = await _create_allocated_order(
        api_client,
        seeded_context,
        reference="ORDER-SHIP-001",
        quantity=4,
    )
    order_id = order["id"]
    package_payload = {
        "weight": "3.250",
        "weight_unit": "lb",
        "length": "12.000",
        "width": "8.000",
        "height": "6.000",
        "dimension_unit": "in",
    }

    premature_pack = await api_client.post(
        f"/api/v1/orders/{order_id}/pack",
        json=package_payload,
        headers=seeded_context.headers(
            "staff", idempotency_key="premature-pack-command"
        ),
    )
    assert premature_pack.status_code == 409
    assert premature_pack.json()["code"] == "ORDER_STATE_CONFLICT"

    picking = await api_client.post(
        f"/api/v1/orders/{order_id}/start-picking",
        headers=seeded_context.headers(
            "staff", idempotency_key="start-picking-command-001"
        ),
    )
    assert picking.status_code == 200
    assert picking.json()["status"] == "picking"

    confirmed = picking.json()
    for item in picking.json()["items"]:
        picked = await api_client.post(
            f"/api/v1/orders/{order_id}/items/{item['id']}/pick",
            json={"picked_quantity": item["quantity"]},
            headers=seeded_context.headers(
                "staff", idempotency_key=f"pick-order-line-{item['id']}"
            ),
        )
        assert picked.status_code == 200
        confirmed = picked.json()
    assert all(
        item["picked_quantity"] == item["quantity"] for item in confirmed["items"]
    )

    packed = await api_client.post(
        f"/api/v1/orders/{order_id}/pack",
        json=package_payload,
        headers=seeded_context.headers("staff", idempotency_key="pack-command-001"),
    )
    assert packed.status_code == 200
    assert packed.json()["status"] == "packed"
    assert packed.json()["package"]["status"] == "packed"

    labelled = await api_client.post(
        f"/api/v1/orders/{order_id}/create-label",
        json={"carrier": "FakeCarrier", "service_level": "ground"},
        headers=seeded_context.headers("staff", idempotency_key="label-command-001"),
    )
    assert labelled.status_code == 200
    assert labelled.json()["status"] == "label_created"
    assert labelled.json()["package"]["status"] == "label_created"
    assert labelled.json()["package"]["tracking_number"].startswith("WF")
    assert labelled.json()["package"]["label_url_or_key"]

    shipped = await api_client.post(
        f"/api/v1/orders/{order_id}/ship",
        headers=seeded_context.headers("staff", idempotency_key="ship-command-001"),
    )
    assert shipped.status_code == 200
    assert shipped.json()["status"] == "shipped"
    assert shipped.json()["package"]["status"] == "shipped"

    cancel_shipped = await api_client.post(
        f"/api/v1/orders/{order_id}/cancel",
        json={"reason": "Too late to cancel"},
        headers=seeded_context.headers(
            "staff", idempotency_key="cancel-shipped-command-001"
        ),
    )
    assert cancel_shipped.status_code == 409
    assert cancel_shipped.json()["code"] == "ORDER_STATE_CONFLICT"

    async with session_factory() as session:
        balance = (
            await session.execute(
                select(InventoryBalance).where(
                    InventoryBalance.warehouse_id == seeded_context.reno_id,
                    InventoryBalance.product_id == seeded_context.product_a_id,
                )
            )
        ).scalar_one()
        reservation = (
            await session.scalars(
                select(InventoryReservation).where(
                    InventoryReservation.order_id == order_id
                )
            )
        ).one()
        movements = list(
            (
                await session.scalars(
                    select(InventoryMovement)
                    .where(InventoryMovement.reference_id == order_id)
                    .order_by(InventoryMovement.created_at)
                )
            ).all()
        )
        assert (balance.on_hand, balance.reserved) == (6, 0)
        assert reservation.status == ReservationStatus.CONSUMED
        assert [movement.movement_type for movement in movements] == [
            MovementType.RESERVE,
            MovementType.SHIP,
        ]


async def test_picking_checklist_persists_partial_counts_and_guards_pack(
    api_client: AsyncClient,
    seeded_context,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Persist one partial pick, replay it once, and reject invalid packing.

    Args:
        api_client: In-process HTTP client for the real application.
        seeded_context: Seeded users, products, and warehouse state.
        session_factory: Factory for independent PostgreSQL verification sessions.
    """
    order = await _create_allocated_order(
        api_client,
        seeded_context,
        reference="ORDER-PICK-CHECKLIST-001",
        quantity=4,
    )
    order_id = order["id"]
    item = order["items"][0]
    item_id = item["id"]
    assert item["picked_quantity"] == 0

    picking = await api_client.post(
        f"/api/v1/orders/{order_id}/start-picking",
        headers=seeded_context.headers(
            "staff", idempotency_key="checklist-start-picking-command"
        ),
    )
    assert picking.status_code == 200
    assert picking.json()["items"][0]["picked_quantity"] == 0

    partial_headers = seeded_context.headers(
        "staff", idempotency_key="partial-pick-command-001"
    )
    partial = await api_client.post(
        f"/api/v1/orders/{order_id}/items/{item_id}/pick",
        json={"picked_quantity": 2},
        headers=partial_headers,
    )
    assert partial.status_code == 200
    assert partial.json()["items"][0]["picked_quantity"] == 2

    replayed = await api_client.post(
        f"/api/v1/orders/{order_id}/items/{item_id}/pick",
        json={"picked_quantity": 2},
        headers=partial_headers,
    )
    assert replayed.status_code == 200
    assert replayed.json() == partial.json()

    persisted = await api_client.get(
        f"/api/v1/orders/{order_id}", headers=seeded_context.headers("staff")
    )
    assert persisted.status_code == 200
    assert persisted.json()["items"][0]["picked_quantity"] == 2

    package_payload = {
        "weight": "2.000",
        "weight_unit": "lb",
        "length": "10.000",
        "width": "7.000",
        "height": "5.000",
        "dimension_unit": "in",
    }
    incomplete_pack = await api_client.post(
        f"/api/v1/orders/{order_id}/pack",
        json=package_payload,
        headers=seeded_context.headers(
            "staff", idempotency_key="incomplete-picking-pack-command"
        ),
    )
    assert incomplete_pack.status_code == 409
    assert incomplete_pack.json()["code"] == "PICKING_INCOMPLETE"
    assert incomplete_pack.json()["order_item_ids"] == [item_id]

    overpick = await api_client.post(
        f"/api/v1/orders/{order_id}/items/{item_id}/pick",
        json={"picked_quantity": 5},
        headers=seeded_context.headers("staff", idempotency_key="overpick-command-001"),
    )
    assert overpick.status_code == 409
    assert overpick.json()["code"] == "PICK_QUANTITY_EXCEEDS_ORDER"

    async with session_factory() as session:
        stored_item = await session.get(OrderItem, item_id)
        package_count = await session.scalar(
            select(func.count(OutboundPackage.id)).where(
                OutboundPackage.order_id == order_id
            )
        )
        pick_audit_count = await session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.table_name == "order_items",
                AuditLog.record_id == item_id,
                AuditLog.action == "ORDER_ITEM_PICK_CONFIRMED",
            )
        )
        assert stored_item is not None
        assert stored_item.picked_quantity == 2
        assert package_count == 0
        assert pick_audit_count == 1


async def test_cancel_and_ship_race_has_one_legal_terminal_stock_result(
    api_client: AsyncClient,
    seeded_context,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Serialize cancellation against shipment and reconcile the winning outcome.

    Args:
        api_client: In-process HTTP client for fulfillment setup.
        seeded_context: Seeded users, products, and warehouse state.
        session_factory: Factory for independent PostgreSQL verification sessions.
    """
    order = await _create_allocated_order(
        api_client,
        seeded_context,
        reference="ORDER-CANCEL-SHIP-RACE-001",
        quantity=4,
    )
    order_id = order["id"]
    picking = await api_client.post(
        f"/api/v1/orders/{order_id}/start-picking",
        headers=seeded_context.headers(
            "staff", idempotency_key="race-start-picking-command"
        ),
    )
    assert picking.status_code == 200
    for item in picking.json()["items"]:
        picked = await api_client.post(
            f"/api/v1/orders/{order_id}/items/{item['id']}/pick",
            json={"picked_quantity": item["quantity"]},
            headers=seeded_context.headers(
                "staff", idempotency_key=f"race-pick-line-{item['id']}"
            ),
        )
        assert picked.status_code == 200
    packed = await api_client.post(
        f"/api/v1/orders/{order_id}/pack",
        json={
            "weight": "3.250",
            "weight_unit": "lb",
            "length": "12.000",
            "width": "8.000",
            "height": "6.000",
            "dimension_unit": "in",
        },
        headers=seeded_context.headers("staff", idempotency_key="race-pack-command"),
    )
    assert packed.status_code == 200
    labelled = await api_client.post(
        f"/api/v1/orders/{order_id}/create-label",
        json={"carrier": "FakeCarrier", "service_level": "ground"},
        headers=seeded_context.headers("staff", idempotency_key="race-label-command"),
    )
    assert labelled.status_code == 200
    assert labelled.json()["status"] == "label_created"

    ship_transport = ASGITransport(app=app, raise_app_exceptions=False)
    cancel_transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with (
        AsyncClient(
            transport=ship_transport, base_url="http://ship-race"
        ) as ship_client,
        AsyncClient(
            transport=cancel_transport, base_url="http://cancel-race"
        ) as cancel_client,
    ):
        responses = await asyncio.wait_for(
            asyncio.gather(
                ship_client.post(
                    f"/api/v1/orders/{order_id}/ship",
                    headers=seeded_context.headers(
                        "staff", idempotency_key="race-ship-command"
                    ),
                ),
                cancel_client.post(
                    f"/api/v1/orders/{order_id}/cancel",
                    json={"reason": "Customer cancelled during carrier handoff"},
                    headers=seeded_context.headers(
                        "staff", idempotency_key="race-cancel-command"
                    ),
                ),
            ),
            timeout=10,
        )

    assert sorted(response.status_code for response in responses) == [200, 409]
    success = next(response for response in responses if response.status_code == 200)
    conflict = next(response for response in responses if response.status_code == 409)
    assert success.json()["status"] in {"shipped", "cancelled"}
    assert conflict.json()["code"] == "ORDER_STATE_CONFLICT"

    async with session_factory() as session:
        stored_order = await session.get(Order, order_id)
        balance = (
            await session.execute(
                select(InventoryBalance).where(
                    InventoryBalance.warehouse_id == seeded_context.reno_id,
                    InventoryBalance.product_id == seeded_context.product_a_id,
                )
            )
        ).scalar_one()
        reservation = (
            await session.scalars(
                select(InventoryReservation).where(
                    InventoryReservation.order_id == order_id
                )
            )
        ).one()
        movements = list(
            (
                await session.scalars(
                    select(InventoryMovement)
                    .where(InventoryMovement.reference_id == order_id)
                    .order_by(InventoryMovement.created_at)
                )
            ).all()
        )
        terminal_audits = list(
            (
                await session.scalars(
                    select(AuditLog).where(
                        AuditLog.table_name == "orders",
                        AuditLog.record_id == order_id,
                        AuditLog.action.in_(["ORDER_SHIPPED", "ORDER_CANCELLED"]),
                    )
                )
            ).all()
        )

        assert stored_order is not None
        assert stored_order.status.value == success.json()["status"]
        assert balance.reserved == 0
        assert len(terminal_audits) == 1
        if stored_order.status.value == "shipped":
            assert (balance.on_hand, balance.reserved) == (6, 0)
            assert reservation.status == ReservationStatus.CONSUMED
            assert [movement.movement_type for movement in movements] == [
                MovementType.RESERVE,
                MovementType.SHIP,
            ]
        else:
            assert (balance.on_hand, balance.reserved) == (10, 0)
            assert reservation.status == ReservationStatus.RELEASED
            assert [movement.movement_type for movement in movements] == [
                MovementType.RESERVE,
                MovementType.RELEASE,
            ]
