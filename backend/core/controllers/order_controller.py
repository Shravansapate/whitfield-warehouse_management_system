"""Outbound order query orchestration controller."""

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
    decode_created_at_cursor,
    validate_created_at_filters,
)
from backend.core.cruds.order_crud import OrderCRUD
from backend.core.models.enums import OrderStatus, UserRole

logging = logger(__name__)


class OrderController:
    """Authorize outbound order list and detail queries."""

    def __init__(self) -> None:
        """Initialize order query collaborator."""
        self.orders = OrderCRUD()

    async def list_orders(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID | None,
        limit: int,
        status: OrderStatus | None,
        created_from: datetime | None,
        created_to: datetime | None,
        cursor: str | None,
        sort: CreatedAtSort,
        user: CurrentUser,
    ) -> CursorPage[dict]:
        """List a filtered keyset page in an authorized warehouse.

        Args:
            session: Request-scoped database session.
            warehouse_id: Requested warehouse identifier.
            limit: Maximum order count.
            status: Optional exact lifecycle status.
            created_from: Inclusive creation-time lower bound.
            created_to: Inclusive creation-time upper bound.
            cursor: Opaque prior-page position.
            sort: Deterministic creation-time order.
            user: Authenticated actor.

        Returns:
            Expanded orders and an opaque next cursor.

        Raises:
            HTTPException 422: If date filters or the cursor are invalid.
        """
        logging.info("Executing OrderController.list_orders")
        resolved = resolve_warehouse_id(user, warehouse_id)
        try:
            validate_created_at_filters(
                created_from=created_from, created_to=created_to
            )
            decoded_cursor = decode_created_at_cursor(cursor, sort=sort)
        except ValueError as error:
            logging.warning("Rejected invalid order-list pagination filters")
            raise HTTPException(
                status_code=422,
                detail={
                    "detail": str(error),
                    "code": "INVALID_PAGINATION",
                },
            ) from error
        return await self.orders.list_orders(
            session,
            warehouse_id=resolved,
            limit=limit,
            status=status,
            created_from=created_from,
            created_to=created_to,
            cursor=decoded_cursor,
            sort=sort,
        )

    async def get_order(
        self, session: AsyncSession, *, order_id: uuid.UUID, user: CurrentUser
    ) -> dict:
        """Read one order after enforcing warehouse scope.

        Args:
            session: Request-scoped database session.
            order_id: Order identifier.
            user: Authenticated actor.

        Returns:
            Expanded order response.
        """
        logging.info("Executing OrderController.get_order")
        order = await self.orders.get_order(session, order_id)
        if order is None:
            raise HTTPException(
                status_code=404,
                detail={"detail": "Order not found", "code": "ORDER_NOT_FOUND"},
            )
        if user.role != UserRole.OWNER and user.warehouse_id != order.warehouse_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "detail": "Cross-warehouse access is not allowed",
                    "code": "WAREHOUSE_FORBIDDEN",
                },
            )
        return await self.orders.serialize_order(session, order)
