"""Authentication and refresh-session controller."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.commons.auth import (
    CurrentUser,
    create_access_token,
    hash_refresh_token,
    new_refresh_token,
    normalize_email,
    verify_password,
)
from backend.core import logger
from backend.core.config import get_settings
from backend.core.cruds.access_crud import AccessCRUD
from backend.core.models.access import User
from backend.core.models.enums import UserRole
from backend.core.models.reliability import RefreshSession
from backend.core.services.transaction import command_transaction

logging = logger(__name__)
settings = get_settings()


class AuthController:
    """Authenticate users and manage revocable refresh sessions."""

    def __init__(self) -> None:
        """Initialize access persistence collaborators."""
        self.access = AccessCRUD()

    async def _user_payload(
        self,
        session: AsyncSession,
        user: User | CurrentUser,
    ) -> dict:
        """Build a safe current-user payload with allowed warehouses.

        Args:
            session: Request-scoped database session.
            user: Persisted or authenticated user identity.

        Returns:
            User profile and authorized warehouses.
        """
        warehouses = await self.access.list_warehouses(session)
        warehouse_id = getattr(user, "warehouse_id", None)
        warehouse_name = getattr(user, "warehouse_name", None)
        if user.role != UserRole.OWNER:
            assignment = await self.access.get_assignment(session, user.id)
            if assignment:
                warehouse_id, warehouse_name = assignment
                warehouses = [item for item in warehouses if item.id == warehouse_id]
            else:
                warehouses = []
        return {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "is_active": user.is_active,
            "warehouse_id": warehouse_id,
            "warehouse_name": warehouse_name,
            "warehouses": warehouses,
        }

    async def login(self, session: AsyncSession, *, email: str, password: str) -> dict:
        """Authenticate credentials and issue access and refresh tokens.

        Args:
            session: Request-scoped database session.
            email: User email.
            password: Plain-text candidate password.

        Returns:
            Token pair and safe user profile.

        Raises:
            HTTPException 401: If credentials are invalid.
            HTTPException 403: If the account is disabled or unassigned.
        """
        logging.info("Executing AuthController.login")
        async with command_transaction(session):
            user = await self.access.get_user_by_email(session, normalize_email(email))
            if user is None or not verify_password(password, user.hashed_password):
                logging.warning("Authentication rejected for invalid credentials")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "detail": "Invalid email or password",
                        "code": "INVALID_CREDENTIALS",
                    },
                )
            if not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "detail": "User account is disabled",
                        "code": "USER_DISABLED",
                    },
                )
            profile = await self._user_payload(session, user)
            if user.role != UserRole.OWNER and profile["warehouse_id"] is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "detail": "No active warehouse assignment",
                        "code": "WAREHOUSE_ASSIGNMENT_REQUIRED",
                    },
                )
            access_token, expires_in = create_access_token(user)
            refresh_token, digest = new_refresh_token()
            session.add(
                RefreshSession(
                    user_id=user.id,
                    token_hash=digest,
                    expires_at=datetime.now(UTC)
                    + timedelta(days=settings.refresh_token_days),
                )
            )
            await session.flush()
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": expires_in,
            "user": profile,
        }

    async def refresh(self, session: AsyncSession, *, refresh_token: str) -> dict:
        """Rotate a valid refresh token and issue a new access token.

        Args:
            session: Request-scoped database session.
            refresh_token: Opaque client refresh token.

        Returns:
            Rotated token pair and safe user profile.

        Raises:
            HTTPException 401: If the session is invalid, revoked, or expired.
        """
        logging.info("Executing AuthController.refresh")
        async with command_transaction(session):
            refresh_session = (
                await session.execute(
                    select(RefreshSession)
                    .where(
                        RefreshSession.token_hash == hash_refresh_token(refresh_token)
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            now = datetime.now(UTC)
            if (
                refresh_session is None
                or refresh_session.revoked_at is not None
                or refresh_session.expires_at <= now
            ):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "detail": "Refresh session is invalid or expired",
                        "code": "INVALID_REFRESH_TOKEN",
                    },
                )
            user = await session.get(User, refresh_session.user_id)
            if user is None or not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "detail": "Refresh session user is unavailable",
                        "code": "INVALID_REFRESH_TOKEN",
                    },
                )
            refresh_session.revoked_at = now
            new_token, digest = new_refresh_token()
            session.add(
                RefreshSession(
                    user_id=user.id,
                    token_hash=digest,
                    expires_at=now + timedelta(days=settings.refresh_token_days),
                )
            )
            profile = await self._user_payload(session, user)
            access_token, expires_in = create_access_token(user)
            await session.flush()
        return {
            "access_token": access_token,
            "refresh_token": new_token,
            "token_type": "bearer",
            "expires_in": expires_in,
            "user": profile,
        }

    async def logout(self, session: AsyncSession, *, refresh_token: str) -> dict:
        """Revoke a refresh session without revealing whether it existed.

        Args:
            session: Request-scoped database session.
            refresh_token: Opaque client refresh token.

        Returns:
            Successful logout acknowledgement.
        """
        logging.info("Executing AuthController.logout")
        async with command_transaction(session):
            refresh_session = (
                await session.execute(
                    select(RefreshSession)
                    .where(
                        RefreshSession.token_hash == hash_refresh_token(refresh_token)
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if refresh_session is not None and refresh_session.revoked_at is None:
                refresh_session.revoked_at = datetime.now(UTC)
                await session.flush()
        return {"detail": "Logged out"}

    async def me(self, session: AsyncSession, *, user: CurrentUser) -> dict:
        """Return the current database-backed identity and warehouse choices.

        Args:
            session: Request-scoped database session.
            user: Authenticated current user.

        Returns:
            Safe current-user payload.
        """
        logging.info("Executing AuthController.me")
        return await self._user_payload(session, user)
