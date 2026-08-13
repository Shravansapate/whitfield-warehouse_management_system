"""Warehouse and user access models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from backend.core.models.enums import UserRole, enum_values


class Warehouse(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A physical warehouse and security boundary."""

    __tablename__ = "warehouses"

    code: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Authenticated WMS operator."""

    __tablename__ = "users"
    __table_args__ = (
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
        CheckConstraint(
            "role IN ('owner', 'manager', 'trusted', 'staff')",
            name="user_role",
        ),
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            native_enum=False,
            create_constraint=False,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class UserWarehouseAssignment(UUIDPrimaryKeyMixin, Base):
    """The active warehouse scope assigned to a non-owner user."""

    __tablename__ = "user_warehouse_assignments"
    __table_args__ = (
        Index(
            "uq_user_active_warehouse_assignment",
            "user_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index("ix_assignment_warehouse_active", "warehouse_id", "is_active"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
