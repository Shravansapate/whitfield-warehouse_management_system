"""All-or-nothing outbound order and fulfillment endpoints."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.commons.auth import CurrentUser, get_current_user
from backend.core import logger
from backend.core.apis.routes.dependencies import (
    IdempotencyKey,
    SourceHeader,
    request_id,
)
from backend.core.apis.schemas.common import CreatedAtSort
from backend.core.apis.schemas.orders import (
    CancelOrderRequest,
    LabelRequest,
    OrderCreateRequest,
    OrderResponse,
    PackageRequest,
    PickItemRequest,
)
from backend.core.controllers.order_controller import OrderController
from backend.core.database.session import get_session
from backend.core.models.enums import AuditSource, OrderStatus
from backend.core.services.order_service import OrderService

router = APIRouter(prefix="/orders", tags=["orders"])
logging = logger(__name__)
controller = OrderController()
service = OrderService()


@router.post("", response_model=OrderResponse, status_code=201)
async def create_order(
    request: OrderCreateRequest,
    http_request: Request,
    idempotency_key: IdempotencyKey,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: SourceHeader = AuditSource.WEB,
) -> dict:
    """Create and atomically allocate a multi-product order.

    Args:
        request: External reference, warehouse, and product lines.
        http_request: Current request context.
        idempotency_key: Required retry identity.
        user: Authenticated actor.
        session: Request-scoped database session.
        source: Originating client channel.

    Returns:
        Allocated or cannot-fulfill order.
    """
    try:
        logging.info("Calling POST /api/v1/orders endpoint")
        return await service.create_order(
            session,
            external_reference=request.external_reference,
            warehouse_id=request.warehouse_id,
            items=[item.model_dump() for item in request.items],
            idempotency_key=idempotency_key,
            user=user,
            request_id=request_id(http_request),
            source=source,
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected order-create failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.get("", response_model=list[OrderResponse])
async def list_orders(
    response: Response,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    warehouse_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    status_filter: Annotated[OrderStatus | None, Query(alias="status")] = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    sort: CreatedAtSort = CreatedAtSort.CREATED_AT_DESC,
) -> list[dict]:
    """List a filtered cursor page in an authorized warehouse.

    The body remains a JSON list; a further page is advertised in
    the ``X-Next-Cursor`` response header.

    Args:
        response: Mutable HTTP response used for the next-cursor header.
        user: Authenticated actor.
        session: Request-scoped database session.
        warehouse_id: Optional owner warehouse selector.
        limit: Maximum order count.
        cursor: Opaque position returned by the prior page.
        status_filter: Optional exact lifecycle status.
        created_from: Inclusive creation-time lower bound.
        created_to: Inclusive creation-time upper bound.
        sort: Ascending or descending creation-time order.

    Returns:
        Expanded outbound orders.
    """
    try:
        logging.info("Calling GET /api/v1/orders endpoint")
        page = await controller.list_orders(
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
            "Unexpected order-list failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Read one authorized order with lines and package state.

    Args:
        order_id: Order identifier.
        user: Authenticated actor.
        session: Request-scoped database session.

    Returns:
        Expanded outbound order.
    """
    try:
        logging.info(f"Calling GET /api/v1/orders/{order_id} endpoint")
        return await controller.get_order(session, order_id=order_id, user=user)
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected order-read failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.post("/{order_id}/start-picking", response_model=OrderResponse)
async def start_picking(
    order_id: uuid.UUID,
    http_request: Request,
    idempotency_key: IdempotencyKey,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: SourceHeader = AuditSource.WEB,
) -> dict:
    """Start physical picking for an allocated order.

    Args:
        order_id: Allocated order identifier.
        http_request: Current request context.
        idempotency_key: Required retry identity.
        user: Authenticated actor.
        session: Request-scoped database session.
        source: Originating client channel.

    Returns:
        Picking order.
    """
    try:
        logging.info(f"Calling POST /api/v1/orders/{order_id}/start-picking endpoint")
        return await service.start_picking(
            session,
            order_id=order_id,
            idempotency_key=idempotency_key,
            user=user,
            request_id=request_id(http_request),
            source=source,
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected start-picking failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.post("/{order_id}/pack", response_model=OrderResponse)
async def pack_order(
    order_id: uuid.UUID,
    request: PackageRequest,
    http_request: Request,
    idempotency_key: IdempotencyKey,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: SourceHeader = AuditSource.WEB,
) -> dict:
    """Record positive measurements and mark a picking order packed.

    Args:
        order_id: Picking order identifier.
        request: Package weight and dimensions.
        http_request: Current request context.
        idempotency_key: Required retry identity.
        user: Authenticated actor.
        session: Request-scoped database session.
        source: Originating client channel.

    Returns:
        Packed order.
    """
    try:
        logging.info(f"Calling POST /api/v1/orders/{order_id}/pack endpoint")
        return await service.pack(
            session,
            order_id=order_id,
            measurements=request.model_dump(),
            idempotency_key=idempotency_key,
            user=user,
            request_id=request_id(http_request),
            source=source,
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error("Unexpected pack failure error_type=%s", type(error).__name__)
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.post(
    "/{order_id}/items/{item_id}/pick",
    response_model=OrderResponse,
)
async def confirm_picked_item(
    order_id: uuid.UUID,
    item_id: uuid.UUID,
    request: PickItemRequest,
    http_request: Request,
    idempotency_key: IdempotencyKey,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: SourceHeader = AuditSource.SCANNER,
) -> dict:
    """Confirm the physical picked count for one order line.

    Args:
        order_id: Picking order identifier.
        item_id: Order line identifier.
        request: Confirmed physical quantity.
        http_request: Current request context.
        idempotency_key: Required retry identity.
        user: Authenticated actor.
        session: Request-scoped database session.
        source: Originating trusted browser/scanner channel.

    Returns:
        Updated order with its persisted checklist.
    """
    try:
        logging.info(
            f"Calling POST /api/v1/orders/{order_id}/items/{item_id}/pick endpoint"
        )
        return await service.confirm_picked_item(
            session,
            order_id=order_id,
            item_id=item_id,
            picked_quantity=request.picked_quantity,
            idempotency_key=idempotency_key,
            user=user,
            request_id=request_id(http_request),
            source=source,
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected pick-item failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.post("/{order_id}/create-label", response_model=OrderResponse)
async def create_label(
    order_id: uuid.UUID,
    request: LabelRequest,
    http_request: Request,
    idempotency_key: IdempotencyKey,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: SourceHeader = AuditSource.WEB,
) -> dict:
    """Create a carrier label without shipping the order.

    Args:
        order_id: Packed order identifier.
        request: Carrier and service selection.
        http_request: Current request context.
        idempotency_key: Required retry identity.
        user: Authenticated actor.
        session: Request-scoped database session.
        source: Originating client channel.

    Returns:
        Label-created order.
    """
    try:
        logging.info(f"Calling POST /api/v1/orders/{order_id}/create-label endpoint")
        return await service.create_label(
            session,
            order_id=order_id,
            carrier=request.carrier,
            service_level=request.service_level,
            idempotency_key=idempotency_key,
            user=user,
            request_id=request_id(http_request),
            source=source,
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected label-create failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.post("/{order_id}/ship", response_model=OrderResponse)
async def ship_order(
    order_id: uuid.UUID,
    http_request: Request,
    idempotency_key: IdempotencyKey,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: SourceHeader = AuditSource.WEB,
) -> dict:
    """Consume reservations and mark a label-created order shipped.

    Args:
        order_id: Label-created order identifier.
        http_request: Current request context.
        idempotency_key: Required retry identity.
        user: Authenticated actor.
        session: Request-scoped database session.
        source: Originating client channel.

    Returns:
        Shipped order.
    """
    try:
        logging.info(f"Calling POST /api/v1/orders/{order_id}/ship endpoint")
        return await service.ship(
            session,
            order_id=order_id,
            idempotency_key=idempotency_key,
            user=user,
            request_id=request_id(http_request),
            source=source,
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error("Unexpected ship failure error_type=%s", type(error).__name__)
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.post("/{order_id}/cancel", response_model=OrderResponse)
async def cancel_order(
    order_id: uuid.UUID,
    request: CancelOrderRequest,
    http_request: Request,
    idempotency_key: IdempotencyKey,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: SourceHeader = AuditSource.WEB,
) -> dict:
    """Cancel and retain a non-shipped order while releasing reservations.

    Args:
        order_id: Order identifier.
        request: Mandatory cancellation reason.
        http_request: Current request context.
        idempotency_key: Required retry identity.
        user: Authenticated actor.
        session: Request-scoped database session.
        source: Originating client channel.

    Returns:
        Cancelled order.
    """
    try:
        logging.info(f"Calling POST /api/v1/orders/{order_id}/cancel endpoint")
        return await service.cancel(
            session,
            order_id=order_id,
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
            "Unexpected order-cancel failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error
