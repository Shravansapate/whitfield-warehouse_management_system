"""Transactional manual inventory and opening-balance workflows."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from backend.commons.auth import CurrentUser, resolve_warehouse_id
from backend.core import logger
from backend.core.cruds.inventory_crud import InventoryCRUD
from backend.core.cruds.reliability_crud import ReliabilityCRUD
from backend.core.models.enums import AdjustmentStatus, AuditSource, MovementType
from backend.core.models.inventory import InventoryAdjustment
from backend.core.models.product import Product
from backend.core.services.transaction import command_transaction

logging = logger(__name__)


class InventoryService:
    """Coordinate controlled stock mutations and their immutable history."""

    def __init__(self) -> None:
        """Initialize inventory persistence collaborators."""
        self.inventory = InventoryCRUD()
        self.reliability = ReliabilityCRUD()

    async def adjust(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID | None,
        product_id: uuid.UUID,
        quantity_delta: int,
        reason: str,
        idempotency_key: str,
        user: CurrentUser,
        request_id: str,
        source: AuditSource,
    ) -> dict:
        """Apply one reasoned manual on-hand correction atomically.

        Args:
            session: Request-scoped database session.
            warehouse_id: Requested warehouse scope.
            product_id: Product being corrected.
            quantity_delta: Nonzero signed on-hand change.
            reason: Mandatory human explanation.
            idempotency_key: Required retry identity.
            user: Authorized trusted, manager, or owner actor.
            request_id: Correlation identifier.
            source: Originating client channel.

        Returns:
            Applied adjustment and resulting balance.

        Raises:
            HTTPException 409: If the delta would violate reserved stock.
        """
        logging.info("Executing InventoryService.adjust")
        if quantity_delta == 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "detail": "quantity_delta cannot be zero",
                    "code": "ZERO_ADJUSTMENT",
                },
            )
        resolved_warehouse = resolve_warehouse_id(user, warehouse_id)
        async with command_transaction(session):
            record, is_new = await self.reliability.acquire_idempotency(
                session,
                user_id=user.id,
                operation="inventory:adjust",
                key=idempotency_key,
                payload={
                    "warehouse_id": str(resolved_warehouse),
                    "product_id": str(product_id),
                    "quantity_delta": quantity_delta,
                    "reason": reason,
                },
            )
            if not is_new:
                return dict(record.response_body or {})
            product = await session.get(Product, product_id)
            if product is None or not product.is_active:
                raise HTTPException(
                    status_code=404,
                    detail={"detail": "Product not found", "code": "PRODUCT_NOT_FOUND"},
                )
            adjustment = InventoryAdjustment(
                warehouse_id=resolved_warehouse,
                product_id=product_id,
                quantity_delta=quantity_delta,
                reason=reason.strip(),
                status=AdjustmentStatus.APPLIED,
                created_by=user.id,
            )
            session.add(adjustment)
            await session.flush()
            result = await self.inventory.adjust_on_hand(
                session,
                warehouse_id=resolved_warehouse,
                product_id=product_id,
                quantity_delta=quantity_delta,
            )
            if result is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "detail": "Adjustment would reduce on-hand stock below reserved stock",
                        "code": "INSUFFICIENT_UNRESERVED_STOCK",
                    },
                )
            on_hand, reserved = result
            await self.inventory.add_movement(
                session,
                warehouse_id=resolved_warehouse,
                product_id=product_id,
                movement_type=MovementType.ADJUST,
                on_hand_delta=quantity_delta,
                reserved_delta=0,
                reference_type="inventory_adjustments",
                reference_id=adjustment.id,
                actor_user_id=user.id,
                source=source,
                on_hand_after=on_hand,
                reserved_after=reserved,
                reason=reason,
            )
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=resolved_warehouse,
                table_name="inventory_adjustments",
                record_id=adjustment.id,
                action="INVENTORY_ADJUSTED",
                request_id=request_id,
                source=source,
                after_value={
                    "product_id": str(product_id),
                    "quantity_delta": quantity_delta,
                    "on_hand": on_hand,
                    "reserved": reserved,
                },
                reason=reason,
            )
            response = jsonable_encoder(
                {
                    "id": adjustment.id,
                    "warehouse_id": resolved_warehouse,
                    "product_id": product_id,
                    "quantity_delta": quantity_delta,
                    "reason": reason,
                    "status": adjustment.status,
                    "on_hand": on_hand,
                    "reserved": reserved,
                    "available": on_hand - reserved,
                }
            )
            await self.reliability.complete_idempotency(
                session,
                record=record,
                response_status=201,
                response_body=response,
                resource_type="inventory_adjustments",
                resource_id=adjustment.id,
            )
        return response

    async def opening_balance(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID,
        product_id: uuid.UUID,
        quantity: int,
        reason: str,
        idempotency_key: str,
        user: CurrentUser,
        request_id: str,
        source: AuditSource,
    ) -> dict:
        """Post a one-time owner-controlled opening balance.

        Refuses a product warehouse that already has any balance row.

        Args:
            session: Request-scoped database session.
            warehouse_id: Stock-owning warehouse.
            product_id: Product receiving initial stock.
            quantity: Positive verified opening quantity.
            reason: Mandatory verification note.
            idempotency_key: Required retry identity.
            user: Owner actor.
            request_id: Correlation identifier.
            source: Originating client channel.

        Returns:
            Resulting opening balance or stored replay.
        """
        logging.info("Executing InventoryService.opening_balance")
        resolved_warehouse = resolve_warehouse_id(user, warehouse_id)
        async with command_transaction(session):
            record, is_new = await self.reliability.acquire_idempotency(
                session,
                user_id=user.id,
                operation="inventory:opening_balance",
                key=idempotency_key,
                payload={
                    "warehouse_id": str(resolved_warehouse),
                    "product_id": str(product_id),
                    "quantity": quantity,
                    "reason": reason,
                },
            )
            if not is_new:
                return dict(record.response_body or {})
            product = await session.get(Product, product_id)
            if product is None or not product.is_active:
                raise HTTPException(
                    status_code=404,
                    detail={"detail": "Product not found", "code": "PRODUCT_NOT_FOUND"},
                )
            balance = await self.inventory.create_opening_balance(
                session,
                warehouse_id=resolved_warehouse,
                product_id=product_id,
                quantity=quantity,
            )
            if balance is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "detail": "Opening balance already exists for this product",
                        "code": "OPENING_BALANCE_EXISTS",
                    },
                )
            on_hand, reserved = balance.on_hand, balance.reserved
            await self.inventory.add_movement(
                session,
                warehouse_id=resolved_warehouse,
                product_id=product_id,
                movement_type=MovementType.OPENING_BALANCE,
                on_hand_delta=quantity,
                reserved_delta=0,
                reference_type="opening_balances",
                reference_id=balance.id,
                actor_user_id=user.id,
                source=source,
                on_hand_after=on_hand,
                reserved_after=reserved,
                reason=reason,
            )
            await self.reliability.add_audit(
                session,
                actor_user_id=user.id,
                warehouse_id=resolved_warehouse,
                table_name="inventory_balances",
                record_id=balance.id,
                action="OPENING_BALANCE_POSTED",
                request_id=request_id,
                source=source,
                after_value={
                    "product_id": str(product_id),
                    "on_hand": on_hand,
                    "reserved": reserved,
                },
                reason=reason,
            )
            response = jsonable_encoder(
                {
                    "warehouse_id": resolved_warehouse,
                    "product_id": product_id,
                    "on_hand": on_hand,
                    "reserved": reserved,
                    "available": on_hand - reserved,
                }
            )
            await self.reliability.complete_idempotency(
                session,
                record=record,
                response_status=201,
                response_body=response,
                resource_type="inventory_balances",
                resource_id=balance.id,
            )
        return response
