"""Inventory balance, low-stock, movement, and adjustment endpoints."""

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
from backend.core.apis.schemas.common import CreatedAtSort, InventorySort
from backend.core.apis.schemas.inventory import (
    InventoryAdjustmentRequest,
    InventoryResponse,
    MovementResponse,
    OpeningBalanceRequest,
)
from backend.core.controllers.inventory_controller import InventoryController
from backend.core.database.session import get_session
from backend.core.models.enums import AuditSource, MovementType, UserRole
from backend.core.services.inventory_service import InventoryService

router = APIRouter(prefix="/inventory", tags=["inventory"])
logging = logger(__name__)
controller = InventoryController()
service = InventoryService()


@router.get("", response_model=list[InventoryResponse])
async def list_inventory(
    response: Response,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    warehouse_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=200),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    sort: InventorySort = InventorySort.NAME_ASC,
    limit: int = Query(default=200, ge=1, le=500),
) -> list[dict]:
    """List a filtered inventory cursor page in an authorized warehouse.

    The body remains a JSON list; a further page is advertised in
    the ``X-Next-Cursor`` response header.

    Args:
        response: Mutable HTTP response used for the next-cursor header.
        user: Authenticated actor.
        session: Request-scoped database session.
        warehouse_id: Optional owner warehouse selector.
        q: Optional SKU, UPC, or product-name search.
        cursor: Opaque position returned by the prior page.
        sort: Product-name or availability order.
        limit: Maximum balance rows returned.

    Returns:
        Inventory balance rows.
    """
    try:
        logging.info("Calling GET /api/v1/inventory endpoint")
        page = await controller.list_inventory(
            session,
            warehouse_id=warehouse_id,
            user=user,
            query=q,
            cursor=cursor,
            sort=sort,
            limit=limit,
        )
        if page.next_cursor is not None:
            response.headers["X-Next-Cursor"] = page.next_cursor
        return page.items
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected inventory-list failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.get("/low-stock", response_model=list[InventoryResponse])
async def low_stock(
    response: Response,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    warehouse_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=200),
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    sort: InventorySort = InventorySort.NAME_ASC,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    """List a filtered low-stock cursor page for an authorized warehouse.

    The body remains a JSON list; a further page is advertised in
    the ``X-Next-Cursor`` response header.

    Args:
        response: Mutable HTTP response used for the next-cursor header.
        user: Authenticated actor.
        session: Request-scoped database session.
        warehouse_id: Optional owner warehouse selector.
        q: Optional SKU, UPC, or product-name search.
        cursor: Opaque position returned by the prior page.
        sort: Product-name or availability order.
        limit: Maximum low-stock rows returned.

    Returns:
        Low-stock balance rows.
    """
    try:
        logging.info("Calling GET /api/v1/inventory/low-stock endpoint")
        page = await controller.list_inventory(
            session,
            warehouse_id=warehouse_id,
            user=user,
            low_stock_only=True,
            query=q,
            cursor=cursor,
            sort=sort,
            limit=limit,
        )
        if page.next_cursor is not None:
            response.headers["X-Next-Cursor"] = page.next_cursor
        return page.items
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected low-stock failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.get("/{product_id}", response_model=InventoryResponse)
async def get_inventory(
    product_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    warehouse_id: uuid.UUID | None = None,
) -> dict:
    """Read one product balance in an authorized warehouse.

    Products without a stored balance are represented with zero quantities.

    Args:
        product_id: Product identifier.
        user: Authenticated actor.
        session: Request-scoped database session.
        warehouse_id: Optional owner warehouse selector.

    Returns:
        Product inventory row.
    """
    try:
        logging.info(f"Calling GET /api/v1/inventory/{product_id} endpoint")
        return await controller.get_inventory(
            session,
            product_id=product_id,
            warehouse_id=warehouse_id,
            user=user,
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected inventory-read failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.get("/{product_id}/movements", response_model=list[MovementResponse])
async def movements(
    product_id: uuid.UUID,
    response: Response,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    warehouse_id: uuid.UUID | None = None,
    movement_type: MovementType | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    sort: CreatedAtSort = CreatedAtSort.CREATED_AT_DESC,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list:
    """List a filtered movement cursor page for a warehouse product.

    The body remains a JSON list; a further page is advertised in
    the ``X-Next-Cursor`` response header.

    Args:
        product_id: Product identifier.
        response: Mutable HTTP response used for the next-cursor header.
        user: Authenticated actor.
        session: Request-scoped database session.
        warehouse_id: Optional owner warehouse selector.
        movement_type: Optional exact movement-kind filter.
        created_from: Inclusive creation-time lower bound.
        created_to: Inclusive creation-time upper bound.
        cursor: Opaque position returned by the prior page.
        sort: Ascending or descending creation-time order.
        limit: Maximum movement count.

    Returns:
        Movement ledger entries.
    """
    try:
        logging.info(f"Calling GET /api/v1/inventory/{product_id}/movements endpoint")
        page = await controller.movements(
            session,
            product_id=product_id,
            warehouse_id=warehouse_id,
            user=user,
            movement_type=movement_type,
            created_from=created_from,
            created_to=created_to,
            cursor=cursor,
            sort=sort,
            limit=limit,
        )
        if page.next_cursor is not None:
            response.headers["X-Next-Cursor"] = page.next_cursor
        return page.items
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected movement-list failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.post("/adjustments", status_code=201)
async def adjustment(
    request: InventoryAdjustmentRequest,
    http_request: Request,
    idempotency_key: IdempotencyKey,
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.TRUSTED, UserRole.MANAGER, UserRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: SourceHeader = AuditSource.WEB,
) -> dict:
    """Apply a reasoned manual stock adjustment atomically.

    Args:
        request: Product, signed delta, and reason.
        http_request: Current request context.
        idempotency_key: Required retry identity.
        user: Authorized trusted, manager, or owner actor.
        session: Request-scoped database session.
        source: Originating client channel.

    Returns:
        Applied adjustment and resulting balance.
    """
    try:
        logging.info("Calling POST /api/v1/inventory/adjustments endpoint")
        return await service.adjust(
            session,
            warehouse_id=request.warehouse_id,
            product_id=request.product_id,
            quantity_delta=request.quantity_delta,
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
            "Unexpected inventory-adjustment failure error_type=%s",
            type(error).__name__,
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.post("/opening-balances", status_code=201)
async def opening_balance(
    request: OpeningBalanceRequest,
    http_request: Request,
    idempotency_key: IdempotencyKey,
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.OWNER))],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: SourceHeader = AuditSource.WEB,
) -> dict:
    """Post a verified one-time opening balance.

    Args:
        request: Warehouse, product, quantity, and reason.
        http_request: Current request context.
        idempotency_key: Required retry identity.
        user: Authorized owner actor.
        session: Request-scoped database session.
        source: Originating client channel.

    Returns:
        Resulting opening balance.
    """
    try:
        logging.info("Calling POST /api/v1/inventory/opening-balances endpoint")
        return await service.opening_balance(
            session,
            warehouse_id=request.warehouse_id,
            product_id=request.product_id,
            quantity=request.quantity,
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
            "Unexpected opening-balance failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error
