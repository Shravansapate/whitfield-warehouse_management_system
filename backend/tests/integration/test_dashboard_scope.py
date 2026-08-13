"""Owner-combined and assigned-warehouse dashboard acceptance tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_owner_can_switch_between_combined_and_warehouse_dashboards(
    api_client: AsyncClient,
    seeded_context,
) -> None:
    """Aggregate both warehouses only when an owner omits the selector."""
    combined = await api_client.get(
        "/api/v1/dashboard/summary",
        headers=seeded_context.headers("owner"),
    )
    assert combined.status_code == 200
    assert combined.json()["warehouse"] == {
        "id": None,
        "code": "ALL",
        "name": "All warehouses",
    }
    assert combined.json()["metrics"]["available_units"] == 78

    columbus = await api_client.get(
        "/api/v1/dashboard/summary",
        params={"warehouse_id": str(seeded_context.columbus_id)},
        headers=seeded_context.headers("owner"),
    )
    assert columbus.status_code == 200
    assert columbus.json()["warehouse"]["id"] == str(seeded_context.columbus_id)
    assert columbus.json()["metrics"]["available_units"] == 65


async def test_manager_dashboard_is_locked_to_assigned_warehouse(
    api_client: AsyncClient,
    seeded_context,
) -> None:
    """Resolve an omitted selector to the assignment and reject another scope."""
    assigned = await api_client.get(
        "/api/v1/dashboard/summary",
        headers=seeded_context.headers("manager"),
    )
    assert assigned.status_code == 200
    assert assigned.json()["warehouse"]["id"] == str(seeded_context.reno_id)
    assert assigned.json()["metrics"]["available_units"] == 13

    forbidden = await api_client.get(
        "/api/v1/dashboard/summary",
        params={"warehouse_id": str(seeded_context.columbus_id)},
        headers=seeded_context.headers("manager"),
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "WAREHOUSE_FORBIDDEN"
