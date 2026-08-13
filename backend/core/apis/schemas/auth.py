"""Authentication API contracts."""

from __future__ import annotations

import uuid

from pydantic import EmailStr, Field

from backend.core.apis.schemas.common import APIModel, WarehouseResponse
from backend.core.models.enums import UserRole


class LoginRequest(APIModel):
    """Email and password login request."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


class RefreshRequest(APIModel):
    """Opaque refresh-token rotation request."""

    refresh_token: str = Field(min_length=32, max_length=512)


class LogoutRequest(APIModel):
    """Refresh-session revocation request."""

    refresh_token: str = Field(min_length=32, max_length=512)


class CurrentUserResponse(APIModel):
    """Safe current-user profile and warehouse choices."""

    id: uuid.UUID
    name: str
    email: EmailStr
    role: UserRole
    is_active: bool
    warehouse_id: uuid.UUID | None = None
    warehouse_name: str | None = None
    warehouses: list[WarehouseResponse] = Field(default_factory=list)


class TokenResponse(APIModel):
    """Access and refresh token pair returned after authentication."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: CurrentUserResponse
