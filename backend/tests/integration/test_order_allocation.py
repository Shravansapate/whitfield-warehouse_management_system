"""All-or-nothing and concurrent PostgreSQL order-allocation tests."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.apis.api import app
from backend.core.models.enums import MovementType, ReservationStatus
from backend.core.models.inventory import InventoryBalance, InventoryMovement
from backend.core.models.order import InventoryReservation, Order

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_inactive_product_cannot_create_order_or_reservation(
    api_client: AsyncClient,
    seeded_context,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reject direct ordering after an owner deactivates a product.

    Args:
        api_client: In-process HTTP client for the real application.
        seeded_context: Seeded users and warehouse state.
        session_factory: Factory for independent PostgreSQL verification sessions.
    """
    created = await api_client.post(
        "/api/v1/products",
        json={
            "sku": "INACTIVE-ORDER-SKU",
            "upc": "INACTIVE-ORDER-0001",
            "name": "Inactive Order Product",
            "description": "Created specifically for inactive-order regression coverage",
        },
        headers=seeded_context.headers("owner"),
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    deactivated = await api_client.patch(
        f"/api/v1/products/{product_id}",
        json={"is_active": False},
        headers=seeded_context.headers("owner"),
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False

    external_reference = "ORDER-INACTIVE-PRODUCT-001"
    rejected = await api_client.post(
        "/api/v1/orders",
        json={
            "external_reference": external_reference,
            "items": [{"product_id": product_id, "quantity": 1}],
        },
        headers=seeded_context.headers(
            "staff", idempotency_key="inactive-product-order-command"
        ),
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "PRODUCT_INACTIVE"
    assert rejected.json()["product_ids"] == [product_id]

    async with session_factory() as session:
        order_count = await session.scalar(
            select(func.count(Order.id)).where(
                Order.external_reference == external_reference
            )
        )
        reservation_count = await session.scalar(
            select(func.count(InventoryReservation.id)).where(
                InventoryReservation.product_id == product_id
            )
        )
        assert order_count == 0
        assert reservation_count == 0


async def test_multiline_allocation_rolls_back_every_line_on_one_shortage(
    api_client: AsyncClient,
    seeded_context,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Leave every balance untouched when any order line lacks availability.

    Args:
        api_client: In-process HTTP client for the real application.
        seeded_context: Seeded users, products, and warehouse state.
        session_factory: Factory for independent PostgreSQL verification sessions.
    """
    response = await api_client.post(
        "/api/v1/orders",
        json={
            "external_reference": "ORDER-ALL-OR-NOTHING-001",
            "items": [
                {"product_id": str(seeded_context.product_a_id), "quantity": 4},
                {"product_id": str(seeded_context.product_b_id), "quantity": 4},
            ],
        },
        headers=seeded_context.headers(
            "staff", idempotency_key="order-all-or-nothing-001"
        ),
    )
    assert response.status_code == 201
    assert response.json()["status"] == "cannot_fulfill"
    assert {item["product_id"] for item in response.json()["shortages"]} == {
        str(seeded_context.product_b_id)
    }

    async with session_factory() as session:
        balances = {
            row.product_id: row.reserved
            for row in (
                await session.scalars(
                    select(InventoryBalance).where(
                        InventoryBalance.warehouse_id == seeded_context.reno_id,
                        InventoryBalance.product_id.in_(
                            [
                                seeded_context.product_a_id,
                                seeded_context.product_b_id,
                            ]
                        ),
                    )
                )
            ).all()
        }
        reservation_count = await session.scalar(
            select(func.count(InventoryReservation.id))
        )
        reserve_movement_count = await session.scalar(
            select(func.count(InventoryMovement.id)).where(
                InventoryMovement.movement_type == MovementType.RESERVE
            )
        )
        assert balances == {
            seeded_context.product_a_id: 0,
            seeded_context.product_b_id: 0,
        }
        assert reservation_count == 0
        assert reserve_movement_count == 0


async def test_two_sessions_cannot_oversell_the_same_balance(
    seeded_context,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Serialize competing reservations through PostgreSQL's guarded update.

    Args:
        seeded_context: Seeded users, products, and warehouse state.
        session_factory: Factory for independent PostgreSQL verification sessions.
    """
    first_transport = ASGITransport(app=app, raise_app_exceptions=False)
    second_transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with (
        AsyncClient(
            transport=first_transport, base_url="http://first-session"
        ) as first,
        AsyncClient(
            transport=second_transport, base_url="http://second-session"
        ) as second,
    ):
        first_request = first.post(
            "/api/v1/orders",
            json={
                "external_reference": "ORDER-CONCURRENT-001",
                "items": [
                    {"product_id": str(seeded_context.product_a_id), "quantity": 7}
                ],
            },
            headers=seeded_context.headers(
                "staff", idempotency_key="order-concurrent-command-001"
            ),
        )
        second_request = second.post(
            "/api/v1/orders",
            json={
                "external_reference": "ORDER-CONCURRENT-002",
                "items": [
                    {"product_id": str(seeded_context.product_a_id), "quantity": 7}
                ],
            },
            headers=seeded_context.headers(
                "staff", idempotency_key="order-concurrent-command-002"
            ),
        )
        responses = await asyncio.gather(first_request, second_request)

    assert [response.status_code for response in responses] == [201, 201]
    assert sorted(response.json()["status"] for response in responses) == [
        "allocated",
        "cannot_fulfill",
    ]

    async with session_factory() as session:
        balance = (
            await session.execute(
                select(InventoryBalance).where(
                    InventoryBalance.warehouse_id == seeded_context.reno_id,
                    InventoryBalance.product_id == seeded_context.product_a_id,
                )
            )
        ).scalar_one()
        active_reservations = await session.scalar(
            select(func.count(InventoryReservation.id)).where(
                InventoryReservation.status == ReservationStatus.ACTIVE
            )
        )
        assert (balance.on_hand, balance.reserved) == (10, 7)
        assert balance.reserved <= balance.on_hand
        assert active_reservations == 1


async def test_inverse_line_order_contention_is_deadlock_free_and_all_or_nothing(
    seeded_context,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Serialize inverse-order multi-line allocations without partial reservations.

    Args:
        seeded_context: Seeded users, products, and warehouse state.
        session_factory: Factory for independent PostgreSQL verification sessions.
    """
    first_transport = ASGITransport(app=app, raise_app_exceptions=False)
    second_transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with (
        AsyncClient(
            transport=first_transport, base_url="http://inverse-order-one"
        ) as first,
        AsyncClient(
            transport=second_transport, base_url="http://inverse-order-two"
        ) as second,
    ):
        responses = await asyncio.wait_for(
            asyncio.gather(
                first.post(
                    "/api/v1/orders",
                    json={
                        "external_reference": "ORDER-INVERSE-CONTENTION-001",
                        "items": [
                            {
                                "product_id": str(seeded_context.product_a_id),
                                "quantity": 6,
                            },
                            {
                                "product_id": str(seeded_context.product_b_id),
                                "quantity": 2,
                            },
                        ],
                    },
                    headers=seeded_context.headers(
                        "staff", idempotency_key="inverse-contention-command-001"
                    ),
                ),
                second.post(
                    "/api/v1/orders",
                    json={
                        "external_reference": "ORDER-INVERSE-CONTENTION-002",
                        "items": [
                            {
                                "product_id": str(seeded_context.product_b_id),
                                "quantity": 2,
                            },
                            {
                                "product_id": str(seeded_context.product_a_id),
                                "quantity": 6,
                            },
                        ],
                    },
                    headers=seeded_context.headers(
                        "staff", idempotency_key="inverse-contention-command-002"
                    ),
                ),
            ),
            timeout=10,
        )

    assert [response.status_code for response in responses] == [201, 201]
    response_by_status = {
        response.json()["status"]: response.json() for response in responses
    }
    assert set(response_by_status) == {"allocated", "cannot_fulfill"}
    allocated_order_id = uuid.UUID(response_by_status["allocated"]["id"])
    failed_order_id = uuid.UUID(response_by_status["cannot_fulfill"]["id"])

    async with session_factory() as session:
        balances = {
            balance.product_id: balance
            for balance in (
                await session.scalars(
                    select(InventoryBalance).where(
                        InventoryBalance.warehouse_id == seeded_context.reno_id,
                        InventoryBalance.product_id.in_(
                            [
                                seeded_context.product_a_id,
                                seeded_context.product_b_id,
                            ]
                        ),
                    )
                )
            ).all()
        }
        reservations = list(
            (
                await session.scalars(
                    select(InventoryReservation).where(
                        InventoryReservation.order_id.in_(
                            [allocated_order_id, failed_order_id]
                        )
                    )
                )
            ).all()
        )
        reserve_movements = list(
            (
                await session.scalars(
                    select(InventoryMovement).where(
                        InventoryMovement.movement_type == MovementType.RESERVE,
                        InventoryMovement.reference_id.in_(
                            [allocated_order_id, failed_order_id]
                        ),
                    )
                )
            ).all()
        )

        assert balances[seeded_context.product_a_id].reserved == 6
        assert balances[seeded_context.product_b_id].reserved == 2
        assert all(balance.reserved <= balance.on_hand for balance in balances.values())
        assert {
            (reservation.order_id, reservation.product_id, reservation.quantity)
            for reservation in reservations
        } == {
            (allocated_order_id, seeded_context.product_a_id, 6),
            (allocated_order_id, seeded_context.product_b_id, 2),
        }
        assert all(
            reservation.status == ReservationStatus.ACTIVE
            for reservation in reservations
        )
        assert {
            (movement.reference_id, movement.product_id, movement.reserved_delta)
            for movement in reserve_movements
        } == {
            (allocated_order_id, seeded_context.product_a_id, 6),
            (allocated_order_id, seeded_context.product_b_id, 2),
        }
