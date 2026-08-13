"""Outbound order, reservation, and package models."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from backend.core.models.enums import (
    OrderStatus,
    PackageStatus,
    ReservationStatus,
    enum_values,
)


class Order(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Multi-product outbound order assigned to exactly one warehouse."""

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("external_reference", name="uq_order_external_reference"),
        Index(
            "ix_orders_warehouse_status_created", "warehouse_id", "status", "created_at"
        ),
        CheckConstraint(
            "status IN ('pending', 'allocated', 'picking', 'packed', 'label_created', 'shipped', 'cannot_fulfill', 'cancelled')",
            name="order_status",
        ),
    )

    external_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[OrderStatus] = mapped_column(
        Enum(
            OrderStatus,
            name="order_status",
            native_enum=False,
            create_constraint=False,
            values_callable=enum_values,
        ),
        nullable=False,
        default=OrderStatus.PENDING,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrderItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Consolidated product quantity requested by an order."""

    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "product_id", name="uq_order_product"),
        CheckConstraint("quantity > 0", name="order_item_quantity_positive"),
        CheckConstraint(
            "picked_quantity >= 0 AND picked_quantity <= quantity",
            name="order_item_picked_quantity_valid",
        ),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(nullable=False)
    picked_quantity: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default=text("0")
    )


class InventoryReservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Order item claim against a warehouse inventory balance."""

    __tablename__ = "inventory_reservations"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="reservation_quantity_positive"),
        Index(
            "uq_active_reservation_order_item",
            "order_item_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_reservations_order_status", "order_id", "status"),
        CheckConstraint(
            "status IN ('active', 'released', 'consumed')",
            name="reservation_status",
        ),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    order_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order_items.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[ReservationStatus] = mapped_column(
        Enum(
            ReservationStatus,
            name="reservation_status",
            native_enum=False,
            create_constraint=False,
            values_callable=enum_values,
        ),
        nullable=False,
        default=ReservationStatus.ACTIVE,
    )


class OutboundPackage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Measured parcel and carrier label associated with an order."""

    __tablename__ = "outbound_packages"
    __table_args__ = (
        CheckConstraint("weight > 0", name="package_weight_positive"),
        CheckConstraint("length > 0", name="package_length_positive"),
        CheckConstraint("width > 0", name="package_width_positive"),
        CheckConstraint("height > 0", name="package_height_positive"),
        CheckConstraint(
            "status IN ('packed', 'label_created', 'shipped')",
            name="package_status",
        ),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    weight: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    weight_unit: Mapped[str] = mapped_column(String(12), nullable=False, default="lb")
    length: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    width: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    height: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    dimension_unit: Mapped[str] = mapped_column(
        String(12), nullable=False, default="in"
    )
    carrier: Mapped[str | None] = mapped_column(String(80), nullable=True)
    service_level: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True, unique=True
    )
    tracking_number: Mapped[str | None] = mapped_column(
        String(160), nullable=True, unique=True
    )
    label_url_or_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[PackageStatus] = mapped_column(
        Enum(
            PackageStatus,
            name="package_status",
            native_enum=False,
            create_constraint=False,
            values_callable=enum_values,
        ),
        nullable=False,
        default=PackageStatus.PACKED,
    )
