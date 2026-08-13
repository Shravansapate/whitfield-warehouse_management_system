"""Owner-managed user API contracts."""

from __future__ import annotations

import uuid

from pydantic import EmailStr, Field, model_validator

from backend.core.apis.schemas.common import APIModel
from backend.core.models.enums import UserRole


class UserCreateRequest(APIModel):
    """Create an operator and optional warehouse assignment."""

    name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    password: str = Field(min_length=10, max_length=256)
    role: UserRole
    warehouse_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_assignment(self) -> UserCreateRequest:
        """Require a warehouse for every non-owner account.

        Returns:
            Validated user creation request.

        Raises:
            ValueError: If a non-owner has no warehouse or an owner has one.
        """
        if self.role == UserRole.OWNER and self.warehouse_id is not None:
            raise ValueError(
                "Owner accounts operate globally and cannot have an assignment"
            )
        if self.role != UserRole.OWNER and self.warehouse_id is None:
            raise ValueError("warehouse_id is required for non-owner accounts")
        return self


class UserUpdateRequest(APIModel):
    """Update safe account attributes."""

    name: str | None = Field(default=None, min_length=2, max_length=160)
    role: UserRole | None = None
    is_active: bool | None = None
    warehouse_id: uuid.UUID | None = None


class PasswordResetRequest(APIModel):
    """Owner-provided replacement password."""

    password: str = Field(min_length=10, max_length=256)


class WarehouseAssignmentRequest(APIModel):
    """Replace a non-owner user's active warehouse assignment."""

    warehouse_id: uuid.UUID


class UserResponse(APIModel):
    """Safe account details excluding password material."""

    id: uuid.UUID
    name: str
    email: EmailStr
    role: UserRole
    is_active: bool
    warehouse_id: uuid.UUID | None = None
    warehouse_name: str | None = None
