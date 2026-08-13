"""Inbound receipt and damaged-return models."""

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
from backend.core.models.enums import DamagedReturnStatus, ReceiptStatus, enum_values


class InboundReceipt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Draft inbound shipment that posts stock only when finalized."""

    __tablename__ = "inbound_receipts"
    __table_args__ = (
        CheckConstraint(
            "nullif(btrim(tracking_number), '') IS NOT NULL OR nullif(btrim(ticket_number), '') IS NOT NULL",
            name="tracking_or_ticket_required",
        ),
        CheckConstraint(
            "status IN ('open', 'receiving', 'received', 'cancelled')",
            name="receipt_status",
        ),
        Index(
            "ix_receipts_warehouse_status_created",
            "warehouse_id",
            "status",
            "created_at",
        ),
        Index(
            "uq_receipt_tracking_number",
            text("lower(tracking_number)"),
            unique=True,
            postgresql_where=text("tracking_number IS NOT NULL"),
        ),
        Index(
            "uq_receipt_warehouse_ticket",
            "warehouse_id",
            text("lower(ticket_number)"),
            unique=True,
            postgresql_where=text("ticket_number IS NOT NULL"),
        ),
    )

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    tracking_number: Mapped[str | None] = mapped_column(
        String(160), nullable=True, index=True
    )
    ticket_number: Mapped[str | None] = mapped_column(
        String(160), nullable=True, index=True
    )
    sender_name: Mapped[str] = mapped_column(String(200), nullable=False)
    sender_contact: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sender_return_address: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ReceiptStatus] = mapped_column(
        Enum(
            ReceiptStatus,
            name="receipt_status",
            native_enum=False,
            create_constraint=False,
            values_callable=enum_values,
        ),
        nullable=False,
        default=ReceiptStatus.OPEN,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    received_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class InboundReceiptItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Draft accepted and damaged quantities for one receipt product."""

    __tablename__ = "inbound_receipt_items"
    __table_args__ = (
        UniqueConstraint("receipt_id", "product_id", name="uq_receipt_product"),
        CheckConstraint("quantity_received > 0", name="quantity_received_positive"),
        CheckConstraint("quantity_accepted >= 0", name="quantity_accepted_nonnegative"),
        CheckConstraint("quantity_damaged >= 0", name="quantity_damaged_nonnegative"),
        CheckConstraint(
            "quantity_accepted + quantity_damaged = quantity_received",
            name="accepted_damaged_equal_received",
        ),
    )

    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inbound_receipts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity_received: Mapped[int] = mapped_column(nullable=False)
    quantity_accepted: Mapped[int] = mapped_column(nullable=False)
    quantity_damaged: Mapped[int] = mapped_column(nullable=False)
    damage_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class DamagedReturn(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Return-to-sender record for damaged inbound units."""

    __tablename__ = "damaged_returns"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="damaged_return_quantity_positive"),
        CheckConstraint(
            "status IN ('pending_return', 'returned_to_sender', 'cancelled')",
            name="damaged_return_status",
        ),
        Index("ix_damaged_returns_warehouse_status", "warehouse_id", "status"),
    )

    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inbound_receipts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    receipt_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inbound_receipt_items.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[DamagedReturnStatus] = mapped_column(
        Enum(
            DamagedReturnStatus,
            name="damaged_return_status",
            native_enum=False,
            create_constraint=False,
            values_callable=enum_values,
        ),
        nullable=False,
        default=DamagedReturnStatus.PENDING_RETURN,
    )
    return_tracking_number: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    returned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    handled_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
