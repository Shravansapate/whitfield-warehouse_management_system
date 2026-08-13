"""Product master and threshold persistence operations."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import logger
from backend.core.apis.schemas.common import (
    CreatedAtCursor,
    CreatedAtSort,
    CursorPage,
    encode_created_at_cursor,
)
from backend.core.cruds.pagination import apply_created_at_pagination
from backend.core.models.product import Product, WarehouseProductSetting

logging = logger(__name__)


class ProductCRUD:
    """Persistence wrapper for products and warehouse settings."""

    async def search(
        self, session: AsyncSession, *, query: str, limit: int = 30
    ) -> list[Product]:
        """Search active products by UPC, SKU, or name.

        Args:
            session: Request-scoped database session.
            query: Scanner or human search value.
            limit: Maximum result count.

        Returns:
            Matching active products.
        """
        logging.info("Executing ProductCRUD.search")
        normalized = query.strip()
        statement = select(Product).where(Product.is_active.is_(True))
        if normalized:
            like = f"%{normalized}%"
            statement = statement.where(
                or_(
                    Product.upc == normalized,
                    Product.sku.ilike(like),
                    Product.name.ilike(like),
                )
            )
        statement = statement.order_by(
            (Product.upc == normalized).desc(),
            (func.lower(Product.sku) == normalized.lower()).desc(),
            Product.name,
        ).limit(limit)
        return list((await session.scalars(statement)).all())

    async def search_page(
        self,
        session: AsyncSession,
        *,
        query: str,
        is_active: bool | None,
        created_from: datetime | None,
        created_to: datetime | None,
        cursor: CreatedAtCursor | None,
        sort: CreatedAtSort,
        limit: int,
    ) -> CursorPage[Product]:
        """Search a filtered product-master keyset page.

        Creation time and UUID form the stable page boundary.

        Args:
            session: Request-scoped database session.
            query: Scanner or human search value.
            is_active: Optional exact active-state filter.
            created_from: Inclusive creation-time lower bound.
            created_to: Inclusive creation-time upper bound.
            cursor: Exclusive prior-page position.
            sort: Deterministic creation-time order.
            limit: Maximum product count.

        Returns:
            Product models and an opaque next cursor.
        """
        logging.info("Executing ProductCRUD.search_page")
        statement = select(Product)
        if is_active is not None:
            statement = statement.where(Product.is_active.is_(is_active))
        normalized = query.strip()
        if normalized:
            like = f"%{normalized}%"
            statement = statement.where(
                or_(
                    Product.upc == normalized,
                    Product.sku.ilike(like),
                    Product.name.ilike(like),
                )
            )
        statement = apply_created_at_pagination(
            statement,
            created_at_column=Product.created_at,
            id_column=Product.id,
            created_from=created_from,
            created_to=created_to,
            cursor=cursor,
            sort=sort,
        ).limit(limit + 1)
        products = list((await session.scalars(statement)).all())
        has_more = len(products) > limit
        visible_products = products[:limit]
        next_cursor = None
        if has_more:
            last_product = visible_products[-1]
            next_cursor = encode_created_at_cursor(
                created_at=last_product.created_at,
                record_id=last_product.id,
                sort=sort,
            )
        return CursorPage(items=visible_products, next_cursor=next_cursor)

    async def set_threshold(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID,
        product_id: uuid.UUID,
        threshold: int,
    ) -> WarehouseProductSetting:
        """Create or update one warehouse product threshold.

        Args:
            session: Active settings transaction.
            warehouse_id: Warehouse owning the threshold.
            product_id: Product receiving the threshold.
            threshold: Nonnegative low-stock threshold.

        Returns:
            Persisted warehouse setting.
        """
        logging.info("Executing ProductCRUD.set_threshold")
        statement = (
            insert(WarehouseProductSetting)
            .values(
                id=uuid.uuid4(),
                warehouse_id=warehouse_id,
                product_id=product_id,
                low_stock_threshold=threshold,
            )
            .on_conflict_do_update(
                constraint="uq_warehouse_product_setting",
                set_={"low_stock_threshold": threshold},
            )
            .returning(WarehouseProductSetting.id)
        )
        setting_id = (await session.execute(statement)).scalar_one()
        setting = await session.get(WarehouseProductSetting, setting_id)
        if setting is None:
            raise RuntimeError("Threshold setting could not be loaded")
        return setting
