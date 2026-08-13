"""Atomic PostgreSQL inventory persistence operations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import logger
from backend.core.apis.schemas.common import (
    CreatedAtCursor,
    CreatedAtSort,
    CursorPage,
    InventorySort,
    ScalarCursor,
    encode_created_at_cursor,
    encode_scalar_cursor,
)
from backend.core.cruds.pagination import (
    apply_created_at_pagination,
    apply_scalar_pagination,
)
from backend.core.models.enums import AuditSource, MovementType
from backend.core.models.inventory import InventoryBalance, InventoryMovement
from backend.core.models.product import Product, WarehouseProductSetting

logging = logger(__name__)


class InventoryCRUD:
    """Balance queries and guarded atomic stock updates."""

    async def add_on_hand(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID,
        product_id: uuid.UUID,
        quantity: int,
    ) -> tuple[int, int]:
        """Atomically create or increment an on-hand balance.

        Args:
            session: Active business transaction session.
            warehouse_id: Stock-owning warehouse.
            product_id: Product receiving stock.
            quantity: Positive accepted quantity.

        Returns:
            Post-update on-hand and reserved quantities.
        """
        logging.info("Executing InventoryCRUD.add_on_hand")
        statement = (
            insert(InventoryBalance)
            .values(
                id=uuid.uuid4(),
                warehouse_id=warehouse_id,
                product_id=product_id,
                on_hand=quantity,
                reserved=0,
            )
            .on_conflict_do_update(
                constraint="uq_inventory_warehouse_product",
                set_={
                    "on_hand": InventoryBalance.on_hand + quantity,
                    "updated_at": func.now(),
                },
            )
            .returning(InventoryBalance.on_hand, InventoryBalance.reserved)
        )
        row = (await session.execute(statement)).one()
        return int(row.on_hand), int(row.reserved)

    async def create_opening_balance(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID,
        product_id: uuid.UUID,
        quantity: int,
    ) -> InventoryBalance | None:
        """Atomically create, but never increment, one opening balance.

        Args:
            session: Active business transaction session.
            warehouse_id: Stock-owning warehouse.
            product_id: Product receiving verified opening stock.
            quantity: Positive opening quantity.

        Returns:
            Newly persisted balance, or None if one already exists.
        """
        logging.info("Executing InventoryCRUD.create_opening_balance")
        balance_id = uuid.uuid4()
        inserted_id = (
            await session.execute(
                insert(InventoryBalance)
                .values(
                    id=balance_id,
                    warehouse_id=warehouse_id,
                    product_id=product_id,
                    on_hand=quantity,
                    reserved=0,
                )
                .on_conflict_do_nothing(constraint="uq_inventory_warehouse_product")
                .returning(InventoryBalance.id)
            )
        ).scalar_one_or_none()
        if inserted_id is None:
            return None
        return await session.get(InventoryBalance, inserted_id)

    async def reserve(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID,
        product_id: uuid.UUID,
        quantity: int,
    ) -> tuple[int, int] | None:
        """Reserve stock only when availability is sufficient.

        Implements the concurrency-safe conditional UPDATE required by the plan.

        Args:
            session: Active allocation transaction.
            warehouse_id: Selected fulfillment warehouse.
            product_id: Product to reserve.
            quantity: Positive reservation quantity.

        Returns:
            Post-update balance, or None when stock is insufficient.
        """
        logging.info("Executing InventoryCRUD.reserve")
        statement = (
            update(InventoryBalance)
            .where(
                InventoryBalance.warehouse_id == warehouse_id,
                InventoryBalance.product_id == product_id,
                (InventoryBalance.on_hand - InventoryBalance.reserved) >= quantity,
            )
            .values(
                reserved=InventoryBalance.reserved + quantity, updated_at=func.now()
            )
            .returning(InventoryBalance.on_hand, InventoryBalance.reserved)
        )
        row = (await session.execute(statement)).one_or_none()
        return (int(row.on_hand), int(row.reserved)) if row else None

    async def release(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID,
        product_id: uuid.UUID,
        quantity: int,
    ) -> tuple[int, int] | None:
        """Release an active reservation with a guarded update.

        Args:
            session: Active cancellation transaction.
            warehouse_id: Reservation warehouse.
            product_id: Reserved product.
            quantity: Quantity to release.

        Returns:
            Post-update balance, or None if reservation state is inconsistent.
        """
        logging.info("Executing InventoryCRUD.release")
        statement = (
            update(InventoryBalance)
            .where(
                InventoryBalance.warehouse_id == warehouse_id,
                InventoryBalance.product_id == product_id,
                InventoryBalance.reserved >= quantity,
            )
            .values(
                reserved=InventoryBalance.reserved - quantity, updated_at=func.now()
            )
            .returning(InventoryBalance.on_hand, InventoryBalance.reserved)
        )
        row = (await session.execute(statement)).one_or_none()
        return (int(row.on_hand), int(row.reserved)) if row else None

    async def consume(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID,
        product_id: uuid.UUID,
        quantity: int,
    ) -> tuple[int, int] | None:
        """Consume reserved units from both on-hand and reserved stock.

        Args:
            session: Active shipment transaction.
            warehouse_id: Fulfillment warehouse.
            product_id: Shipped product.
            quantity: Reserved quantity being shipped.

        Returns:
            Post-update balance, or None if guards fail.
        """
        logging.info("Executing InventoryCRUD.consume")
        statement = (
            update(InventoryBalance)
            .where(
                InventoryBalance.warehouse_id == warehouse_id,
                InventoryBalance.product_id == product_id,
                InventoryBalance.reserved >= quantity,
                InventoryBalance.on_hand >= quantity,
            )
            .values(
                on_hand=InventoryBalance.on_hand - quantity,
                reserved=InventoryBalance.reserved - quantity,
                updated_at=func.now(),
            )
            .returning(InventoryBalance.on_hand, InventoryBalance.reserved)
        )
        row = (await session.execute(statement)).one_or_none()
        return (int(row.on_hand), int(row.reserved)) if row else None

    async def adjust_on_hand(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID,
        product_id: uuid.UUID,
        quantity_delta: int,
    ) -> tuple[int, int] | None:
        """Apply a manual delta without dropping below reserved stock.

        Args:
            session: Active adjustment transaction.
            warehouse_id: Stock-owning warehouse.
            product_id: Adjusted product.
            quantity_delta: Nonzero signed quantity change.

        Returns:
            Post-update balance, or None when the negative delta is unsafe.
        """
        logging.info("Executing InventoryCRUD.adjust_on_hand")
        if quantity_delta > 0:
            return await self.add_on_hand(
                session,
                warehouse_id=warehouse_id,
                product_id=product_id,
                quantity=quantity_delta,
            )
        statement = (
            update(InventoryBalance)
            .where(
                InventoryBalance.warehouse_id == warehouse_id,
                InventoryBalance.product_id == product_id,
                InventoryBalance.on_hand + quantity_delta >= InventoryBalance.reserved,
            )
            .values(
                on_hand=InventoryBalance.on_hand + quantity_delta, updated_at=func.now()
            )
            .returning(InventoryBalance.on_hand, InventoryBalance.reserved)
        )
        row = (await session.execute(statement)).one_or_none()
        return (int(row.on_hand), int(row.reserved)) if row else None

    async def add_movement(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID,
        product_id: uuid.UUID,
        movement_type: MovementType,
        on_hand_delta: int,
        reserved_delta: int,
        reference_type: str,
        reference_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        source: AuditSource,
        on_hand_after: int,
        reserved_after: int,
        reason: str | None = None,
    ) -> InventoryMovement:
        """Append a balance movement inside the active transaction.

        Args:
            session: Active business transaction session.
            warehouse_id: Movement warehouse.
            product_id: Movement product.
            movement_type: Domain movement kind.
            on_hand_delta: Signed on-hand change.
            reserved_delta: Signed reserved change.
            reference_type: Originating business resource type.
            reference_id: Originating resource identifier.
            actor_user_id: User responsible for the movement.
            source: Originating client channel.
            on_hand_after: Resulting on-hand quantity.
            reserved_after: Resulting reserved quantity.
            reason: Optional human explanation.

        Returns:
            Pending immutable movement model.
        """
        logging.info("Executing InventoryCRUD.add_movement")
        movement = InventoryMovement(
            warehouse_id=warehouse_id,
            product_id=product_id,
            movement_type=movement_type,
            on_hand_delta=on_hand_delta,
            reserved_delta=reserved_delta,
            reference_type=reference_type,
            reference_id=reference_id,
            actor_user_id=actor_user_id,
            source=source,
            reason=reason,
            on_hand_after=on_hand_after,
            reserved_after=reserved_after,
        )
        session.add(movement)
        await session.flush()
        return movement

    async def list_inventory(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID,
        low_stock_only: bool = False,
        query: str | None = None,
        cursor: ScalarCursor | None = None,
        sort: InventorySort = InventorySort.NAME_ASC,
        limit: int = 200,
    ) -> CursorPage[dict[str, Any]]:
        """List a filtered inventory keyset page for one warehouse.

        Includes active products with zero stock so master data remains visible.

        Args:
            session: Request-scoped database session.
            warehouse_id: Authorized warehouse scope.
            low_stock_only: Return only rows at or below threshold.
            query: Optional case-insensitive SKU, UPC, or name search.
            cursor: Exclusive prior-page scalar position.
            sort: Deterministic product-name or availability order.
            limit: Maximum balance rows returned.

        Returns:
            Inventory rows and an opaque next cursor.
        """
        logging.info("Executing InventoryCRUD.list_inventory")
        available = func.coalesce(InventoryBalance.on_hand, 0) - func.coalesce(
            InventoryBalance.reserved, 0
        )
        threshold = func.coalesce(WarehouseProductSetting.low_stock_threshold, 0)
        statement = (
            select(
                Product.id.label("product_id"),
                Product.sku,
                Product.upc,
                Product.name,
                func.coalesce(InventoryBalance.on_hand, 0).label("on_hand"),
                func.coalesce(InventoryBalance.reserved, 0).label("reserved"),
                available.label("available"),
                threshold.label("low_stock_threshold"),
            )
            .outerjoin(
                InventoryBalance,
                and_(
                    InventoryBalance.product_id == Product.id,
                    InventoryBalance.warehouse_id == warehouse_id,
                ),
            )
            .outerjoin(
                WarehouseProductSetting,
                and_(
                    WarehouseProductSetting.product_id == Product.id,
                    WarehouseProductSetting.warehouse_id == warehouse_id,
                ),
            )
            .where(Product.is_active.is_(True))
        )
        if low_stock_only:
            statement = statement.where(available <= threshold)
        normalized = (query or "").strip()
        if normalized:
            like = f"%{normalized}%"
            statement = statement.where(
                or_(
                    Product.upc == normalized,
                    Product.sku.ilike(like),
                    Product.name.ilike(like),
                )
            )
        sort_value: Any
        if sort in {InventorySort.NAME_ASC, InventorySort.NAME_DESC}:
            sort_value = func.lower(Product.name)
        else:
            sort_value = available
        statement = statement.add_columns(sort_value.label("_sort_value"))
        descending = sort in {
            InventorySort.NAME_DESC,
            InventorySort.AVAILABLE_DESC,
        }
        if sort in {InventorySort.AVAILABLE_ASC, InventorySort.AVAILABLE_DESC}:
            if cursor is not None and not isinstance(cursor.value, int):
                raise ValueError("Invalid pagination cursor")
            numeric_cursor = (
                ScalarCursor(value=int(cursor.value), record_id=cursor.record_id)
                if cursor is not None
                else None
            )
            statement = apply_scalar_pagination(
                statement,
                value_column=available,
                id_column=Product.id,
                cursor=numeric_cursor,
                descending=descending,
            )
        else:
            if cursor is not None and not isinstance(cursor.value, str):
                raise ValueError("Invalid pagination cursor")
            statement = apply_scalar_pagination(
                statement,
                value_column=sort_value,
                id_column=Product.id,
                cursor=cursor,
                descending=descending,
            )
        rows = (await session.execute(statement.limit(limit + 1))).mappings().all()
        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        next_cursor = None
        if has_more:
            last_row = visible_rows[-1]
            cursor_value: str | int = (
                int(last_row._sort_value)
                if sort in {InventorySort.AVAILABLE_ASC, InventorySort.AVAILABLE_DESC}
                else str(last_row._sort_value)
            )
            next_cursor = encode_scalar_cursor(
                value=cursor_value,
                record_id=last_row.product_id,
                sort=sort,
            )
        items: list[dict[str, Any]] = []
        for row in visible_rows:
            item = dict(row)
            item.pop("_sort_value")
            items.append(
                {
                    "warehouse_id": warehouse_id,
                    **item,
                    "is_low_stock": int(row.available) <= int(row.low_stock_threshold),
                }
            )
        return CursorPage(items=items, next_cursor=next_cursor)

    async def get_inventory(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID,
        product_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        """Read one active product's balance in a warehouse.

        Products without a balance are returned with zero quantities.

        Args:
            session: Request-scoped database session.
            warehouse_id: Authorized warehouse scope.
            product_id: Product identifier.

        Returns:
            Inventory response row when the active product exists, otherwise None.
        """
        logging.info("Executing InventoryCRUD.get_inventory")
        available = func.coalesce(InventoryBalance.on_hand, 0) - func.coalesce(
            InventoryBalance.reserved, 0
        )
        threshold = func.coalesce(WarehouseProductSetting.low_stock_threshold, 0)
        row = (
            (
                await session.execute(
                    select(
                        Product.id.label("product_id"),
                        Product.sku,
                        Product.upc,
                        Product.name,
                        func.coalesce(InventoryBalance.on_hand, 0).label("on_hand"),
                        func.coalesce(InventoryBalance.reserved, 0).label("reserved"),
                        available.label("available"),
                        threshold.label("low_stock_threshold"),
                    )
                    .outerjoin(
                        InventoryBalance,
                        and_(
                            InventoryBalance.product_id == Product.id,
                            InventoryBalance.warehouse_id == warehouse_id,
                        ),
                    )
                    .outerjoin(
                        WarehouseProductSetting,
                        and_(
                            WarehouseProductSetting.product_id == Product.id,
                            WarehouseProductSetting.warehouse_id == warehouse_id,
                        ),
                    )
                    .where(
                        Product.id == product_id,
                        Product.is_active.is_(True),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return {
            "warehouse_id": warehouse_id,
            **dict(row),
            "is_low_stock": int(row.available) <= int(row.low_stock_threshold),
        }

    async def list_movements(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID,
        product_id: uuid.UUID,
        movement_type: MovementType | None,
        created_from: datetime | None,
        created_to: datetime | None,
        cursor: CreatedAtCursor | None,
        sort: CreatedAtSort,
        limit: int,
    ) -> CursorPage[InventoryMovement]:
        """List a filtered immutable movement keyset page.

        Creation time and UUID form the stable page boundary.

        Args:
            session: Request-scoped database session.
            warehouse_id: Authorized warehouse scope.
            product_id: Product whose ledger is requested.
            movement_type: Optional exact movement-kind filter.
            created_from: Inclusive creation-time lower bound.
            created_to: Inclusive creation-time upper bound.
            cursor: Exclusive prior-page position.
            sort: Deterministic creation-time order.
            limit: Maximum ledger entries returned.

        Returns:
            Movement models and an opaque next cursor.
        """
        logging.info("Executing InventoryCRUD.list_movements")
        statement = select(InventoryMovement).where(
            InventoryMovement.warehouse_id == warehouse_id,
            InventoryMovement.product_id == product_id,
        )
        if movement_type is not None:
            statement = statement.where(
                InventoryMovement.movement_type == movement_type
            )
        statement = apply_created_at_pagination(
            statement,
            created_at_column=InventoryMovement.created_at,
            id_column=InventoryMovement.id,
            created_from=created_from,
            created_to=created_to,
            cursor=cursor,
            sort=sort,
        ).limit(limit + 1)
        movements = list((await session.scalars(statement)).all())
        has_more = len(movements) > limit
        visible_movements = movements[:limit]
        next_cursor = None
        if has_more:
            last_movement = visible_movements[-1]
            next_cursor = encode_created_at_cursor(
                created_at=last_movement.created_at,
                record_id=last_movement.id,
                sort=sort,
            )
        return CursorPage(items=visible_movements, next_cursor=next_cursor)
