"""Outbound order and package API contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import Field

from backend.core.apis.schemas.common import APIModel
from backend.core.models.enums import OrderStatus


class OrderItemRequest(APIModel):
    """Requested product quantity for an outbound order."""

    product_id: uuid.UUID
    quantity: int = Field(gt=0, le=1_000_000)


class OrderCreateRequest(APIModel):
    """Create and atomically allocate a multi-product order."""

    external_reference: str = Field(min_length=1, max_length=160)
    warehouse_id: uuid.UUID | None = None
    items: list[OrderItemRequest] = Field(min_length=1, max_length=500)


class PackageRequest(APIModel):
    """Positive package measurements required before label creation."""

    weight: Decimal = Field(gt=0, max_digits=10, decimal_places=3)
    weight_unit: str = Field(default="lb", pattern=r"^(lb|kg)$")
    length: Decimal = Field(gt=0, max_digits=10, decimal_places=3)
    width: Decimal = Field(gt=0, max_digits=10, decimal_places=3)
    height: Decimal = Field(gt=0, max_digits=10, decimal_places=3)
    dimension_unit: str = Field(default="in", pattern=r"^(in|cm)$")


class PickItemRequest(APIModel):
    """Confirmed physical count for one order line."""

    picked_quantity: int = Field(ge=0, le=1_000_000)


class LabelRequest(APIModel):
    """Carrier and service choice for label creation."""

    carrier: str = Field(default="FakeCarrier", min_length=2, max_length=80)
    service_level: str = Field(default="ground", min_length=2, max_length=80)


class CancelOrderRequest(APIModel):
    """Mandatory reason for retaining and cancelling an order."""

    reason: str = Field(min_length=3, max_length=2000)


class OrderItemResponse(APIModel):
    """Consolidated outbound order line."""

    id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    product_name: str
    quantity: int
    picked_quantity: int


class PackageResponse(APIModel):
    """Measured package and carrier label state."""

    id: uuid.UUID
    weight: Decimal
    weight_unit: str
    length: Decimal
    width: Decimal
    height: Decimal
    dimension_unit: str
    carrier: str | None
    service_level: str | None
    tracking_number: str | None
    label_url_or_key: str | None
    status: str


class OrderResponse(APIModel):
    """Order status, lines, and package summary."""

    id: uuid.UUID
    external_reference: str
    warehouse_id: uuid.UUID
    status: OrderStatus
    items: list[OrderItemResponse]
    package: PackageResponse | None = None
    shortages: list[dict] = Field(default_factory=list)
    cancel_reason: str | None = None
    created_at: datetime


class DashboardSummaryResponse(APIModel):
    """Role-scoped operational counts for one warehouse."""

    warehouse: dict
    metrics: dict[str, int]
