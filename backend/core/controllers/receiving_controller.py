"""Inbound receiving HTTP orchestration controller."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.commons.auth import CurrentUser, resolve_warehouse_id
from backend.core import logger
from backend.core.apis.schemas.common import (
    CreatedAtCursor,
    CreatedAtSort,
    CursorPage,
    decode_created_at_cursor,
    validate_created_at_filters,
)
from backend.core.cruds.receiving_crud import ReceivingCRUD
from backend.core.models.enums import DamagedReturnStatus, ReceiptStatus, UserRole
from backend.core.services.receiving_service import ReceivingService

logging = logger(__name__)


def _validated_pagination_cursor(
    *,
    cursor: str | None,
    sort: CreatedAtSort,
    created_from: datetime | None,
    created_to: datetime | None,
    resource_name: str,
) -> CreatedAtCursor | None:
    """Validate a receiving-list date window and opaque cursor.

    Args:
        cursor: Opaque prior-page position.
        sort: Creation-time order required by the query.
        created_from: Inclusive creation-time lower bound.
        created_to: Inclusive creation-time upper bound.
        resource_name: Stable resource name used for warning logs.

    Returns:
        Decoded cursor or None for a first page.

    Raises:
        HTTPException 422: If date filters or the cursor are invalid.
    """
    try:
        validate_created_at_filters(created_from=created_from, created_to=created_to)
        return decode_created_at_cursor(cursor, sort=sort)
    except ValueError as error:
        logging.warning("Rejected invalid %s-list pagination filters", resource_name)
        raise HTTPException(
            status_code=422,
            detail={"detail": str(error), "code": "INVALID_PAGINATION"},
        ) from error


class ReceivingController:
    """Authorize and orchestrate inbound receiving operations."""

    def __init__(self) -> None:
        """Initialize receiving workflow and query collaborators."""
        self.service = ReceivingService()
        self.crud = ReceivingCRUD()

    async def list_receipts(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID | None,
        limit: int,
        status: ReceiptStatus | None,
        created_from: datetime | None,
        created_to: datetime | None,
        cursor: str | None,
        sort: CreatedAtSort,
        user: CurrentUser,
    ) -> CursorPage[dict]:
        """List a filtered receipt page in an authorized warehouse.

        Args:
            session: Request-scoped database session.
            warehouse_id: Requested warehouse identifier.
            limit: Maximum record count.
            status: Optional exact lifecycle status.
            created_from: Inclusive creation-time lower bound.
            created_to: Inclusive creation-time upper bound.
            cursor: Opaque prior-page position.
            sort: Deterministic creation-time order.
            user: Authenticated actor.

        Returns:
            Receipt summaries and an opaque next cursor.

        Raises:
            HTTPException 422: If date filters or the cursor are invalid.
        """
        logging.info("Executing ReceivingController.list_receipts")
        resolved = resolve_warehouse_id(user, warehouse_id)
        decoded_cursor = _validated_pagination_cursor(
            cursor=cursor,
            sort=sort,
            created_from=created_from,
            created_to=created_to,
            resource_name="receipt",
        )
        return await self.crud.list_receipts(
            session,
            warehouse_id=resolved,
            limit=limit,
            status=status,
            created_from=created_from,
            created_to=created_to,
            cursor=decoded_cursor,
            sort=sort,
        )

    async def get_receipt(
        self, session: AsyncSession, *, receipt_id: uuid.UUID, user: CurrentUser
    ) -> dict:
        """Read one receipt after enforcing warehouse scope.

        Args:
            session: Request-scoped database session.
            receipt_id: Receipt identifier.
            user: Authenticated actor.

        Returns:
            Expanded receipt response.
        """
        logging.info("Executing ReceivingController.get_receipt")
        receipt = await self.crud.get_receipt(session, receipt_id)
        if receipt is None:
            raise HTTPException(
                status_code=404,
                detail={"detail": "Receipt not found", "code": "RECEIPT_NOT_FOUND"},
            )
        if user.role != UserRole.OWNER and user.warehouse_id != receipt.warehouse_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "detail": "Cross-warehouse access is not allowed",
                    "code": "WAREHOUSE_FORBIDDEN",
                },
            )
        return await self.crud.serialize_receipt(session, receipt)

    async def list_damaged_returns(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID | None,
        limit: int,
        status: DamagedReturnStatus | None,
        created_from: datetime | None,
        created_to: datetime | None,
        cursor: str | None,
        sort: CreatedAtSort,
        user: CurrentUser,
    ) -> CursorPage[dict]:
        """List a damaged-return page within an authorized warehouse.

        Args:
            session: Request-scoped database session.
            warehouse_id: Requested warehouse identifier.
            limit: Maximum record count.
            status: Optional exact return lifecycle status.
            created_from: Inclusive creation-time lower bound.
            created_to: Inclusive creation-time upper bound.
            cursor: Opaque prior-page position.
            sort: Deterministic creation-time order.
            user: Authenticated actor.

        Returns:
            Damaged returns and an opaque next cursor.

        Raises:
            HTTPException 422: If date filters or the cursor are invalid.
        """
        logging.info("Executing ReceivingController.list_damaged_returns")
        resolved = resolve_warehouse_id(user, warehouse_id)
        decoded_cursor = _validated_pagination_cursor(
            cursor=cursor,
            sort=sort,
            created_from=created_from,
            created_to=created_to,
            resource_name="damaged-return",
        )
        return await self.crud.list_damaged_returns(
            session,
            warehouse_id=resolved,
            limit=limit,
            status=status,
            created_from=created_from,
            created_to=created_to,
            cursor=decoded_cursor,
            sort=sort,
        )
