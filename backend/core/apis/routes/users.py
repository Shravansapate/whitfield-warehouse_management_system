"""Owner-managed account and warehouse endpoints."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.commons.auth import CurrentUser, get_current_user, require_roles
from backend.core import logger
from backend.core.apis.routes.dependencies import request_id
from backend.core.apis.schemas.common import (
    CreatedAtSort,
    MessageResponse,
    WarehouseResponse,
)
from backend.core.apis.schemas.users import (
    PasswordResetRequest,
    UserCreateRequest,
    UserResponse,
    UserUpdateRequest,
    WarehouseAssignmentRequest,
)
from backend.core.controllers.access_controller import AccessController
from backend.core.database.session import get_session
from backend.core.models.enums import UserRole

router = APIRouter(tags=["access"])
logging = logger(__name__)
controller = AccessController()
Owner = Annotated[CurrentUser, Depends(require_roles(UserRole.OWNER))]


@router.get("/warehouses", response_model=list[WarehouseResponse])
async def list_warehouses(
    response: Response,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    is_active: bool | None = True,
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    sort: CreatedAtSort = CreatedAtSort.CREATED_AT_ASC,
    limit: int = Query(default=100, ge=1, le=500),
) -> list:
    """List a cursor page of warehouses visible to the authenticated user.

    The body remains a JSON list; a further page is advertised in
    the ``X-Next-Cursor`` response header.

    Args:
        response: Mutable HTTP response used for the next-cursor header.
        user: Authenticated current user.
        session: Request-scoped database session.
        is_active: Optional exact active-state filter.
        cursor: Opaque position returned by the prior page.
        sort: Ascending or descending creation-time order.
        limit: Maximum warehouse count.

    Returns:
        Authorized warehouse rows.
    """
    try:
        logging.info("Calling GET /api/v1/warehouses endpoint")
        page = await controller.list_warehouses(
            session,
            user=user,
            is_active=is_active,
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
            "Unexpected warehouse-list failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    response: Response,
    user: Owner,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = Query(default=None, max_length=200),
    role: UserRole | None = None,
    is_active: bool | None = None,
    warehouse_id: uuid.UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    sort: CreatedAtSort = CreatedAtSort.CREATED_AT_DESC,
    limit: int = Query(default=200, ge=1, le=500),
) -> list[dict]:
    """List a filtered safe-user cursor page for the owner console.

    The body remains a JSON list; a further page is advertised in
    the ``X-Next-Cursor`` response header.

    Args:
        response: Mutable HTTP response used for the next-cursor header.
        user: Authorized owner actor.
        session: Request-scoped database session.
        q: Optional case-insensitive name or email search.
        role: Optional exact role filter.
        is_active: Optional exact active-state filter.
        warehouse_id: Optional active assignment filter.
        created_from: Inclusive creation-time lower bound.
        created_to: Inclusive creation-time upper bound.
        cursor: Opaque position returned by the prior page.
        sort: Ascending or descending creation-time order.
        limit: Maximum account count.

    Returns:
        Safe user records.
    """
    try:
        logging.info("Calling GET /api/v1/users endpoint")
        del user
        page = await controller.list_users(
            session,
            query=q,
            role=role,
            is_active=is_active,
            warehouse_id=warehouse_id,
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
            "Unexpected user-list failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    request: UserCreateRequest,
    http_request: Request,
    user: Owner,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Create an owner-managed account and warehouse assignment.

    Args:
        request: Validated account data.
        http_request: Current request context.
        user: Authorized owner actor.
        session: Request-scoped database session.

    Returns:
        Safe created-user record.
    """
    try:
        logging.info("Calling POST /api/v1/users endpoint")
        return await controller.create_user(
            session,
            data=request.model_dump(),
            actor=user,
            request_id=request_id(http_request),
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected user-create failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    request: UserUpdateRequest,
    http_request: Request,
    user: Owner,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Update a user's name, role, or active state.

    Args:
        user_id: Account identifier.
        request: Explicit account fields.
        http_request: Current request context.
        user: Authorized owner actor.
        session: Request-scoped database session.

    Returns:
        Updated safe account record.
    """
    try:
        logging.info(f"Calling PATCH /api/v1/users/{user_id} endpoint")
        return await controller.update_user(
            session,
            user_id=user_id,
            data=request.model_dump(exclude_unset=True),
            actor=user,
            request_id=request_id(http_request),
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected user-update failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.post("/users/{user_id}/reset-password", response_model=MessageResponse)
async def reset_password(
    user_id: uuid.UUID,
    request: PasswordResetRequest,
    http_request: Request,
    user: Owner,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Reset a user's password and revoke active refresh sessions.

    Args:
        user_id: Account identifier.
        request: Replacement password.
        http_request: Current request context.
        user: Authorized owner actor.
        session: Request-scoped database session.

    Returns:
        Reset acknowledgement.
    """
    try:
        logging.info(f"Calling POST /api/v1/users/{user_id}/reset-password endpoint")
        return await controller.reset_password(
            session,
            user_id=user_id,
            password=request.password,
            actor=user,
            request_id=request_id(http_request),
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected password-reset failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.put("/users/{user_id}/warehouse-assignment", response_model=UserResponse)
async def assign_warehouse(
    user_id: uuid.UUID,
    request: WarehouseAssignmentRequest,
    http_request: Request,
    user: Owner,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Replace a non-owner user's active warehouse assignment.

    Args:
        user_id: Account identifier.
        request: New warehouse identifier.
        http_request: Current request context.
        user: Authorized owner actor.
        session: Request-scoped database session.

    Returns:
        Updated safe account record.
    """
    try:
        logging.info(
            f"Calling PUT /api/v1/users/{user_id}/warehouse-assignment endpoint"
        )
        return await controller.assign_warehouse(
            session,
            user_id=user_id,
            warehouse_id=request.warehouse_id,
            actor=user,
            request_id=request_id(http_request),
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected warehouse-assignment failure error_type=%s",
            type(error).__name__,
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error
