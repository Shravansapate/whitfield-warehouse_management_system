"""Password, JWT, refresh-token, and authorization helpers."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.core.database.session import get_session
from backend.core.models.access import User, UserWarehouseAssignment, Warehouse
from backend.core.models.enums import UserRole

settings = get_settings()
password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_prefix}/auth/login")


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Authenticated actor reloaded from PostgreSQL for every request."""

    id: uuid.UUID
    name: str
    email: str
    role: UserRole
    is_active: bool
    warehouse_id: uuid.UUID | None
    warehouse_name: str | None


def normalize_email(email: str) -> str:
    """Normalize an email for identity and uniqueness checks.

    Args:
        email: User-provided email address.

    Returns:
        Trimmed lower-case email.
    """
    return email.strip().lower()


def hash_password(password: str) -> str:
    """Hash a password with the recommended Argon2 configuration.

    Args:
        password: Plain-text password accepted only at the boundary.

    Returns:
        Argon2 password hash.
    """
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify a candidate password without exposing stored material.

    Args:
        password: Candidate plain-text password.
        hashed: Persisted Argon2 hash.

    Returns:
        True when the password is valid.
    """
    return password_hash.verify(password, hashed)


def create_access_token(user: User) -> tuple[str, int]:
    """Create a short-lived signed access token.

    The database remains authoritative for current role and active status.

    Args:
        user: Persisted user receiving the token.

    Returns:
        JWT and lifetime in seconds.
    """
    lifetime = settings.access_token_minutes * 60
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(user.id),
            "iat": now,
            "exp": now + timedelta(seconds=lifetime),
            "jti": str(uuid.uuid4()),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return token, lifetime


def decode_access_token(token: str) -> uuid.UUID:
    """Decode an access token into its user identifier.

    Args:
        token: Encoded bearer JWT.

    Returns:
        Authenticated user UUID.

    Raises:
        HTTPException 401: If the token is missing, invalid, or expired.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp", "iat"]},
        )
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "detail": "Invalid or expired authentication token",
                "code": "INVALID_TOKEN",
            },
            headers={"WWW-Authenticate": "Bearer"},
        ) from error


def new_refresh_token() -> tuple[str, bytes]:
    """Generate an opaque refresh token and its storage hash.

    Returns:
        Raw token for the client and SHA-256 digest for persistence.
    """
    token = secrets.token_urlsafe(48)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> bytes:
    """Hash an opaque refresh token for database lookup.

    Args:
        token: Raw refresh token supplied by the client.

    Returns:
        Fixed-size SHA-256 digest.
    """
    return hashlib.sha256(token.encode("utf-8")).digest()


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CurrentUser:
    """Authenticate a bearer token and reload current access from PostgreSQL.

    Disabled users and missing non-owner assignments are rejected.

    Args:
        token: Bearer access token.
        session: Request-scoped database session.

    Returns:
        Current authenticated actor and warehouse scope.

    Raises:
        HTTPException 401: If the token or user is invalid.
        HTTPException 403: If the user is disabled or unassigned.
    """
    user_id = decode_access_token(token)
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "detail": "Authentication user no longer exists",
                "code": "INVALID_TOKEN",
            },
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"detail": "User account is disabled", "code": "USER_DISABLED"},
        )

    warehouse_id: uuid.UUID | None = None
    warehouse_name: str | None = None
    if user.role != UserRole.OWNER:
        assignment_row = (
            await session.execute(
                select(UserWarehouseAssignment.warehouse_id, Warehouse.name)
                .join(Warehouse, Warehouse.id == UserWarehouseAssignment.warehouse_id)
                .where(
                    UserWarehouseAssignment.user_id == user.id,
                    UserWarehouseAssignment.is_active.is_(True),
                    Warehouse.is_active.is_(True),
                )
            )
        ).one_or_none()
        if assignment_row is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "detail": "No active warehouse assignment",
                    "code": "WAREHOUSE_ASSIGNMENT_REQUIRED",
                },
            )
        warehouse_id, warehouse_name = assignment_row

    return CurrentUser(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        warehouse_id=warehouse_id,
        warehouse_name=warehouse_name,
    )


def require_roles(*roles: UserRole):
    """Create a dependency that permits only selected roles.

    Args:
        roles: Allowed authorization roles.

    Returns:
        FastAPI dependency validating the current actor.
    """

    async def dependency(
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        """Validate the current user's role against the allowed set.

        Args:
            user: Authenticated current user.

        Returns:
            Authorized current user.

        Raises:
            HTTPException 403: If the role is not permitted.
        """
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "detail": "You do not have permission for this action",
                    "code": "ROLE_FORBIDDEN",
                },
            )
        return user

    return dependency


def resolve_warehouse_id(user: CurrentUser, requested: uuid.UUID | None) -> uuid.UUID:
    """Resolve and enforce a command or query warehouse scope.

    Non-owners cannot select another warehouse; owners must select one explicitly.

    Args:
        user: Authenticated actor.
        requested: Caller-supplied warehouse ID, if any.

    Returns:
        Authorized warehouse UUID.

    Raises:
        HTTPException 400: If an owner omits a required warehouse.
        HTTPException 403: If a non-owner requests a different warehouse.
    """
    if user.role == UserRole.OWNER:
        if requested is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "detail": "warehouse_id is required for owner operations",
                    "code": "WAREHOUSE_REQUIRED",
                },
            )
        return requested
    if requested is not None and requested != user.warehouse_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "detail": "Cross-warehouse access is not allowed",
                "code": "WAREHOUSE_FORBIDDEN",
            },
        )
    if user.warehouse_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "detail": "No active warehouse assignment",
                "code": "WAREHOUSE_ASSIGNMENT_REQUIRED",
            },
        )
    return user.warehouse_id
