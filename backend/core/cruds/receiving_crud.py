"""Inbound receipt persistence and response queries."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
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
from backend.core.models.enums import DamagedReturnStatus, ReceiptStatus
from backend.core.models.product import Product
from backend.core.models.receiving import (
    DamagedReturn,
    InboundReceipt,
    InboundReceiptItem,
)

logging = logger(__name__)


class ReceivingCRUD:
    """Persistence wrapper for draft receipts and damaged returns."""

    async def get_receipt(
        self,
        session: AsyncSession,
        receipt_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> InboundReceipt | None:
        """Read an inbound receipt with an optional row lock.

        Args:
            session: Request-scoped database session.
            receipt_id: Receipt identifier.
            for_update: Lock the state-machine row for a command.

        Returns:
            Receipt model or None.
        """
        logging.info("Executing ReceivingCRUD.get_receipt")
        statement = select(InboundReceipt).where(InboundReceipt.id == receipt_id)
        if for_update:
            statement = statement.with_for_update()
        return (await session.execute(statement)).scalar_one_or_none()

    async def add_item(
        self,
        session: AsyncSession,
        *,
        receipt_id: uuid.UUID,
        product_id: uuid.UUID,
        quantity_received: int,
        quantity_accepted: int,
        quantity_damaged: int,
        damage_notes: str | None,
    ) -> uuid.UUID:
        """Atomically add or consolidate a product scan on a draft receipt.

        Args:
            session: Active receipt-item transaction.
            receipt_id: Parent draft receipt.
            product_id: Scanned product.
            quantity_received: Total units in this scan.
            quantity_accepted: Accepted units in this scan.
            quantity_damaged: Damaged units in this scan.
            damage_notes: Damage explanation when applicable.

        Returns:
            Consolidated receipt item identifier.
        """
        logging.info("Executing ReceivingCRUD.add_item")
        statement = (
            insert(InboundReceiptItem)
            .values(
                id=uuid.uuid4(),
                receipt_id=receipt_id,
                product_id=product_id,
                quantity_received=quantity_received,
                quantity_accepted=quantity_accepted,
                quantity_damaged=quantity_damaged,
                damage_notes=damage_notes,
            )
            .on_conflict_do_update(
                constraint="uq_receipt_product",
                set_={
                    "quantity_received": InboundReceiptItem.quantity_received
                    + quantity_received,
                    "quantity_accepted": InboundReceiptItem.quantity_accepted
                    + quantity_accepted,
                    "quantity_damaged": InboundReceiptItem.quantity_damaged
                    + quantity_damaged,
                    "damage_notes": damage_notes,
                    "updated_at": func.now(),
                },
            )
            .returning(InboundReceiptItem.id)
        )
        return (await session.execute(statement)).scalar_one()

    async def delete_item(
        self, session: AsyncSession, *, receipt_id: uuid.UUID, item_id: uuid.UUID
    ) -> bool:
        """Delete one line while its receipt remains a draft.

        Args:
            session: Active receipt-item transaction.
            receipt_id: Parent receipt identifier.
            item_id: Draft item identifier.

        Returns:
            True when a row was deleted.
        """
        logging.info("Executing ReceivingCRUD.delete_item")
        result = await session.execute(
            delete(InboundReceiptItem).where(
                InboundReceiptItem.id == item_id,
                InboundReceiptItem.receipt_id == receipt_id,
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def list_items(
        self, session: AsyncSession, receipt_id: uuid.UUID
    ) -> list[InboundReceiptItem]:
        """List receipt lines in deterministic product order.

        Args:
            session: Request-scoped database session.
            receipt_id: Parent receipt identifier.

        Returns:
            Receipt item models.
        """
        logging.info("Executing ReceivingCRUD.list_items")
        return list(
            (
                await session.scalars(
                    select(InboundReceiptItem)
                    .where(InboundReceiptItem.receipt_id == receipt_id)
                    .order_by(InboundReceiptItem.product_id)
                )
            ).all()
        )

    async def serialize_receipt(
        self,
        session: AsyncSession,
        receipt: InboundReceipt,
        *,
        include_items: bool = True,
    ) -> dict[str, Any]:
        """Build a receipt response with aggregate and optional line details.

        Args:
            session: Request-scoped database session.
            receipt: Receipt model to serialize.
            include_items: Include expanded product lines.

        Returns:
            Receipt response dictionary.
        """
        logging.info("Executing ReceivingCRUD.serialize_receipt")
        rows = (
            await session.execute(
                select(InboundReceiptItem, Product)
                .join(Product, Product.id == InboundReceiptItem.product_id)
                .where(InboundReceiptItem.receipt_id == receipt.id)
                .order_by(Product.name)
            )
        ).all()
        items = [
            {
                "id": item.id,
                "product_id": item.product_id,
                "sku": product.sku,
                "upc": product.upc,
                "product_name": product.name,
                "quantity_received": item.quantity_received,
                "quantity_accepted": item.quantity_accepted,
                "quantity_damaged": item.quantity_damaged,
                "damage_notes": item.damage_notes,
            }
            for item, product in rows
        ]
        return {
            "id": receipt.id,
            "warehouse_id": receipt.warehouse_id,
            "tracking_number": receipt.tracking_number,
            "ticket_number": receipt.ticket_number,
            "sender_name": receipt.sender_name,
            "sender_contact": receipt.sender_contact,
            "sender_return_address": receipt.sender_return_address,
            "status": receipt.status,
            "accepted": sum(item[0].quantity_accepted for item in rows),
            "damaged": sum(item[0].quantity_damaged for item in rows),
            "lines": len(rows),
            "items": items if include_items else [],
            "created_at": receipt.created_at,
            "received_at": receipt.received_at,
        }

    async def list_receipts(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID,
        limit: int,
        status: ReceiptStatus | None,
        created_from: datetime | None,
        created_to: datetime | None,
        cursor: CreatedAtCursor | None,
        sort: CreatedAtSort,
    ) -> CursorPage[dict[str, Any]]:
        """List a filtered keyset page within one warehouse scope.

        Args:
            session: Request-scoped database session.
            warehouse_id: Authorized warehouse identifier.
            limit: Maximum records returned.
            status: Optional exact lifecycle status.
            created_from: Inclusive creation-time lower bound.
            created_to: Inclusive creation-time upper bound.
            cursor: Exclusive prior-page position.
            sort: Deterministic creation-time order.

        Returns:
            Receipt summaries and an opaque next cursor.
        """
        logging.info("Executing ReceivingCRUD.list_receipts")
        statement = select(InboundReceipt).where(
            InboundReceipt.warehouse_id == warehouse_id
        )
        if status is not None:
            statement = statement.where(InboundReceipt.status == status)
        statement = apply_created_at_pagination(
            statement,
            created_at_column=InboundReceipt.created_at,
            id_column=InboundReceipt.id,
            created_from=created_from,
            created_to=created_to,
            cursor=cursor,
            sort=sort,
        ).limit(limit + 1)
        receipts = list((await session.scalars(statement)).all())
        has_more = len(receipts) > limit
        visible_receipts = receipts[:limit]
        next_cursor = None
        if has_more:
            last_receipt = visible_receipts[-1]
            next_cursor = encode_created_at_cursor(
                created_at=last_receipt.created_at,
                record_id=last_receipt.id,
                sort=sort,
            )
        return CursorPage(
            items=[
                await self.serialize_receipt(session, receipt, include_items=False)
                for receipt in visible_receipts
            ],
            next_cursor=next_cursor,
        )

    async def list_damaged_returns(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID,
        limit: int,
        status: DamagedReturnStatus | None,
        created_from: datetime | None,
        created_to: datetime | None,
        cursor: CreatedAtCursor | None,
        sort: CreatedAtSort,
    ) -> CursorPage[dict[str, Any]]:
        """List a filtered damaged-return page for one warehouse.

        Args:
            session: Request-scoped database session.
            warehouse_id: Authorized warehouse identifier.
            limit: Maximum records returned.
            status: Optional exact return lifecycle status.
            created_from: Inclusive creation-time lower bound.
            created_to: Inclusive creation-time upper bound.
            cursor: Exclusive prior-page position.
            sort: Deterministic creation-time order.

        Returns:
            Damaged returns and an opaque next cursor.
        """
        logging.info("Executing ReceivingCRUD.list_damaged_returns")
        statement = (
            select(DamagedReturn, Product)
            .join(Product, Product.id == DamagedReturn.product_id)
            .where(DamagedReturn.warehouse_id == warehouse_id)
        )
        if status is not None:
            statement = statement.where(DamagedReturn.status == status)
        statement = apply_created_at_pagination(
            statement,
            created_at_column=DamagedReturn.created_at,
            id_column=DamagedReturn.id,
            created_from=created_from,
            created_to=created_to,
            cursor=cursor,
            sort=sort,
        ).limit(limit + 1)
        rows = (await session.execute(statement)).all()
        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        next_cursor = None
        if has_more:
            last_record = visible_rows[-1][0]
            next_cursor = encode_created_at_cursor(
                created_at=last_record.created_at,
                record_id=last_record.id,
                sort=sort,
            )
        return CursorPage(
            items=[
                {
                    "id": record.id,
                    "receipt_id": record.receipt_id,
                    "warehouse_id": record.warehouse_id,
                    "product_id": record.product_id,
                    "sku": product.sku,
                    "product_name": product.name,
                    "quantity": record.quantity,
                    "status": record.status,
                    "return_tracking_number": record.return_tracking_number,
                    "returned_at": record.returned_at,
                }
                for record, product in visible_rows
            ],
            next_cursor=next_cursor,
        )
