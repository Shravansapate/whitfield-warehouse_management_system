"""Transactional inbound receiving workflows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.commons.auth import CurrentUser, resolve_warehouse_id
from backend.core import logger
from backend.core.apis.schemas.receiving import (
    ReceiptCreateRequest,
    ReceiptItemRequest,
    ReceiptItemUpdateRequest,
)
from backend.core.cruds.inventory_crud import InventoryCRUD
from backend.core.cruds.receiving_crud import ReceivingCRUD
from backend.core.cruds.reliability_crud import ReliabilityCRUD
from backend.core.models.enums import (
    AuditSource,
    DamagedReturnStatus,
    MovementType,
    ReceiptStatus,
    UserRole,
)
from backend.core.models.product import Product
from backend.core.models.receiving import (
    DamagedReturn,
    InboundReceipt,
    InboundReceiptItem,
)
from backend.core.services.transaction import command_transaction

logging = logger(__name__)


class ReceivingService:
    """Coordinate draft scanning, final receipt posting, and damaged returns."""

    def __init__(self) -> None:
        """Initialize receiving persistence collaborators."""
        self.receiving = ReceivingCRUD()
        self.inventory = InventoryCRUD()
        self.reliability = ReliabilityCRUD()

    @staticmethod
    def _assert_scope(user: CurrentUser, warehouse_id: uuid.UUID) -> None:
        """Enforce the actor's warehouse boundary for an existing resource.

        Args:
            user: Authenticated actor.
            warehouse_id: Resource warehouse identifier.

        Raises:
            HTTPException 403: If a non-owner accesses another warehouse.
        """
        if user.role != UserRole.OWNER and user.warehouse_id != warehouse_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "detail": "Cross-warehouse access is not allowed",
                    "code": "WAREHOUSE_FORBIDDEN",
                },
            )

    async def create_receipt(
        self,
        session: AsyncSession,
        *,
        request: ReceiptCreateRequest,
        user: CurrentUser,
        request_id: str,
        source: AuditSource,
    ) -> dict:
        """Create an open inbound receipt without changing inventory.

        Args:
            session: Request-scoped database session.
            request: Validated receipt data.
            user: Authenticated actor.
            request_id: Correlation identifier.
            source: Originating client channel.

        Returns:
            Created receipt response.
        """
        logging.info("Executing ReceivingService.create_receipt")
        warehouse_id = resolve_warehouse_id(user, request.warehouse_id)
        async with command_transaction(session):
            reference_locks: list[str] = []
            if request.tracking_number:
                reference_locks.append(
                    f"receipt-tracking:{request.tracking_number.casefold()}"
                )
            if request.ticket_number:
                reference_locks.append(
                    f"receipt-ticket:{warehouse_id}:{request.ticket_number.casefold()}"
                )
            for reference_key in sorted(reference_locks):
                await session.execute(
                    text(
                        "SELECT pg_advisory_xact_lock(hashtextextended(:reference_key, 0))"
                    ),
                    {"reference_key": reference_key},
                )
            duplicate_filters = []
            if request.tracking_number:
                duplicate_filters.append(
                    func.lower(InboundReceipt.tracking_number)
                    == request.tracking_number.lower()
                )
            if request.ticket_number:
                duplicate_filters.append(
                    (InboundReceipt.warehouse_id == warehouse_id)
                    & (
                        func.lower(InboundReceipt.ticket_number)
                        == request.ticket_number.lower()
                    )
                )
            if duplicate_filters:
                duplicate = (
                    await session.execute(
                        select(InboundReceipt.id).where(or_(*duplicate_filters))
                    )
                ).scalar_one_or_none()
                if duplicate is not None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "detail": "A receipt already exists for this tracking or ticket reference",
                            "code": "RECEIPT_REFERENCE_EXISTS",
                        },
                    )
            receipt = InboundReceipt(
                warehouse_id=warehouse_id,
                tracking_number=request.tracking_number,
                ticket_number=request.ticket_number,
                sender_name=request.sender_name.strip(),
                sender_contact=request.sender_contact,
                sender_return_address=request.sender_return_address.strip(),
                created_by=user.id,
                status=ReceiptStatus.OPEN,
            )
            session.add(receipt)
            await session.flush()
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=warehouse_id,
                table_name="inbound_receipts",
                record_id=receipt.id,
                action="RECEIPT_CREATED",
                request_id=request_id,
                source=source,
                after_value={"status": receipt.status.value},
            )
            payload = await self.receiving.serialize_receipt(session, receipt)
        return jsonable_encoder(payload)

    async def add_item(
        self,
        session: AsyncSession,
        *,
        receipt_id: uuid.UUID,
        request: ReceiptItemRequest,
        idempotency_key: str,
        user: CurrentUser,
        request_id: str,
        source: AuditSource,
    ) -> dict:
        """Add or consolidate a retry-safe scan on a draft receipt.

        Draft scans never change sellable inventory.

        Args:
            session: Request-scoped database session.
            receipt_id: Draft receipt identifier.
            request: Validated scan quantities.
            idempotency_key: Required retry identity.
            user: Authenticated actor.
            request_id: Correlation identifier.
            source: Originating client channel.

        Returns:
            Updated receipt response or stored replay.
        """
        logging.info("Executing ReceivingService.add_item")
        response: dict
        async with command_transaction(session):
            receipt = await self.receiving.get_receipt(
                session, receipt_id, for_update=True
            )
            if receipt is None:
                raise HTTPException(
                    status_code=404,
                    detail={"detail": "Receipt not found", "code": "RECEIPT_NOT_FOUND"},
                )
            self._assert_scope(user, receipt.warehouse_id)
            record, is_new = await self.reliability.acquire_idempotency(
                session,
                user_id=user.id,
                operation=f"receipt:{receipt_id}:add_item",
                key=idempotency_key,
                payload=request.model_dump(mode="json"),
            )
            if not is_new:
                return dict(record.response_body or {})
            if receipt.status not in {ReceiptStatus.OPEN, ReceiptStatus.RECEIVING}:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Only a draft receipt can be edited",
                        "code": "RECEIPT_NOT_EDITABLE",
                    },
                )
            product = await session.get(Product, request.product_id)
            if product is None or not product.is_active:
                raise HTTPException(
                    status_code=404,
                    detail={"detail": "Product not found", "code": "PRODUCT_NOT_FOUND"},
                )
            item_id = await self.receiving.add_item(
                session,
                receipt_id=receipt.id,
                **request.model_dump(),
            )
            receipt.status = ReceiptStatus.RECEIVING
            await session.flush()
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=receipt.warehouse_id,
                table_name="inbound_receipt_items",
                record_id=item_id,
                action="RECEIPT_ITEM_SCANNED",
                request_id=request_id,
                source=source,
                after_value=request.model_dump(mode="json"),
            )
            response = jsonable_encoder(
                await self.receiving.serialize_receipt(session, receipt)
            )
            await self.reliability.complete_idempotency(
                session,
                record=record,
                response_status=200,
                response_body=response,
                resource_type="inbound_receipts",
                resource_id=receipt.id,
            )
        return response

    async def update_item(
        self,
        session: AsyncSession,
        *,
        receipt_id: uuid.UUID,
        item_id: uuid.UUID,
        request: ReceiptItemUpdateRequest,
        idempotency_key: str,
        user: CurrentUser,
        request_id: str,
        source: AuditSource,
    ) -> dict:
        """Replace one receipt line while inventory remains unchanged.

        Args:
            session: Request-scoped database session.
            receipt_id: Parent receipt identifier.
            item_id: Draft item identifier.
            request: Replacement quantities.
            idempotency_key: Required retry identity.
            user: Authenticated actor.
            request_id: Correlation identifier.
            source: Originating client channel.

        Returns:
            Updated receipt response or stored replay.
        """
        logging.info("Executing ReceivingService.update_item")
        async with command_transaction(session):
            receipt = await self.receiving.get_receipt(
                session, receipt_id, for_update=True
            )
            if receipt is None:
                raise HTTPException(
                    status_code=404,
                    detail={"detail": "Receipt not found", "code": "RECEIPT_NOT_FOUND"},
                )
            self._assert_scope(user, receipt.warehouse_id)
            record, is_new = await self.reliability.acquire_idempotency(
                session,
                user_id=user.id,
                operation=f"receipt:{receipt_id}:item:{item_id}:update",
                key=idempotency_key,
                payload=request.model_dump(mode="json"),
            )
            if not is_new:
                return dict(record.response_body or {})
            if receipt.status not in {ReceiptStatus.OPEN, ReceiptStatus.RECEIVING}:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Only a draft receipt can be edited",
                        "code": "RECEIPT_NOT_EDITABLE",
                    },
                )
            item = await session.get(InboundReceiptItem, item_id)
            if item is None or item.receipt_id != receipt.id:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "detail": "Receipt item not found",
                        "code": "RECEIPT_ITEM_NOT_FOUND",
                    },
                )
            before = {
                "quantity_received": item.quantity_received,
                "quantity_accepted": item.quantity_accepted,
                "quantity_damaged": item.quantity_damaged,
            }
            for field, value in request.model_dump().items():
                setattr(item, field, value)
            await session.flush()
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=receipt.warehouse_id,
                table_name="inbound_receipt_items",
                record_id=item.id,
                action="RECEIPT_ITEM_UPDATED",
                request_id=request_id,
                source=source,
                before_value=before,
                after_value=request.model_dump(mode="json"),
            )
            response = jsonable_encoder(
                await self.receiving.serialize_receipt(session, receipt)
            )
            await self.reliability.complete_idempotency(
                session,
                record=record,
                response_status=200,
                response_body=response,
                resource_type="inbound_receipts",
                resource_id=receipt.id,
            )
        return response

    async def delete_item(
        self,
        session: AsyncSession,
        *,
        receipt_id: uuid.UUID,
        item_id: uuid.UUID,
        idempotency_key: str,
        user: CurrentUser,
        request_id: str,
        source: AuditSource,
    ) -> dict:
        """Delete one draft receipt line idempotently.

        Args:
            session: Request-scoped database session.
            receipt_id: Parent receipt identifier.
            item_id: Draft item identifier.
            idempotency_key: Required retry identity.
            user: Authenticated actor.
            request_id: Correlation identifier.
            source: Originating client channel.

        Returns:
            Updated receipt response or stored replay.
        """
        logging.info("Executing ReceivingService.delete_item")
        async with command_transaction(session):
            receipt = await self.receiving.get_receipt(
                session, receipt_id, for_update=True
            )
            if receipt is None:
                raise HTTPException(
                    status_code=404,
                    detail={"detail": "Receipt not found", "code": "RECEIPT_NOT_FOUND"},
                )
            self._assert_scope(user, receipt.warehouse_id)
            record, is_new = await self.reliability.acquire_idempotency(
                session,
                user_id=user.id,
                operation=f"receipt:{receipt_id}:item:{item_id}:delete",
                key=idempotency_key,
                payload={"item_id": str(item_id)},
            )
            if not is_new:
                return dict(record.response_body or {})
            if receipt.status not in {ReceiptStatus.OPEN, ReceiptStatus.RECEIVING}:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Only a draft receipt can be edited",
                        "code": "RECEIPT_NOT_EDITABLE",
                    },
                )
            deleted = await self.receiving.delete_item(
                session, receipt_id=receipt.id, item_id=item_id
            )
            if not deleted:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "detail": "Receipt item not found",
                        "code": "RECEIPT_ITEM_NOT_FOUND",
                    },
                )
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=receipt.warehouse_id,
                table_name="inbound_receipt_items",
                record_id=item_id,
                action="RECEIPT_ITEM_DELETED",
                request_id=request_id,
                source=source,
            )
            response = jsonable_encoder(
                await self.receiving.serialize_receipt(session, receipt)
            )
            await self.reliability.complete_idempotency(
                session,
                record=record,
                response_status=200,
                response_body=response,
                resource_type="inbound_receipts",
                resource_id=receipt.id,
            )
        return response

    async def finalize_receipt(
        self,
        session: AsyncSession,
        *,
        receipt_id: uuid.UUID,
        idempotency_key: str,
        user: CurrentUser,
        request_id: str,
        source: AuditSource,
    ) -> dict:
        """Atomically post accepted stock and damaged return records once.

        Args:
            session: Request-scoped database session.
            receipt_id: Receipt to complete.
            idempotency_key: Required retry identity.
            user: Authenticated actor.
            request_id: Correlation identifier.
            source: Originating client channel.

        Returns:
            Completed receipt response or stored replay.
        """
        logging.info("Executing ReceivingService.finalize_receipt")
        async with command_transaction(session):
            receipt = await self.receiving.get_receipt(
                session, receipt_id, for_update=True
            )
            if receipt is None:
                raise HTTPException(
                    status_code=404,
                    detail={"detail": "Receipt not found", "code": "RECEIPT_NOT_FOUND"},
                )
            self._assert_scope(user, receipt.warehouse_id)
            record, is_new = await self.reliability.acquire_idempotency(
                session,
                user_id=user.id,
                operation=f"receipt:{receipt_id}:finalize",
                key=idempotency_key,
                payload={"receipt_id": str(receipt_id)},
            )
            if not is_new:
                return dict(record.response_body or {})
            if receipt.status not in {ReceiptStatus.OPEN, ReceiptStatus.RECEIVING}:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Receipt has already reached a terminal state",
                        "code": "RECEIPT_STATE_CONFLICT",
                    },
                )
            items = await self.receiving.list_items(session, receipt.id)
            if not items:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Receipt must contain at least one item",
                        "code": "EMPTY_RECEIPT",
                    },
                )
            for item in items:
                if item.quantity_accepted > 0:
                    on_hand, reserved = await self.inventory.add_on_hand(
                        session,
                        warehouse_id=receipt.warehouse_id,
                        product_id=item.product_id,
                        quantity=item.quantity_accepted,
                    )
                    await self.inventory.add_movement(
                        session,
                        warehouse_id=receipt.warehouse_id,
                        product_id=item.product_id,
                        movement_type=MovementType.RECEIPT,
                        on_hand_delta=item.quantity_accepted,
                        reserved_delta=0,
                        reference_type="inbound_receipts",
                        reference_id=receipt.id,
                        actor_user_id=user.id,
                        source=source,
                        on_hand_after=on_hand,
                        reserved_after=reserved,
                    )
                if item.quantity_damaged > 0:
                    session.add(
                        DamagedReturn(
                            receipt_id=receipt.id,
                            receipt_item_id=item.id,
                            warehouse_id=receipt.warehouse_id,
                            product_id=item.product_id,
                            quantity=item.quantity_damaged,
                            status=DamagedReturnStatus.PENDING_RETURN,
                        )
                    )
            receipt.status = ReceiptStatus.RECEIVED
            receipt.received_by = user.id
            receipt.received_at = datetime.now(UTC)
            await session.flush()
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=receipt.warehouse_id,
                table_name="inbound_receipts",
                record_id=receipt.id,
                action="RECEIPT_FINALIZED",
                request_id=request_id,
                source=source,
                before_value={"status": ReceiptStatus.RECEIVING.value},
                after_value={
                    "status": ReceiptStatus.RECEIVED.value,
                    "accepted": sum(item.quantity_accepted for item in items),
                    "damaged": sum(item.quantity_damaged for item in items),
                },
            )
            response = jsonable_encoder(
                await self.receiving.serialize_receipt(session, receipt)
            )
            await self.reliability.complete_idempotency(
                session,
                record=record,
                response_status=200,
                response_body=response,
                resource_type="inbound_receipts",
                resource_id=receipt.id,
            )
        return response

    async def cancel_receipt(
        self,
        session: AsyncSession,
        *,
        receipt_id: uuid.UUID,
        reason: str,
        idempotency_key: str,
        user: CurrentUser,
        request_id: str,
        source: AuditSource,
    ) -> dict:
        """Cancel a draft receipt without ever posting its stock.

        Args:
            session: Request-scoped database session.
            receipt_id: Draft receipt identifier.
            reason: Mandatory cancellation explanation.
            idempotency_key: Required retry identity.
            user: Authenticated actor.
            request_id: Correlation identifier.
            source: Originating client channel.

        Returns:
            Cancelled receipt response or stored replay.
        """
        logging.info("Executing ReceivingService.cancel_receipt")
        async with command_transaction(session):
            receipt = await self.receiving.get_receipt(
                session, receipt_id, for_update=True
            )
            if receipt is None:
                raise HTTPException(
                    status_code=404,
                    detail={"detail": "Receipt not found", "code": "RECEIPT_NOT_FOUND"},
                )
            self._assert_scope(user, receipt.warehouse_id)
            record, is_new = await self.reliability.acquire_idempotency(
                session,
                user_id=user.id,
                operation=f"receipt:{receipt_id}:cancel",
                key=idempotency_key,
                payload={"receipt_id": str(receipt_id), "reason": reason},
            )
            if not is_new:
                return dict(record.response_body or {})
            if receipt.status not in {ReceiptStatus.OPEN, ReceiptStatus.RECEIVING}:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Receipt cannot be cancelled from its current state",
                        "code": "RECEIPT_STATE_CONFLICT",
                    },
                )
            before = receipt.status.value
            receipt.status = ReceiptStatus.CANCELLED
            await session.flush()
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=receipt.warehouse_id,
                table_name="inbound_receipts",
                record_id=receipt.id,
                action="RECEIPT_CANCELLED",
                request_id=request_id,
                source=source,
                before_value={"status": before},
                after_value={"status": receipt.status.value},
                reason=reason,
            )
            response = jsonable_encoder(
                await self.receiving.serialize_receipt(session, receipt)
            )
            await self.reliability.complete_idempotency(
                session,
                record=record,
                response_status=200,
                response_body=response,
                resource_type="inbound_receipts",
                resource_id=receipt.id,
            )
        return response

    async def complete_damaged_return(
        self,
        session: AsyncSession,
        *,
        damaged_return_id: uuid.UUID,
        tracking_number: str,
        notes: str | None,
        idempotency_key: str,
        user: CurrentUser,
        request_id: str,
        source: AuditSource,
    ) -> dict:
        """Complete return-to-sender tracking without changing sellable stock.

        Args:
            session: Request-scoped database session.
            damaged_return_id: Damaged return identifier.
            tracking_number: Carrier return reference.
            notes: Optional handling notes.
            idempotency_key: Required retry identity.
            user: Authorized trusted, manager, or owner actor.
            request_id: Correlation identifier.
            source: Originating client channel.

        Returns:
            Completed damaged-return payload or stored replay.
        """
        logging.info("Executing ReceivingService.complete_damaged_return")
        async with command_transaction(session):
            damaged = (
                await session.execute(
                    select(DamagedReturn)
                    .where(DamagedReturn.id == damaged_return_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if damaged is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "detail": "Damaged return not found",
                        "code": "DAMAGED_RETURN_NOT_FOUND",
                    },
                )
            self._assert_scope(user, damaged.warehouse_id)
            record, is_new = await self.reliability.acquire_idempotency(
                session,
                user_id=user.id,
                operation=f"damaged_return:{damaged_return_id}:complete",
                key=idempotency_key,
                payload={"tracking_number": tracking_number, "notes": notes},
            )
            if not is_new:
                return dict(record.response_body or {})
            if damaged.status != DamagedReturnStatus.PENDING_RETURN:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Damaged return is already complete",
                        "code": "DAMAGED_RETURN_STATE_CONFLICT",
                    },
                )
            damaged.status = DamagedReturnStatus.RETURNED_TO_SENDER
            damaged.return_tracking_number = tracking_number.strip()
            damaged.returned_at = datetime.now(UTC)
            damaged.handled_by = user.id
            damaged.notes = notes
            await session.flush()
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=damaged.warehouse_id,
                table_name="damaged_returns",
                record_id=damaged.id,
                action="DAMAGED_RETURN_COMPLETED",
                request_id=request_id,
                source=source,
                after_value={
                    "status": damaged.status.value,
                    "tracking_number": damaged.return_tracking_number,
                },
            )
            response = jsonable_encoder(
                {
                    "id": damaged.id,
                    "receipt_id": damaged.receipt_id,
                    "warehouse_id": damaged.warehouse_id,
                    "product_id": damaged.product_id,
                    "quantity": damaged.quantity,
                    "status": damaged.status,
                    "return_tracking_number": damaged.return_tracking_number,
                    "returned_at": damaged.returned_at,
                }
            )
            await self.reliability.complete_idempotency(
                session,
                record=record,
                response_status=200,
                response_body=response,
                resource_type="damaged_returns",
                resource_id=damaged.id,
            )
        return response
