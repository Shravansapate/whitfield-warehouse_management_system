"""Read-only scheduled low-stock check."""

from __future__ import annotations

import asyncio
import uuid

from backend.core import logger
from backend.core.apis.schemas.common import (
    InventorySort,
    ScalarCursor,
    decode_scalar_cursor,
)
from backend.core.cruds.access_crud import AccessCRUD
from backend.core.cruds.inventory_crud import InventoryCRUD
from backend.core.database.engine import async_session_factory, engine

logging = logger(__name__)


async def run_low_stock_check(job_id: str | None = None) -> dict[str, int]:
    """Count low-stock products by warehouse without mutating business data.

    Every cursor page is followed so the aggregate is never capped.

    Args:
        job_id: Optional scheduler correlation identifier.

    Returns:
        Low-stock counts keyed by warehouse code.
    """
    correlation_id = job_id or str(uuid.uuid4())
    access = AccessCRUD()
    inventory = InventoryCRUD()
    results: dict[str, int] = {}
    async with async_session_factory() as session:
        warehouses = await access.list_warehouses(session)
        for warehouse in warehouses:
            count = 0
            cursor: ScalarCursor | None = None
            while True:
                page = await inventory.list_inventory(
                    session,
                    warehouse_id=warehouse.id,
                    low_stock_only=True,
                    cursor=cursor,
                    limit=500,
                )
                count += len(page.items)
                if page.next_cursor is None:
                    break
                cursor = decode_scalar_cursor(
                    page.next_cursor, sort=InventorySort.NAME_ASC
                )
            results[warehouse.code] = count
    logging.info(
        "Low-stock job completed job_id=%s warehouse_counts=%s",
        correlation_id,
        results,
    )
    return results


async def _main() -> None:
    """Run one low-stock check from the command line."""
    results = await run_low_stock_check()
    print(results)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
