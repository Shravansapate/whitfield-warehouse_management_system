"""Inventory query orchestration controller."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.commons.auth import CurrentUser, resolve_warehouse_id
from backend.core import logger
from backend.core.apis.schemas.common import (
    CreatedAtSort,
    CursorPage,
    InventorySort,
    decode_created_at_cursor,
    decode_scalar_cursor,
    validate_created_at_filters,
)
from backend.core.cruds.inventory_crud import InventoryCRUD
from backend.core.models.enums import MovementType
from backend.core.models.inventory import InventoryMovement

logging = logger(__name__)


class InventoryController:
    """Authorize inventory balance and immutable movement queries."""

    def __init__(self) -> None:
        """Initialize inventory query collaborator."""
        self.inventory = InventoryCRUD()

    async def list_inventory(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID | None,
        user: CurrentUser,
        low_stock_only: bool = False,
        query: str | None = None,
        cursor: str | None = None,
        sort: InventorySort = InventorySort.NAME_ASC,
        limit: int = 200,
    ) -> CursorPage[dict]:
        """List a filtered balance cursor page in an authorized warehouse.

        Warehouse scope is enforced before an opaque cursor is parsed.

        Args:
            session: Request-scoped database session.
            warehouse_id: Requested warehouse identifier.
            user: Authenticated actor.
            low_stock_only: Filter to low-stock products.
            query: Optional product search value.
            cursor: Opaque prior-page position.
            sort: Product-name or availability order.
            limit: Maximum balance rows returned.

        Returns:
            Inventory rows and an opaque next cursor.

        Raises:
            HTTPException 422: If the cursor is malformed or incompatible.
        """
        logging.info("Executing InventoryController.list_inventory")
        resolved = resolve_warehouse_id(user, warehouse_id)
        try:
            decoded_cursor = decode_scalar_cursor(cursor, sort=sort)
        except ValueError as error:
            logging.warning("Rejected invalid inventory-list pagination cursor")
            raise HTTPException(
                status_code=422,
                detail={"detail": str(error), "code": "INVALID_PAGINATION"},
            ) from error
        return await self.inventory.list_inventory(
            session,
            warehouse_id=resolved,
            low_stock_only=low_stock_only,
            query=query,
            cursor=decoded_cursor,
            sort=sort,
            limit=limit,
        )

    async def get_inventory(
        self,
        session: AsyncSession,
        *,
        product_id: uuid.UUID,
        warehouse_id: uuid.UUID | None,
        user: CurrentUser,
    ) -> dict:
        """Read one product balance in an authorized warehouse.

        Warehouse scope is enforced before persistence is queried.

        Args:
            session: Request-scoped database session.
            product_id: Product identifier.
            warehouse_id: Requested warehouse identifier.
            user: Authenticated actor.

        Returns:
            Inventory response row.

        Raises:
            HTTPException 404: If the active product does not exist.
        """
        logging.info("Executing InventoryController.get_inventory")
        resolved = resolve_warehouse_id(user, warehouse_id)
        row = await self.inventory.get_inventory(
            session,
            warehouse_id=resolved,
            product_id=product_id,
        )
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "detail": "Inventory product not found",
                    "code": "INVENTORY_NOT_FOUND",
                },
            )
        return row

    async def movements(
        self,
        session: AsyncSession,
        *,
        product_id: uuid.UUID,
        warehouse_id: uuid.UUID | None,
        user: CurrentUser,
        movement_type: MovementType | None,
        created_from: datetime | None,
        created_to: datetime | None,
        cursor: str | None,
        sort: CreatedAtSort,
        limit: int,
    ) -> CursorPage[InventoryMovement]:
        """List a filtered movement page for one product and warehouse.

        Warehouse scope is enforced before filters or cursors are parsed.

        Args:
            session: Request-scoped database session.
            product_id: Product identifier.
            warehouse_id: Requested warehouse identifier.
            user: Authenticated actor.
            movement_type: Optional exact movement-kind filter.
            created_from: Inclusive creation-time lower bound.
            created_to: Inclusive creation-time upper bound.
            cursor: Opaque prior-page position.
            sort: Deterministic creation-time order.
            limit: Maximum event count.

        Returns:
            Movement models and an opaque next cursor.

        Raises:
            HTTPException 422: If dates or the cursor are invalid.
        """
        logging.info("Executing InventoryController.movements")
        resolved = resolve_warehouse_id(user, warehouse_id)
        try:
            validate_created_at_filters(
                created_from=created_from, created_to=created_to
            )
            decoded_cursor = decode_created_at_cursor(cursor, sort=sort)
        except ValueError as error:
            logging.warning("Rejected invalid movement-list pagination filters")
            raise HTTPException(
                status_code=422,
                detail={"detail": str(error), "code": "INVALID_PAGINATION"},
            ) from error
        return await self.inventory.list_movements(
            session,
            warehouse_id=resolved,
            product_id=product_id,
            movement_type=movement_type,
            created_from=created_from,
            created_to=created_to,
            cursor=decoded_cursor,
            sort=sort,
            limit=limit,
        )
