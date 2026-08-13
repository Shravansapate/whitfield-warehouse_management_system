"""Dashboard and append-only audit query controller."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.commons.auth import CurrentUser, resolve_warehouse_id
from backend.core import logger
from backend.core.apis.schemas.common import (
    CreatedAtSort,
    CursorPage,
    decode_created_at_cursor,
    validate_created_at_filters,
)
from backend.core.cruds.reliability_crud import ReliabilityCRUD
from backend.core.models.access import Warehouse
from backend.core.models.enums import (
    AuditSource,
    DamagedReturnStatus,
    OrderStatus,
    ReceiptStatus,
    UserRole,
)
from backend.core.models.inventory import InventoryBalance
from backend.core.models.order import Order
from backend.core.models.receiving import DamagedReturn, InboundReceipt
from backend.core.models.reliability import AuditLog

logging = logger(__name__)


class ReadController:
    """Provide role-scoped operational summaries and audit history."""

    def __init__(self) -> None:
        """Initialize append-only audit persistence collaborator."""
        self.reliability = ReliabilityCRUD()

    async def dashboard_summary(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID | None,
        user: CurrentUser,
    ) -> dict:
        """Compute current operational metrics for one warehouse or all owner scopes.

        Args:
            session: Request-scoped database session.
            warehouse_id: Requested warehouse, optional only for owner combined view.
            user: Authenticated actor.

        Returns:
            Warehouse identity and current metric counts.
        """
        logging.info("Executing ReadController.dashboard_summary")
        if user.role == UserRole.OWNER and warehouse_id is None:
            resolved = None
            warehouse_payload: dict[str, uuid.UUID | str | None] = {
                "id": None,
                "code": "ALL",
                "name": "All warehouses",
            }
        else:
            resolved = resolve_warehouse_id(user, warehouse_id)
            warehouse = await session.get(Warehouse, resolved)
            if warehouse is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "detail": "Warehouse not found",
                        "code": "WAREHOUSE_NOT_FOUND",
                    },
                )
            warehouse_payload = {
                "id": warehouse.id,
                "code": warehouse.code,
                "name": warehouse.name,
            }

        balance_statement = select(
            func.coalesce(
                func.sum(InventoryBalance.on_hand - InventoryBalance.reserved), 0
            ),
            func.coalesce(func.sum(InventoryBalance.reserved), 0),
        )
        receiving_statement = select(func.count(InboundReceipt.id)).where(
            InboundReceipt.status.in_([ReceiptStatus.OPEN, ReceiptStatus.RECEIVING])
        )
        fulfillment_statement = select(func.count(Order.id)).where(
            Order.status.in_(
                [
                    OrderStatus.ALLOCATED,
                    OrderStatus.PICKING,
                    OrderStatus.PACKED,
                    OrderStatus.LABEL_CREATED,
                ]
            )
        )
        damaged_statement = select(func.count(DamagedReturn.id)).where(
            DamagedReturn.status == DamagedReturnStatus.PENDING_RETURN
        )
        audit_statement = select(func.count(AuditLog.id))
        if resolved is not None:
            balance_statement = balance_statement.where(
                InventoryBalance.warehouse_id == resolved
            )
            receiving_statement = receiving_statement.where(
                InboundReceipt.warehouse_id == resolved
            )
            fulfillment_statement = fulfillment_statement.where(
                Order.warehouse_id == resolved
            )
            damaged_statement = damaged_statement.where(
                DamagedReturn.warehouse_id == resolved
            )
            audit_statement = audit_statement.where(AuditLog.warehouse_id == resolved)
        available_units, reserved_units = (
            await session.execute(balance_statement)
        ).one()
        return {
            "warehouse": warehouse_payload,
            "metrics": {
                "available_units": int(available_units),
                "reserved_units": int(reserved_units),
                "receiving_backlog": int(
                    (await session.execute(receiving_statement)).scalar_one()
                ),
                "orders_to_ship": int(
                    (await session.execute(fulfillment_statement)).scalar_one()
                ),
                "damaged_returns": int(
                    (await session.execute(damaged_statement)).scalar_one()
                ),
                "audit_events": int(
                    (await session.execute(audit_statement)).scalar_one()
                ),
            },
        }

    async def audit_logs(
        self,
        session: AsyncSession,
        *,
        user: CurrentUser,
        warehouse_id: uuid.UUID | None,
        table_name: str | None,
        record_id: uuid.UUID | None,
        action: str | None,
        source: AuditSource | None,
        created_from: datetime | None,
        created_to: datetime | None,
        cursor: str | None,
        sort: CreatedAtSort,
        limit: int,
    ) -> CursorPage[dict]:
        """List a filtered audit cursor page within the actor's scope.

        Args:
            session: Request-scoped database session.
            user: Manager or owner actor.
            warehouse_id: Optional owner filter or manager scope.
            table_name: Optional changed-table filter.
            record_id: Optional record identifier filter.
            action: Optional exact audit action filter.
            source: Optional exact audit source filter.
            created_from: Inclusive creation-time lower bound.
            created_to: Inclusive creation-time upper bound.
            cursor: Opaque prior-page position.
            sort: Deterministic creation-time order.
            limit: Maximum event count.

        Returns:
            Human-readable events and an opaque next cursor.

        Raises:
            HTTPException 422: If date filters or the cursor are invalid.
        """
        logging.info("Executing ReadController.audit_logs")
        resolved_warehouse_id: uuid.UUID | None
        if user.role != UserRole.OWNER:
            resolved_warehouse_id = resolve_warehouse_id(user, warehouse_id)
        else:
            resolved_warehouse_id = warehouse_id
        try:
            validate_created_at_filters(
                created_from=created_from, created_to=created_to
            )
            decoded_cursor = decode_created_at_cursor(cursor, sort=sort)
        except ValueError as error:
            logging.warning("Rejected invalid audit-list pagination filters")
            raise HTTPException(
                status_code=422,
                detail={"detail": str(error), "code": "INVALID_PAGINATION"},
            ) from error
        page = await self.reliability.list_audit_logs(
            session,
            warehouse_id=resolved_warehouse_id,
            table_name=table_name,
            record_id=record_id,
            action=action,
            source=source,
            created_from=created_from,
            created_to=created_to,
            cursor=decoded_cursor,
            sort=sort,
            limit=limit,
        )
        return CursorPage(
            items=[
                {
                    "id": audit.id,
                    "actor_user_id": audit.actor_user_id,
                    "actor_name": actor_name,
                    "warehouse_id": audit.warehouse_id,
                    "table_name": audit.table_name,
                    "record_id": audit.record_id,
                    "action": audit.action,
                    "source": audit.source.value,
                    "reason": audit.reason,
                    "before_value": audit.before_value,
                    "after_value": audit.after_value,
                    "created_at": audit.created_at,
                }
                for audit, actor_name in page.items
            ],
            next_cursor=page.next_cursor,
        )
