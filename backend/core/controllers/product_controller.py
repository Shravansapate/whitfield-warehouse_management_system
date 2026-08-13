"""Product master and threshold orchestration controller."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.commons.auth import CurrentUser, resolve_warehouse_id
from backend.core import logger
from backend.core.apis.schemas.common import (
    CreatedAtSort,
    CursorPage,
    decode_created_at_cursor,
    validate_created_at_filters,
)
from backend.core.cruds.product_crud import ProductCRUD
from backend.core.cruds.reliability_crud import ReliabilityCRUD
from backend.core.models.enums import AuditSource
from backend.core.models.product import Product
from backend.core.services.transaction import command_transaction

logging = logger(__name__)


class ProductController:
    """Manage product master data and warehouse low-stock settings."""

    def __init__(self) -> None:
        """Initialize product and audit persistence collaborators."""
        self.products = ProductCRUD()
        self.reliability = ReliabilityCRUD()

    async def search(
        self,
        session: AsyncSession,
        *,
        query: str,
        is_active: bool | None,
        created_from: datetime | None,
        created_to: datetime | None,
        cursor: str | None,
        sort: CreatedAtSort,
        limit: int,
    ) -> CursorPage[Product]:
        """Search a filtered product-master cursor page.

        Dates and the opaque page position are validated before querying.

        Args:
            session: Request-scoped database session.
            query: Scanner or human search value.
            is_active: Optional exact active-state filter.
            created_from: Inclusive creation-time lower bound.
            created_to: Inclusive creation-time upper bound.
            cursor: Opaque prior-page position.
            sort: Deterministic creation-time order.
            limit: Maximum product count.

        Returns:
            Product models and an opaque next cursor.

        Raises:
            HTTPException 422: If dates or the cursor are invalid.
        """
        logging.info("Executing ProductController.search")
        try:
            validate_created_at_filters(
                created_from=created_from, created_to=created_to
            )
            decoded_cursor = decode_created_at_cursor(cursor, sort=sort)
        except ValueError as error:
            logging.warning("Rejected invalid product-list pagination filters")
            raise HTTPException(
                status_code=422,
                detail={"detail": str(error), "code": "INVALID_PAGINATION"},
            ) from error
        return await self.products.search_page(
            session,
            query=query,
            is_active=is_active,
            created_from=created_from,
            created_to=created_to,
            cursor=decoded_cursor,
            sort=sort,
            limit=limit,
        )

    async def create(
        self,
        session: AsyncSession,
        *,
        data: dict,
        user: CurrentUser,
        request_id: str,
    ) -> Product:
        """Create a unique product master record.

        Args:
            session: Request-scoped database session.
            data: Validated product fields.
            user: Owner actor.
            request_id: Correlation identifier.

        Returns:
            Created product model.
        """
        logging.info("Executing ProductController.create")
        async with command_transaction(session):
            duplicate = (
                await session.execute(
                    select(Product.id).where(
                        or_(
                            Product.sku == data["sku"].strip(),
                            Product.upc == data["upc"].strip(),
                        )
                    )
                )
            ).scalar_one_or_none()
            if duplicate:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "SKU or UPC already exists",
                        "code": "PRODUCT_EXISTS",
                    },
                )
            product = Product(
                sku=data["sku"].strip(),
                upc=data["upc"].strip(),
                name=data["name"].strip(),
                description=data.get("description"),
                is_active=True,
            )
            session.add(product)
            await session.flush()
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=None,
                table_name="products",
                record_id=product.id,
                action="PRODUCT_CREATED",
                request_id=request_id,
                source=AuditSource.WEB,
                after_value={
                    "sku": product.sku,
                    "upc": product.upc,
                    "name": product.name,
                },
            )
        return product

    async def update(
        self,
        session: AsyncSession,
        *,
        product_id: uuid.UUID,
        data: dict,
        user: CurrentUser,
        request_id: str,
    ) -> Product:
        """Update product master fields without deleting history.

        Args:
            session: Request-scoped database session.
            product_id: Product identifier.
            data: Explicit fields to update.
            user: Owner actor.
            request_id: Correlation identifier.

        Returns:
            Updated product model.
        """
        logging.info("Executing ProductController.update")
        async with command_transaction(session):
            product = (
                await session.execute(
                    select(Product).where(Product.id == product_id).with_for_update()
                )
            ).scalar_one_or_none()
            if product is None:
                raise HTTPException(
                    status_code=404,
                    detail={"detail": "Product not found", "code": "PRODUCT_NOT_FOUND"},
                )
            before = {
                "sku": product.sku,
                "upc": product.upc,
                "name": product.name,
                "is_active": product.is_active,
            }
            for field, value in data.items():
                setattr(
                    product, field, value.strip() if isinstance(value, str) else value
                )
            await session.flush()
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=None,
                table_name="products",
                record_id=product.id,
                action="PRODUCT_UPDATED",
                request_id=request_id,
                source=AuditSource.WEB,
                before_value=before,
                after_value={
                    "sku": product.sku,
                    "upc": product.upc,
                    "name": product.name,
                    "is_active": product.is_active,
                },
            )
        return product

    async def set_threshold(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID,
        product_id: uuid.UUID,
        threshold: int,
        user: CurrentUser,
        request_id: str,
    ) -> dict:
        """Set a low-stock threshold within the actor's warehouse scope.

        Args:
            session: Request-scoped database session.
            warehouse_id: Requested warehouse identifier.
            product_id: Product identifier.
            threshold: Nonnegative threshold.
            user: Manager or owner actor.
            request_id: Correlation identifier.

        Returns:
            Updated threshold payload.
        """
        logging.info("Executing ProductController.set_threshold")
        resolved_warehouse = resolve_warehouse_id(user, warehouse_id)
        async with command_transaction(session):
            product = await session.get(Product, product_id)
            if product is None:
                raise HTTPException(
                    status_code=404,
                    detail={"detail": "Product not found", "code": "PRODUCT_NOT_FOUND"},
                )
            setting = await self.products.set_threshold(
                session,
                warehouse_id=resolved_warehouse,
                product_id=product_id,
                threshold=threshold,
            )
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=resolved_warehouse,
                table_name="warehouse_product_settings",
                record_id=setting.id,
                action="LOW_STOCK_THRESHOLD_SET",
                request_id=request_id,
                source=AuditSource.WEB,
                after_value={
                    "product_id": str(product_id),
                    "low_stock_threshold": threshold,
                },
            )
        return {
            "warehouse_id": resolved_warehouse,
            "product_id": product_id,
            "low_stock_threshold": threshold,
        }
