"""PostgreSQL coverage for scoped operational list pagination and filters."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _created_at_key(payload: dict) -> tuple[datetime, uuid.UUID]:
    """Build the API's deterministic ascending creation-time sort key.

    Args:
        payload: Serialized record containing ``created_at`` and ``id``.

    Returns:
        Time and UUID tuple matching the PostgreSQL keyset ordering.
    """
    return datetime.fromisoformat(payload["created_at"]), uuid.UUID(payload["id"])


async def _create_order(
    api_client: AsyncClient,
    seeded_context,
    *,
    external_reference: str,
    quantity: int,
) -> dict:
    """Create one order through the real atomic allocation endpoint.

    Args:
        api_client: In-process HTTP client for the application.
        seeded_context: Seeded users, products, and warehouse state.
        external_reference: Unique external order reference.
        quantity: Requested units of the seeded product.

    Returns:
        Serialized created order.
    """
    response = await api_client.post(
        "/api/v1/orders",
        json={
            "external_reference": external_reference,
            "items": [
                {
                    "product_id": str(seeded_context.product_a_id),
                    "quantity": quantity,
                }
            ],
        },
        headers=seeded_context.headers(
            "staff", idempotency_key=f"pagination-{external_reference}"
        ),
    )
    assert response.status_code == 201
    return response.json()


async def _create_receipt(
    api_client: AsyncClient,
    seeded_context,
    *,
    suffix: str,
) -> dict:
    """Create one open receipt through the real receiving endpoint.

    Args:
        api_client: In-process HTTP client for the application.
        seeded_context: Seeded users and warehouse scope.
        suffix: Unique tracking-number suffix.

    Returns:
        Serialized open receipt.
    """
    response = await api_client.post(
        "/api/v1/inbound-receipts",
        json={
            "tracking_number": f"PAGINATION-RECEIPT-{suffix}",
            "sender_name": "Pagination Vendor",
            "sender_return_address": "10 Cursor Lane, Reno, NV",
        },
        headers=seeded_context.headers("staff"),
    )
    assert response.status_code == 201
    return response.json()


async def _finalize_damaged_receipt(
    api_client: AsyncClient,
    seeded_context,
    *,
    suffix: str,
) -> None:
    """Create and finalize a receipt containing one damaged unit.

    Args:
        api_client: In-process HTTP client for the application.
        seeded_context: Seeded users, product, and warehouse state.
        suffix: Stable command and tracking-number suffix.
    """
    receipt = await _create_receipt(
        api_client, seeded_context, suffix=f"DAMAGED-{suffix}"
    )
    receipt_id = receipt["id"]
    scanned = await api_client.post(
        f"/api/v1/inbound-receipts/{receipt_id}/items",
        json={
            "product_id": str(seeded_context.product_a_id),
            "quantity_received": 1,
            "quantity_accepted": 0,
            "quantity_damaged": 1,
            "damage_notes": "Pagination damage test",
        },
        headers=seeded_context.headers(
            "staff", idempotency_key=f"pagination-damaged-scan-{suffix}"
        ),
    )
    assert scanned.status_code == 200
    finalized = await api_client.post(
        f"/api/v1/inbound-receipts/{receipt_id}/receive",
        headers=seeded_context.headers(
            "staff", idempotency_key=f"pagination-damaged-finalize-{suffix}"
        ),
    )
    assert finalized.status_code == 200


async def test_orders_use_scoped_stable_cursors_and_validated_filters(
    api_client: AsyncClient,
    seeded_context,
) -> None:
    """Page orders without gaps while enforcing status, dates, sort, and scope.

    Args:
        api_client: In-process HTTP client for the real application.
        seeded_context: Seeded users, products, and warehouse state.
    """
    allocated = [
        await _create_order(
            api_client,
            seeded_context,
            external_reference=f"PAGINATED-ORDER-{index}",
            quantity=1,
        )
        for index in range(3)
    ]
    failed = await _create_order(
        api_client,
        seeded_context,
        external_reference="PAGINATED-ORDER-SHORTAGE",
        quantity=100,
    )
    assert failed["status"] == "cannot_fulfill"

    expected = sorted(allocated, key=_created_at_key)
    first_page = await api_client.get(
        "/api/v1/orders",
        params={"status": "allocated", "sort": "created_at_asc", "limit": 2},
        headers=seeded_context.headers("staff"),
    )
    assert first_page.status_code == 200
    assert isinstance(first_page.json(), list)
    cursor = first_page.headers.get("X-Next-Cursor")
    assert cursor
    assert [row["id"] for row in first_page.json()] == [
        row["id"] for row in expected[:2]
    ]

    second_page = await api_client.get(
        "/api/v1/orders",
        params={
            "status": "allocated",
            "sort": "created_at_asc",
            "limit": 2,
            "cursor": cursor,
        },
        headers=seeded_context.headers("staff"),
    )
    assert second_page.status_code == 200
    assert [row["id"] for row in second_page.json()] == [expected[2]["id"]]
    assert "X-Next-Cursor" not in second_page.headers

    status_filtered = await api_client.get(
        "/api/v1/orders",
        params={"status": "cannot_fulfill"},
        headers=seeded_context.headers("staff"),
    )
    assert status_filtered.status_code == 200
    assert [row["id"] for row in status_filtered.json()] == [failed["id"]]

    date_filtered = await api_client.get(
        "/api/v1/orders",
        params={
            "status": "allocated",
            "sort": "created_at_asc",
            "created_from": expected[1]["created_at"],
        },
        headers=seeded_context.headers("staff"),
    )
    assert date_filtered.status_code == 200
    assert all(
        datetime.fromisoformat(row["created_at"])
        >= datetime.fromisoformat(expected[1]["created_at"])
        for row in date_filtered.json()
    )

    wrong_sort_cursor = await api_client.get(
        "/api/v1/orders",
        params={"cursor": cursor, "sort": "created_at_desc"},
        headers=seeded_context.headers("staff"),
    )
    assert wrong_sort_cursor.status_code == 422
    assert wrong_sort_cursor.json()["code"] == "INVALID_PAGINATION"

    reversed_dates = await api_client.get(
        "/api/v1/orders",
        params={
            "created_from": "2030-01-01T00:00:00Z",
            "created_to": "2020-01-01T00:00:00Z",
        },
        headers=seeded_context.headers("staff"),
    )
    assert reversed_dates.status_code == 422
    assert reversed_dates.json()["code"] == "INVALID_PAGINATION"

    invalid_sort = await api_client.get(
        "/api/v1/orders",
        params={"sort": "status_desc"},
        headers=seeded_context.headers("staff"),
    )
    assert invalid_sort.status_code == 422

    scope_precedes_cursor_validation = await api_client.get(
        "/api/v1/orders",
        params={
            "warehouse_id": str(seeded_context.columbus_id),
            "cursor": "not-valid",
        },
        headers=seeded_context.headers("staff"),
    )
    assert scope_precedes_cursor_validation.status_code == 403
    assert scope_precedes_cursor_validation.json()["code"] == "WAREHOUSE_FORBIDDEN"


async def test_receipts_and_audit_logs_page_with_domain_filters(
    api_client: AsyncClient,
    seeded_context,
) -> None:
    """Page receipts and audit events with unchanged list response bodies.

    Args:
        api_client: In-process HTTP client for the real application.
        seeded_context: Seeded users and warehouse state.
    """
    receipts = [
        await _create_receipt(api_client, seeded_context, suffix=str(index))
        for index in range(3)
    ]
    cancelled = await api_client.post(
        f"/api/v1/inbound-receipts/{receipts[1]['id']}/cancel",
        json={"reason": "Pagination status filter"},
        headers=seeded_context.headers(
            "staff", idempotency_key="pagination-receipt-cancel"
        ),
    )
    assert cancelled.status_code == 200

    expected_open = sorted(
        [receipts[0], receipts[2]],
        key=_created_at_key,
    )
    first_page = await api_client.get(
        "/api/v1/inbound-receipts",
        params={"status": "open", "sort": "created_at_asc", "limit": 1},
        headers=seeded_context.headers("staff"),
    )
    assert first_page.status_code == 200
    assert isinstance(first_page.json(), list)
    receipt_cursor = first_page.headers.get("X-Next-Cursor")
    assert receipt_cursor

    second_page = await api_client.get(
        "/api/v1/inbound-receipts",
        params={
            "status": "open",
            "sort": "created_at_asc",
            "limit": 1,
            "cursor": receipt_cursor,
        },
        headers=seeded_context.headers("staff"),
    )
    assert second_page.status_code == 200
    assert [row["id"] for row in first_page.json() + second_page.json()] == [
        row["id"] for row in expected_open
    ]

    cancelled_only = await api_client.get(
        "/api/v1/inbound-receipts",
        params={"status": "cancelled"},
        headers=seeded_context.headers("staff"),
    )
    assert cancelled_only.status_code == 200
    assert [row["id"] for row in cancelled_only.json()] == [receipts[1]["id"]]

    audit_first = await api_client.get(
        "/api/v1/audit-logs",
        params={
            "action": "RECEIPT_CREATED",
            "source": "web",
            "sort": "created_at_asc",
            "limit": 2,
        },
        headers=seeded_context.headers("manager"),
    )
    assert audit_first.status_code == 200
    assert isinstance(audit_first.json(), list)
    assert all(row["action"] == "RECEIPT_CREATED" for row in audit_first.json())
    assert all(row["source"] == "web" for row in audit_first.json())
    audit_cursor = audit_first.headers.get("X-Next-Cursor")
    assert audit_cursor

    audit_second = await api_client.get(
        "/api/v1/audit-logs",
        params={
            "action": "RECEIPT_CREATED",
            "source": "web",
            "sort": "created_at_asc",
            "limit": 2,
            "cursor": audit_cursor,
        },
        headers=seeded_context.headers("manager"),
    )
    assert audit_second.status_code == 200
    audit_rows = audit_first.json() + audit_second.json()
    assert len(audit_rows) == 3
    assert len({row["id"] for row in audit_rows}) == 3

    audit_date_filtered = await api_client.get(
        "/api/v1/audit-logs",
        params={
            "action": "RECEIPT_CREATED",
            "created_from": audit_rows[1]["created_at"],
            "sort": "created_at_asc",
        },
        headers=seeded_context.headers("manager"),
    )
    assert audit_date_filtered.status_code == 200
    assert all(
        datetime.fromisoformat(row["created_at"])
        >= datetime.fromisoformat(audit_rows[1]["created_at"])
        for row in audit_date_filtered.json()
    )

    audit_scope_precedes_cursor = await api_client.get(
        "/api/v1/audit-logs",
        params={
            "warehouse_id": str(seeded_context.columbus_id),
            "cursor": "not-valid",
        },
        headers=seeded_context.headers("manager"),
    )
    assert audit_scope_precedes_cursor.status_code == 403
    assert audit_scope_precedes_cursor.json()["code"] == "WAREHOUSE_FORBIDDEN"


async def test_damaged_returns_support_cursor_status_and_date_filters(
    api_client: AsyncClient,
    seeded_context,
) -> None:
    """Page damaged returns and isolate completed return-to-sender work.

    Args:
        api_client: In-process HTTP client for the real application.
        seeded_context: Seeded users, product, and warehouse state.
    """
    for index in range(3):
        await _finalize_damaged_receipt(api_client, seeded_context, suffix=str(index))

    all_pending = await api_client.get(
        "/api/v1/damaged-returns",
        params={"status": "pending_return", "sort": "created_at_asc"},
        headers=seeded_context.headers("staff"),
    )
    assert all_pending.status_code == 200
    expected_ids = [row["id"] for row in all_pending.json()]
    assert len(expected_ids) == 3

    first_page = await api_client.get(
        "/api/v1/damaged-returns",
        params={
            "status": "pending_return",
            "sort": "created_at_asc",
            "limit": 2,
        },
        headers=seeded_context.headers("staff"),
    )
    assert first_page.status_code == 200
    cursor = first_page.headers.get("X-Next-Cursor")
    assert cursor
    second_page = await api_client.get(
        "/api/v1/damaged-returns",
        params={
            "status": "pending_return",
            "sort": "created_at_asc",
            "limit": 2,
            "cursor": cursor,
        },
        headers=seeded_context.headers("staff"),
    )
    assert second_page.status_code == 200
    assert [row["id"] for row in first_page.json() + second_page.json()] == expected_ids

    completed = await api_client.post(
        f"/api/v1/damaged-returns/{expected_ids[0]}/complete",
        json={"return_tracking_number": "PAGINATION-RETURN-COMPLETE"},
        headers=seeded_context.headers(
            "manager", idempotency_key="pagination-damaged-complete"
        ),
    )
    assert completed.status_code == 200

    completed_only = await api_client.get(
        "/api/v1/damaged-returns",
        params={"status": "returned_to_sender"},
        headers=seeded_context.headers("staff"),
    )
    assert completed_only.status_code == 200
    assert [row["id"] for row in completed_only.json()] == [expected_ids[0]]

    future_only = await api_client.get(
        "/api/v1/damaged-returns",
        params={"created_from": "2100-01-01T00:00:00Z"},
        headers=seeded_context.headers("staff"),
    )
    assert future_only.status_code == 200
    assert future_only.json() == []
