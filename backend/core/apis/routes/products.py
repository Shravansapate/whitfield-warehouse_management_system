"""Product master, scanner search, and low-stock threshold endpoints."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.commons.auth import CurrentUser, get_current_user, require_roles
from backend.core import logger
from backend.core.apis.routes.dependencies import request_id
from backend.core.apis.schemas.common import CreatedAtSort
from backend.core.apis.schemas.products import (
    ProductCreateRequest,
    ProductResponse,
    ProductUpdateRequest,
    ThresholdRequest,
)
from backend.core.controllers.product_controller import ProductController
from backend.core.database.session import get_session
from backend.core.models.enums import UserRole
from backend.core.models.product import Product

router = APIRouter(tags=["products"])
logging = logger(__name__)
controller = ProductController()


@router.post("/products", response_model=ProductResponse, status_code=201)
async def create_product(
    request: ProductCreateRequest,
    http_request: Request,
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.OWNER))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Product:
    """Create a globally unique product master record.

    Args:
        request: Validated product data.
        http_request: Current request context.
        user: Authorized owner actor.
        session: Request-scoped database session.

    Returns:
        Created product.
    """
    try:
        logging.info("Calling POST /api/v1/products endpoint")
        return await controller.create(
            session,
            data=request.model_dump(),
            user=user,
            request_id=request_id(http_request),
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected product-create failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.get("/products/search", response_model=list[ProductResponse])
async def search_products(
    response: Response,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str = Query(default="", max_length=200),
    is_active: bool | None = True,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    sort: CreatedAtSort = CreatedAtSort.CREATED_AT_DESC,
    limit: int = Query(default=30, ge=1, le=500),
) -> list[Product]:
    """Search a filtered product-master cursor page.

    The body remains a JSON list; a further page is advertised in
    the ``X-Next-Cursor`` response header.

    Args:
        response: Mutable HTTP response used for the next-cursor header.
        user: Authenticated actor.
        session: Request-scoped database session.
        q: Scanner or text query.
        is_active: Optional exact active-state filter.
        created_from: Inclusive creation-time lower bound.
        created_to: Inclusive creation-time upper bound.
        cursor: Opaque position returned by the prior page.
        sort: Ascending or descending creation-time order.
        limit: Maximum product count.

    Returns:
        Matching products.
    """
    try:
        logging.info("Calling GET /api/v1/products/search endpoint")
        del user
        page = await controller.search(
            session,
            query=q,
            is_active=is_active,
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
            "Unexpected product-search failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.get("/products/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Product:
    """Read one active or inactive product by identifier.

    Args:
        product_id: Product identifier.
        user: Authenticated actor.
        session: Request-scoped database session.

    Returns:
        Product master record.
    """
    try:
        logging.info(f"Calling GET /api/v1/products/{product_id} endpoint")
        del user
        product = await session.get(Product, product_id)
        if product is None:
            raise HTTPException(
                status_code=404,
                detail={"detail": "Product not found", "code": "PRODUCT_NOT_FOUND"},
            )
        return product
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected product-read failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.patch("/products/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: uuid.UUID,
    request: ProductUpdateRequest,
    http_request: Request,
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.OWNER))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Product:
    """Update mutable product master fields.

    Args:
        product_id: Product identifier.
        request: Explicit update fields.
        http_request: Current request context.
        user: Authorized owner actor.
        session: Request-scoped database session.

    Returns:
        Updated product.
    """
    try:
        logging.info(f"Calling PATCH /api/v1/products/{product_id} endpoint")
        return await controller.update(
            session,
            product_id=product_id,
            data=request.model_dump(exclude_unset=True),
            user=user,
            request_id=request_id(http_request),
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected product-update failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.put("/warehouses/{warehouse_id}/products/{product_id}/threshold")
async def set_threshold(
    warehouse_id: uuid.UUID,
    product_id: uuid.UUID,
    request: ThresholdRequest,
    http_request: Request,
    user: Annotated[
        CurrentUser, Depends(require_roles(UserRole.MANAGER, UserRole.OWNER))
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Set a warehouse-specific product low-stock threshold.

    Args:
        warehouse_id: Warehouse identifier.
        product_id: Product identifier.
        request: Nonnegative threshold.
        http_request: Current request context.
        user: Authorized manager or owner actor.
        session: Request-scoped database session.

    Returns:
        Updated threshold payload.
    """
    try:
        logging.info(
            f"Calling PUT /api/v1/warehouses/{warehouse_id}/products/{product_id}/threshold endpoint"
        )
        return await controller.set_threshold(
            session,
            warehouse_id=warehouse_id,
            product_id=product_id,
            threshold=request.low_stock_threshold,
            user=user,
            request_id=request_id(http_request),
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected threshold-update failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error
