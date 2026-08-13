"""Product master and warehouse threshold models."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Product(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Globally unique product master record."""

    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(
        String(80), unique=True, nullable=False, index=True
    )
    upc: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class WarehouseProductSetting(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Warehouse-specific operating settings for a product."""

    __tablename__ = "warehouse_product_settings"
    __table_args__ = (
        UniqueConstraint(
            "warehouse_id", "product_id", name="uq_warehouse_product_setting"
        ),
        CheckConstraint(
            "low_stock_threshold >= 0", name="low_stock_threshold_nonnegative"
        ),
    )

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    low_stock_threshold: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default=text("0")
    )
