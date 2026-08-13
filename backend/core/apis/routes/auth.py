"""Login, token rotation, logout, and current-user endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.commons.auth import CurrentUser, get_current_user
from backend.core import logger
from backend.core.apis.schemas.auth import (
    CurrentUserResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
)
from backend.core.apis.schemas.common import MessageResponse
from backend.core.controllers.auth_controller import AuthController
from backend.core.database.session import get_session

router = APIRouter(prefix="/auth", tags=["authentication"])
logging = logger(__name__)
controller = AuthController()


@router.post("/login", response_model=TokenResponse)
async def login(
    request: LoginRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Authenticate an active WMS user with email and password.

    Args:
        request: Login credentials.
        session: Request-scoped database session.

    Returns:
        Access/refresh token pair and user profile.

    Raises:
        HTTPException: For invalid credentials, disabled users, or server errors.
    """
    try:
        logging.info("Calling POST /api/v1/auth/login endpoint")
        return await controller.login(
            session, email=request.email, password=request.password
        )
    except HTTPException as error:
        logging.warning("Login request rejected error_type=%s", type(error).__name__)
        raise
    except Exception as error:
        logging.error("Unexpected login failure error_type=%s", type(error).__name__)
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: RefreshRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Rotate a valid refresh session.

    Args:
        request: Opaque refresh token.
        session: Request-scoped database session.

    Returns:
        Rotated token pair and user profile.

    Raises:
        HTTPException: For invalid refresh sessions or server errors.
    """
    try:
        logging.info("Calling POST /api/v1/auth/refresh endpoint")
        return await controller.refresh(session, refresh_token=request.refresh_token)
    except HTTPException as error:
        logging.warning("Refresh request rejected error_type=%s", type(error).__name__)
        raise
    except Exception as error:
        logging.error("Unexpected refresh failure error_type=%s", type(error).__name__)
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.post("/logout", response_model=MessageResponse)
async def logout(
    request: LogoutRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Revoke an opaque refresh session.

    Args:
        request: Refresh token to revoke.
        session: Request-scoped database session.

    Returns:
        Logout acknowledgement.

    Raises:
        HTTPException 500: If revocation unexpectedly fails.
    """
    try:
        logging.info("Calling POST /api/v1/auth/logout endpoint")
        return await controller.logout(session, refresh_token=request.refresh_token)
    except HTTPException:
        raise
    except Exception as error:
        logging.error("Unexpected logout failure error_type=%s", type(error).__name__)
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.get("/me", response_model=CurrentUserResponse)
async def me(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Return the authenticated user's current database-backed permissions.

    Args:
        user: Authenticated current user.
        session: Request-scoped database session.

    Returns:
        Current user profile and allowed warehouses.

    Raises:
        HTTPException: For authentication or server errors.
    """
    try:
        logging.info("Calling GET /api/v1/auth/me endpoint")
        return await controller.me(session, user=user)
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected current-user failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error
