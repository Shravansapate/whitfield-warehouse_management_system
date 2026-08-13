"""Scanner-first inbound receipt and damaged-return endpoints."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.commons.auth import CurrentUser, get_current_user, require_roles
from backend.core import logger
from backend.core.apis.routes.dependencies import (
    IdempotencyKey,
    SourceHeader,
    request_id,
)
from backend.core.apis.schemas.common import CreatedAtSort
from backend.core.apis.schemas.receiving import (
    DamagedReturnCompleteRequest,
    DamagedReturnResponse,
    ReceiptCancelRequest,
    ReceiptCreateRequest,
    ReceiptItemRequest,
    ReceiptItemUpdateRequest,
    ReceiptResponse,
)
from backend.core.controllers.receiving_controller import ReceivingController
from backend.core.database.session import get_session
from backend.core.models.enums import (
    AuditSource,
    DamagedReturnStatus,
    ReceiptStatus,
    UserRole,
)

router = APIRouter(tags=["receiving"])
logging = logger(__name__)
controller = ReceivingController()


@router.post("/inbound-receipts", response_model=ReceiptResponse, status_code=201)
async def create_receipt(
    request: ReceiptCreateRequest,
    http_request: Request,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: SourceHeader = AuditSource.WEB,
) -> dict:
    """Open an inbound receipt without posting stock.

    Args:
        request: Shipment reference and sender details.
        http_request: Current request context.
        user: Authenticated actor.
        session: Request-scoped database session.
        source: Originating client channel.

    Returns:
        Created open receipt.
    """
    try:
        logging.info("Calling POST /api/v1/inbound-receipts endpoint")
        return await controller.service.create_receipt(
            session,
            request=request,
            user=user,
            request_id=request_id(http_request),
            source=source,
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected receipt-create failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.get("/inbound-receipts", response_model=list[ReceiptResponse])
async def list_receipts(
    response: Response,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    warehouse_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    status_filter: Annotated[ReceiptStatus | None, Query(alias="status")] = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    sort: CreatedAtSort = CreatedAtSort.CREATED_AT_DESC,
) -> list[dict]:
    """List a filtered receipt cursor page in an authorized warehouse.

    The body remains a JSON list; a further page is advertised in
    the ``X-Next-Cursor`` response header.

    Args:
        response: Mutable HTTP response used for the next-cursor header.
        user: Authenticated actor.
        session: Request-scoped database session.
        warehouse_id: Optional owner warehouse selector.
        limit: Maximum receipt count.
        cursor: Opaque position returned by the prior page.
        status_filter: Optional exact lifecycle status.
        created_from: Inclusive creation-time lower bound.
        created_to: Inclusive creation-time upper bound.
        sort: Ascending or descending creation-time order.

    Returns:
        Receipt summaries.
    """
    try:
        logging.info("Calling GET /api/v1/inbound-receipts endpoint")
        page = await controller.list_receipts(
            session,
            warehouse_id=warehouse_id,
            limit=limit,
            status=status_filter,
            created_from=created_from,
            created_to=created_to,
            cursor=cursor,
            sort=sort,
            user=user,
        )
        if page.next_cursor is not None:
            response.headers["X-Next-Cursor"] = page.next_cursor
        return page.items
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected receipt-list failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.get("/inbound-receipts/{receipt_id}", response_model=ReceiptResponse)
async def get_receipt(
    receipt_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Read one authorized receipt and its lines.

    Args:
        receipt_id: Receipt identifier.
        user: Authenticated actor.
        session: Request-scoped database session.

    Returns:
        Expanded receipt.
    """
    try:
        logging.info(f"Calling GET /api/v1/inbound-receipts/{receipt_id} endpoint")
        return await controller.get_receipt(session, receipt_id=receipt_id, user=user)
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected receipt-read failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.post("/inbound-receipts/{receipt_id}/items", response_model=ReceiptResponse)
async def add_receipt_item(
    receipt_id: uuid.UUID,
    request: ReceiptItemRequest,
    http_request: Request,
    idempotency_key: IdempotencyKey,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: SourceHeader = AuditSource.SCANNER,
) -> dict:
    """Add a retry-safe product scan to a draft receipt.

    Args:
        receipt_id: Draft receipt identifier.
        request: Accepted and damaged scan quantities.
        http_request: Current request context.
        idempotency_key: Required retry identity.
        user: Authenticated actor.
        session: Request-scoped database session.
        source: Originating client channel.

    Returns:
        Updated draft receipt.
    """
    try:
        logging.info(
            f"Calling POST /api/v1/inbound-receipts/{receipt_id}/items endpoint"
        )
        return await controller.service.add_item(
            session,
            receipt_id=receipt_id,
            request=request,
            idempotency_key=idempotency_key,
            user=user,
            request_id=request_id(http_request),
            source=source,
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected receipt-item-create failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.patch(
    "/inbound-receipts/{receipt_id}/items/{item_id}", response_model=ReceiptResponse
)
async def update_receipt_item(
    receipt_id: uuid.UUID,
    item_id: uuid.UUID,
    request: ReceiptItemUpdateRequest,
    http_request: Request,
    idempotency_key: IdempotencyKey,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: SourceHeader = AuditSource.WEB,
) -> dict:
    """Replace a retry-safe draft receipt line.

    Args:
        receipt_id: Parent receipt identifier.
        item_id: Draft line identifier.
        request: Replacement quantities.
        http_request: Current request context.
        idempotency_key: Required retry identity.
        user: Authenticated actor.
        session: Request-scoped database session.
        source: Originating client channel.

    Returns:
        Updated draft receipt.
    """
    try:
        logging.info(f"Calling PATCH receipt item {item_id} endpoint")
        return await controller.service.update_item(
            session,
            receipt_id=receipt_id,
            item_id=item_id,
            request=request,
            idempotency_key=idempotency_key,
            user=user,
            request_id=request_id(http_request),
            source=source,
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected receipt-item-update failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.delete(
    "/inbound-receipts/{receipt_id}/items/{item_id}", response_model=ReceiptResponse
)
async def delete_receipt_item(
    receipt_id: uuid.UUID,
    item_id: uuid.UUID,
    http_request: Request,
    idempotency_key: IdempotencyKey,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: SourceHeader = AuditSource.WEB,
) -> dict:
    """Delete a retry-safe draft receipt line.

    Args:
        receipt_id: Parent receipt identifier.
        item_id: Draft line identifier.
        http_request: Current request context.
        idempotency_key: Required retry identity.
        user: Authenticated actor.
        session: Request-scoped database session.
        source: Originating client channel.

    Returns:
        Updated draft receipt.
    """
    try:
        logging.info(f"Calling DELETE receipt item {item_id} endpoint")
        return await controller.service.delete_item(
            session,
            receipt_id=receipt_id,
            item_id=item_id,
            idempotency_key=idempotency_key,
            user=user,
            request_id=request_id(http_request),
            source=source,
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected receipt-item-delete failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.post("/inbound-receipts/{receipt_id}/receive", response_model=ReceiptResponse)
async def finalize_receipt(
    receipt_id: uuid.UUID,
    http_request: Request,
    idempotency_key: IdempotencyKey,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: SourceHeader = AuditSource.WEB,
) -> dict:
    """Atomically finalize a receipt and post accepted stock exactly once.

    Args:
        receipt_id: Draft receipt identifier.
        http_request: Current request context.
        idempotency_key: Required retry identity.
        user: Authenticated actor.
        session: Request-scoped database session.
        source: Originating client channel.

    Returns:
        Completed receipt.
    """
    try:
        logging.info(
            f"Calling POST /api/v1/inbound-receipts/{receipt_id}/receive endpoint"
        )
        return await controller.service.finalize_receipt(
            session,
            receipt_id=receipt_id,
            idempotency_key=idempotency_key,
            user=user,
            request_id=request_id(http_request),
            source=source,
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected receipt-finalize failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.post("/inbound-receipts/{receipt_id}/cancel", response_model=ReceiptResponse)
async def cancel_receipt(
    receipt_id: uuid.UUID,
    request: ReceiptCancelRequest,
    http_request: Request,
    idempotency_key: IdempotencyKey,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: SourceHeader = AuditSource.WEB,
) -> dict:
    """Cancel a draft receipt without posting inventory.

    Args:
        receipt_id: Draft receipt identifier.
        request: Mandatory cancellation reason.
        http_request: Current request context.
        idempotency_key: Required retry identity.
        user: Authenticated actor.
        session: Request-scoped database session.
        source: Originating client channel.

    Returns:
        Cancelled receipt.
    """
    try:
        logging.info(
            f"Calling POST /api/v1/inbound-receipts/{receipt_id}/cancel endpoint"
        )
        return await controller.service.cancel_receipt(
            session,
            receipt_id=receipt_id,
            reason=request.reason,
            idempotency_key=idempotency_key,
            user=user,
            request_id=request_id(http_request),
            source=source,
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected receipt-cancel failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.get("/damaged-returns", response_model=list[DamagedReturnResponse])
async def list_damaged_returns(
    response: Response,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    warehouse_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    status_filter: Annotated[DamagedReturnStatus | None, Query(alias="status")] = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    sort: CreatedAtSort = CreatedAtSort.CREATED_AT_DESC,
) -> list[dict]:
    """List a filtered damaged-return cursor page.

    The body remains a JSON list; a further page is advertised in
    the ``X-Next-Cursor`` response header.

    Args:
        response: Mutable HTTP response used for the next-cursor header.
        user: Authenticated actor.
        session: Request-scoped database session.
        warehouse_id: Optional owner warehouse selector.
        limit: Maximum return count.
        cursor: Opaque position returned by the prior page.
        status_filter: Optional exact return lifecycle status.
        created_from: Inclusive creation-time lower bound.
        created_to: Inclusive creation-time upper bound.
        sort: Ascending or descending creation-time order.

    Returns:
        Damaged-return records.
    """
    try:
        logging.info("Calling GET /api/v1/damaged-returns endpoint")
        page = await controller.list_damaged_returns(
            session,
            warehouse_id=warehouse_id,
            limit=limit,
            status=status_filter,
            created_from=created_from,
            created_to=created_to,
            cursor=cursor,
            sort=sort,
            user=user,
        )
        if page.next_cursor is not None:
            response.headers["X-Next-Cursor"] = page.next_cursor
        return page.items
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected damaged-return-list failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.post("/damaged-returns/{damaged_return_id}/complete")
async def complete_damaged_return(
    damaged_return_id: uuid.UUID,
    request: DamagedReturnCompleteRequest,
    http_request: Request,
    idempotency_key: IdempotencyKey,
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.TRUSTED, UserRole.MANAGER, UserRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: SourceHeader = AuditSource.WEB,
) -> dict:
    """Complete return-to-sender evidence without changing sellable stock.

    Args:
        damaged_return_id: Damaged return identifier.
        request: Return tracking details.
        http_request: Current request context.
        idempotency_key: Required retry identity.
        user: Authorized trusted, manager, or owner actor.
        session: Request-scoped database session.
        source: Originating client channel.

    Returns:
        Completed damaged-return payload.
    """
    try:
        logging.info(
            f"Calling POST /api/v1/damaged-returns/{damaged_return_id}/complete endpoint"
        )
        return await controller.service.complete_damaged_return(
            session,
            damaged_return_id=damaged_return_id,
            tracking_number=request.return_tracking_number,
            notes=request.notes,
            idempotency_key=idempotency_key,
            user=user,
            request_id=request_id(http_request),
            source=source,
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected damaged-return-complete failure error_type=%s",
            type(error).__name__,
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error
