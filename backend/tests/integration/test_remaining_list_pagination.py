"""PostgreSQL coverage for the remaining universal list contracts."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _collect_cursor_pages(
    api_client: AsyncClient,
    *,
    path: str,
    headers: dict[str, str],
    params: dict[str, Any],
) -> list[dict]:
    """Follow opaque response cursors until one complete list is collected.

    Stable filters and sorting are reused for every continuation request.

    Args:
        api_client: In-process HTTP client for the real application.
        path: List endpoint path.
        headers: Authenticated request headers.
        params: Stable filters and sort, excluding the cursor.

    Returns:
        Concatenated serialized rows from every page.
    """
    rows: list[dict] = []
    cursor: str | None = None
    while True:
        request_params = {**params}
        if cursor is not None:
            request_params["cursor"] = cursor
        response = await api_client.get(path, headers=headers, params=request_params)
        assert response.status_code == 200
        rows.extend(response.json())
        cursor = response.headers.get("X-Next-Cursor")
        if cursor is None:
            return rows


async def test_inventory_and_low_stock_use_stable_scalar_cursors(
    api_client: AsyncClient,
    seeded_context,
) -> None:
    """Page balances by name and availability while preserving warehouse scope.

    Search, low-stock, detail, malformed-cursor, and authorization paths run too.

    Args:
        api_client: In-process HTTP client for the real application.
        seeded_context: Seeded users, products, and warehouse balances.
    """
    headers = seeded_context.headers("staff")
    for sort in ("name_asc", "available_desc"):
        complete = await api_client.get(
            "/api/v1/inventory",
            headers=headers,
            params={"sort": sort, "limit": 500},
        )
        assert complete.status_code == 200
        paged = await _collect_cursor_pages(
            api_client,
            path="/api/v1/inventory",
            headers=headers,
            params={"sort": sort, "limit": 1},
        )
        assert [row["product_id"] for row in paged] == [
            row["product_id"] for row in complete.json()
        ]

    search = await api_client.get(
        "/api/v1/inventory",
        headers=headers,
        params={"q": "TEST-B"},
    )
    assert search.status_code == 200
    assert [row["product_id"] for row in search.json()] == [
        str(seeded_context.product_b_id)
    ]

    low_stock = await api_client.get(
        "/api/v1/inventory/low-stock",
        headers=headers,
        params={"limit": 1},
    )
    assert low_stock.status_code == 200
    assert [row["product_id"] for row in low_stock.json()] == [
        str(seeded_context.product_c_id)
    ]
    assert "X-Next-Cursor" not in low_stock.headers

    detail = await api_client.get(
        f"/api/v1/inventory/{seeded_context.product_b_id}",
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json()["available"] == 3

    first_page = await api_client.get(
        "/api/v1/inventory",
        headers=headers,
        params={"sort": "name_asc", "limit": 1},
    )
    cursor = first_page.headers["X-Next-Cursor"]
    wrong_sort = await api_client.get(
        "/api/v1/inventory",
        headers=headers,
        params={"sort": "available_asc", "cursor": cursor},
    )
    assert wrong_sort.status_code == 422
    assert wrong_sort.json()["code"] == "INVALID_PAGINATION"

    malformed_cursor = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "v": 1,
                    "sort": "name_asc",
                    "value_type": [],
                    "value": "test product a",
                    "id": str(seeded_context.product_a_id),
                }
            ).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    malformed = await api_client.get(
        "/api/v1/inventory",
        headers=headers,
        params={"sort": "name_asc", "cursor": malformed_cursor},
    )
    assert malformed.status_code == 422
    assert malformed.json()["code"] == "INVALID_PAGINATION"

    scope_first = await api_client.get(
        "/api/v1/inventory",
        headers=headers,
        params={
            "warehouse_id": str(seeded_context.columbus_id),
            "cursor": "not-valid",
        },
    )
    assert scope_first.status_code == 403
    assert scope_first.json()["code"] == "WAREHOUSE_FORBIDDEN"


async def test_inventory_movements_page_with_type_date_and_scope_filters(
    api_client: AsyncClient,
    seeded_context,
) -> None:
    """Page the ledger and enforce movement type, date, sort, and scope.

    Real adjustments create immutable events for the cursor assertions.

    Args:
        api_client: In-process HTTP client for the real application.
        seeded_context: Seeded users, product, and warehouse state.
    """
    for suffix, quantity in (("first", 1), ("second", 2), ("third", -1)):
        response = await api_client.post(
            "/api/v1/inventory/adjustments",
            json={
                "product_id": str(seeded_context.product_a_id),
                "quantity_delta": quantity,
                "reason": f"Cursor movement {suffix}",
            },
            headers=seeded_context.headers(
                "manager", idempotency_key=f"movement-page-{suffix}"
            ),
        )
        assert response.status_code == 201

    path = f"/api/v1/inventory/{seeded_context.product_a_id}/movements"
    headers = seeded_context.headers("manager")
    complete = await api_client.get(
        path,
        headers=headers,
        params={"sort": "created_at_asc", "limit": 100},
    )
    assert complete.status_code == 200
    paged = await _collect_cursor_pages(
        api_client,
        path=path,
        headers=headers,
        params={"sort": "created_at_asc", "limit": 1},
    )
    assert [row["id"] for row in paged] == [row["id"] for row in complete.json()]

    type_filtered = await api_client.get(
        path,
        headers=headers,
        params={"movement_type": "ADJUST"},
    )
    assert type_filtered.status_code == 200
    assert len(type_filtered.json()) == 3
    assert {row["movement_type"] for row in type_filtered.json()} == {"ADJUST"}

    future_filtered = await api_client.get(
        path,
        headers=headers,
        params={"created_from": "2999-01-01T00:00:00Z"},
    )
    assert future_filtered.status_code == 200
    assert future_filtered.json() == []

    reversed_dates = await api_client.get(
        path,
        headers=headers,
        params={
            "created_from": "2030-01-02T00:00:00Z",
            "created_to": "2030-01-01T00:00:00Z",
        },
    )
    assert reversed_dates.status_code == 422
    assert reversed_dates.json()["code"] == "INVALID_PAGINATION"

    scope_first = await api_client.get(
        path,
        headers=headers,
        params={
            "warehouse_id": str(seeded_context.columbus_id),
            "cursor": "not-valid",
        },
    )
    assert scope_first.status_code == 403
    assert scope_first.json()["code"] == "WAREHOUSE_FORBIDDEN"


async def test_access_and_product_lists_expose_complete_filtered_pages(
    api_client: AsyncClient,
    seeded_context,
) -> None:
    """Page warehouses, users, and products with their domain filters.

    Role authorization and active, assignment, search, and date filters run too.

    Args:
        api_client: In-process HTTP client for the real application.
        seeded_context: Seeded access and product-master records.
    """
    owner_headers = seeded_context.headers("owner")
    warehouse_complete = await api_client.get(
        "/api/v1/warehouses",
        headers=owner_headers,
        params={"sort": "created_at_asc", "limit": 100},
    )
    assert warehouse_complete.status_code == 200
    warehouse_paged = await _collect_cursor_pages(
        api_client,
        path="/api/v1/warehouses",
        headers=owner_headers,
        params={"sort": "created_at_asc", "limit": 1},
    )
    assert [row["id"] for row in warehouse_paged] == [
        row["id"] for row in warehouse_complete.json()
    ]

    scoped_warehouses = await api_client.get(
        "/api/v1/warehouses",
        headers=seeded_context.headers("staff"),
        params={"limit": 1},
    )
    assert scoped_warehouses.status_code == 200
    assert [row["id"] for row in scoped_warehouses.json()] == [
        str(seeded_context.reno_id)
    ]
    assert "X-Next-Cursor" not in scoped_warehouses.headers

    inactive_warehouses = await api_client.get(
        "/api/v1/warehouses",
        headers=owner_headers,
        params={"is_active": False},
    )
    assert inactive_warehouses.status_code == 200
    assert inactive_warehouses.json() == []

    users_complete = await api_client.get(
        "/api/v1/users",
        headers=owner_headers,
        params={"sort": "created_at_asc", "limit": 500},
    )
    assert users_complete.status_code == 200
    users_paged = await _collect_cursor_pages(
        api_client,
        path="/api/v1/users",
        headers=owner_headers,
        params={"sort": "created_at_asc", "limit": 1},
    )
    assert [row["id"] for row in users_paged] == [
        row["id"] for row in users_complete.json()
    ]

    manager_users = await api_client.get(
        "/api/v1/users",
        headers=owner_headers,
        params={"role": "manager", "q": "reno", "is_active": True},
    )
    assert manager_users.status_code == 200
    assert [row["id"] for row in manager_users.json()] == [
        str(seeded_context.manager_id)
    ]

    assigned_users = await api_client.get(
        "/api/v1/users",
        headers=owner_headers,
        params={"warehouse_id": str(seeded_context.reno_id)},
    )
    assert assigned_users.status_code == 200
    assert {row["id"] for row in assigned_users.json()} == {
        str(seeded_context.manager_id),
        str(seeded_context.staff_id),
        str(seeded_context.disabled_id),
    }

    disabled_users = await api_client.get(
        "/api/v1/users",
        headers=owner_headers,
        params={"is_active": False},
    )
    assert disabled_users.status_code == 200
    assert [row["id"] for row in disabled_users.json()] == [
        str(seeded_context.disabled_id)
    ]

    unauthorized_users = await api_client.get(
        "/api/v1/users",
        headers=seeded_context.headers("staff"),
        params={"cursor": "not-valid"},
    )
    assert unauthorized_users.status_code == 403
    assert unauthorized_users.json()["code"] == "ROLE_FORBIDDEN"

    products_complete = await api_client.get(
        "/api/v1/products/search",
        headers=owner_headers,
        params={"sort": "created_at_asc", "limit": 500},
    )
    assert products_complete.status_code == 200
    products_paged = await _collect_cursor_pages(
        api_client,
        path="/api/v1/products/search",
        headers=owner_headers,
        params={"sort": "created_at_asc", "limit": 1},
    )
    assert [row["id"] for row in products_paged] == [
        row["id"] for row in products_complete.json()
    ]

    product_search = await api_client.get(
        "/api/v1/products/search",
        headers=owner_headers,
        params={"q": "TEST-B"},
    )
    assert product_search.status_code == 200
    assert [row["id"] for row in product_search.json()] == [
        str(seeded_context.product_b_id)
    ]

    deactivate = await api_client.patch(
        f"/api/v1/products/{seeded_context.product_c_id}",
        headers=owner_headers,
        json={"is_active": False},
    )
    assert deactivate.status_code == 200
    inactive_products = await api_client.get(
        "/api/v1/products/search",
        headers=owner_headers,
        params={"is_active": False},
    )
    assert inactive_products.status_code == 200
    assert [row["id"] for row in inactive_products.json()] == [
        str(seeded_context.product_c_id)
    ]

    future_products = await api_client.get(
        "/api/v1/products/search",
        headers=owner_headers,
        params={"created_from": "2999-01-01T00:00:00Z"},
    )
    assert future_products.status_code == 200
    assert future_products.json() == []

    first_product_page = await api_client.get(
        "/api/v1/products/search",
        headers=owner_headers,
        params={"sort": "created_at_asc", "limit": 1},
    )
    product_cursor = first_product_page.headers["X-Next-Cursor"]
    wrong_product_sort = await api_client.get(
        "/api/v1/products/search",
        headers=owner_headers,
        params={"sort": "created_at_desc", "cursor": product_cursor},
    )
    assert wrong_product_sort.status_code == 422
    assert wrong_product_sort.json()["code"] == "INVALID_PAGINATION"
