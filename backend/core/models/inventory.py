"""Inventory balance, movement, and adjustment models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from backend.core.models.enums import (
    AdjustmentStatus,
    AuditSource,
    MovementType,
    enum_values,
)


class InventoryBalance(UUIDPrimaryKeyMixin, Base):
    """Current on-hand and reserved stock for one warehouse product."""

    __tablename__ = "inventory_balances"
    __table_args__ = (
        UniqueConstraint(
            "warehouse_id", "product_id", name="uq_inventory_warehouse_product"
        ),
        CheckConstraint("on_hand >= 0", name="on_hand_nonnegative"),
        CheckConstraint("reserved >= 0", name="reserved_nonnegative"),
        CheckConstraint("reserved <= on_hand", name="reserved_not_above_on_hand"),
        Index("ix_inventory_warehouse_updated", "warehouse_id", "updated_at"),
    )

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    on_hand: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default=text("0")
    )
    reserved: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class InventoryMovement(UUIDPrimaryKeyMixin, Base):
    """Immutable ledger entry for every balance change."""

    __tablename__ = "inventory_movements"
    __table_args__ = (
        Index("ix_movements_warehouse_created", "warehouse_id", "created_at"),
        Index("ix_movements_reference", "reference_type", "reference_id"),
        CheckConstraint(
            "movement_type IN ('OPENING_BALANCE', 'RECEIPT', 'RESERVE', 'RELEASE', 'SHIP', 'ADJUST', 'TRANSFER_OUT', 'TRANSFER_IN')",
            name="movement_type",
        ),
        CheckConstraint(
            "source IN ('web', 'scanner', 'voice', 'automation', 'api', 'system')",
            name="audit_source",
        ),
    )

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    movement_type: Mapped[MovementType] = mapped_column(
        Enum(
            MovementType,
            name="movement_type",
            native_enum=False,
            create_constraint=False,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    on_hand_delta: Mapped[int] = mapped_column(nullable=False, default=0)
    reserved_delta: Mapped[int] = mapped_column(nullable=False, default=0)
    reference_type: Mapped[str] = mapped_column(String(80), nullable=False)
    reference_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source: Mapped[AuditSource] = mapped_column(
        Enum(
            AuditSource,
            name="audit_source",
            native_enum=False,
            create_constraint=False,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    on_hand_after: Mapped[int] = mapped_column(nullable=False)
    reserved_after: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class InventoryAdjustment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Reasoned manual stock correction request and result."""

    __tablename__ = "inventory_adjustments"
    __table_args__ = (
        CheckConstraint("quantity_delta <> 0", name="quantity_delta_nonzero"),
        CheckConstraint("status IN ('applied')", name="adjustment_status"),
    )

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity_delta: Mapped[int] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AdjustmentStatus] = mapped_column(
        Enum(
            AdjustmentStatus,
            name="adjustment_status",
            native_enum=False,
            create_constraint=False,
            values_callable=enum_values,
        ),
        nullable=False,
        default=AdjustmentStatus.APPLIED,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
