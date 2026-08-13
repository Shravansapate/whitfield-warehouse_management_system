"""Product and warehouse-setting API contracts."""

from __future__ import annotations

import uuid

from pydantic import Field

from backend.core.apis.schemas.common import APIModel


class ProductCreateRequest(APIModel):
    """Create a globally unique product master record."""

    sku: str = Field(min_length=1, max_length=80)
    upc: str = Field(min_length=4, max_length=32, pattern=r"^[A-Za-z0-9-]+$")
    name: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class ProductUpdateRequest(APIModel):
    """Update mutable product master fields."""

    sku: str | None = Field(default=None, min_length=1, max_length=80)
    upc: str | None = Field(
        default=None, min_length=4, max_length=32, pattern=r"^[A-Za-z0-9-]+$"
    )
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


class ProductResponse(APIModel):
    """Product master data returned to clients."""

    id: uuid.UUID
    sku: str
    upc: str
    name: str
    description: str | None
    is_active: bool


class ThresholdRequest(APIModel):
    """Warehouse-specific low-stock threshold update."""

    low_stock_threshold: int = Field(ge=0, le=1_000_000)
