"""Outbound order persistence and response queries."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import logger
from backend.core.apis.schemas.common import (
    CreatedAtCursor,
    CreatedAtSort,
    CursorPage,
    encode_created_at_cursor,
)
from backend.core.cruds.pagination import apply_created_at_pagination
from backend.core.models.enums import OrderStatus
from backend.core.models.order import (
    InventoryReservation,
    Order,
    OrderItem,
    OutboundPackage,
)
from backend.core.models.product import Product

logging = logger(__name__)


class OrderCRUD:
    """Persistence wrapper for order state, lines, reservations, and packages."""

    async def get_order(
        self, session: AsyncSession, order_id: uuid.UUID, *, for_update: bool = False
    ) -> Order | None:
        """Read an order with an optional state-machine row lock.

        Args:
            session: Request-scoped database session.
            order_id: Order identifier.
            for_update: Lock the order for a mutating transition.

        Returns:
            Order model or None.
        """
        logging.info("Executing OrderCRUD.get_order")
        statement = select(Order).where(Order.id == order_id)
        if for_update:
            statement = statement.with_for_update()
        return (await session.execute(statement)).scalar_one_or_none()

    async def list_reservations(
        self, session: AsyncSession, order_id: uuid.UUID, *, for_update: bool = False
    ) -> list[InventoryReservation]:
        """List reservations for an order in deterministic product order.

        Args:
            session: Request-scoped database session.
            order_id: Parent order identifier.
            for_update: Lock reservation rows for cancellation or shipment.

        Returns:
            Reservation models.
        """
        logging.info("Executing OrderCRUD.list_reservations")
        statement = (
            select(InventoryReservation)
            .where(InventoryReservation.order_id == order_id)
            .order_by(InventoryReservation.product_id)
        )
        if for_update:
            statement = statement.with_for_update()
        return list((await session.scalars(statement)).all())

    async def get_package(
        self, session: AsyncSession, order_id: uuid.UUID, *, for_update: bool = False
    ) -> OutboundPackage | None:
        """Read the MVP single package for an order.

        Args:
            session: Request-scoped database session.
            order_id: Parent order identifier.
            for_update: Lock the package for a transition.

        Returns:
            Package model or None.
        """
        logging.info("Executing OrderCRUD.get_package")
        statement = select(OutboundPackage).where(OutboundPackage.order_id == order_id)
        if for_update:
            statement = statement.with_for_update()
        return (await session.execute(statement)).scalar_one_or_none()

    async def serialize_order(
        self,
        session: AsyncSession,
        order: Order,
        *,
        shortages: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Build an order response with product lines and package state.

        Args:
            session: Request-scoped database session.
            order: Order model to serialize.
            shortages: Optional allocation shortage descriptions.

        Returns:
            Order response dictionary.
        """
        logging.info("Executing OrderCRUD.serialize_order")
        rows = (
            await session.execute(
                select(OrderItem, Product)
                .join(Product, Product.id == OrderItem.product_id)
                .where(OrderItem.order_id == order.id)
                .order_by(Product.name)
            )
        ).all()
        package = await self.get_package(session, order.id)
        package_payload = None
        if package is not None:
            package_payload = {
                "id": package.id,
                "weight": package.weight,
                "weight_unit": package.weight_unit,
                "length": package.length,
                "width": package.width,
                "height": package.height,
                "dimension_unit": package.dimension_unit,
                "carrier": package.carrier,
                "service_level": package.service_level,
                "tracking_number": package.tracking_number,
                "label_url_or_key": package.label_url_or_key,
                "status": package.status,
            }
        return {
            "id": order.id,
            "external_reference": order.external_reference,
            "warehouse_id": order.warehouse_id,
            "status": order.status,
            "items": [
                {
                    "id": item.id,
                    "product_id": item.product_id,
                    "sku": product.sku,
                    "product_name": product.name,
                    "quantity": item.quantity,
                    "picked_quantity": item.picked_quantity,
                }
                for item, product in rows
            ],
            "package": package_payload,
            "shortages": shortages or [],
            "cancel_reason": order.cancel_reason,
            "created_at": order.created_at,
        }

    async def list_orders(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID,
        limit: int,
        status: OrderStatus | None,
        created_from: datetime | None,
        created_to: datetime | None,
        cursor: CreatedAtCursor | None,
        sort: CreatedAtSort,
    ) -> CursorPage[dict[str, Any]]:
        """List a filtered keyset page within one warehouse scope.

        Args:
            session: Request-scoped database session.
            warehouse_id: Authorized warehouse identifier.
            limit: Maximum order count.
            status: Optional exact lifecycle status.
            created_from: Inclusive creation-time lower bound.
            created_to: Inclusive creation-time upper bound.
            cursor: Exclusive prior-page position.
            sort: Deterministic creation-time order.

        Returns:
            Expanded orders and an opaque next cursor.
        """
        logging.info("Executing OrderCRUD.list_orders")
        statement = select(Order).where(Order.warehouse_id == warehouse_id)
        if status is not None:
            statement = statement.where(Order.status == status)
        statement = apply_created_at_pagination(
            statement,
            created_at_column=Order.created_at,
            id_column=Order.id,
            created_from=created_from,
            created_to=created_to,
            cursor=cursor,
            sort=sort,
        ).limit(limit + 1)
        orders = list((await session.scalars(statement)).all())
        has_more = len(orders) > limit
        visible_orders = orders[:limit]
        next_cursor = None
        if has_more:
            last_order = visible_orders[-1]
            next_cursor = encode_created_at_cursor(
                created_at=last_order.created_at,
                record_id=last_order.id,
                sort=sort,
            )
        return CursorPage(
            items=[
                await self.serialize_order(session, order) for order in visible_orders
            ],
            next_cursor=next_cursor,
        )
