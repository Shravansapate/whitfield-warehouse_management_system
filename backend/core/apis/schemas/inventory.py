"""Inventory query and mutation API contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from backend.core.apis.schemas.common import APIModel


class InventoryResponse(APIModel):
    """Current balance and threshold for one warehouse product."""

    warehouse_id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    upc: str
    name: str
    on_hand: int
    reserved: int
    available: int
    low_stock_threshold: int
    is_low_stock: bool


class InventoryAdjustmentRequest(APIModel):
    """Reasoned manual inventory delta."""

    warehouse_id: uuid.UUID | None = None
    product_id: uuid.UUID
    quantity_delta: int = Field(ge=-1_000_000, le=1_000_000)
    reason: str = Field(min_length=3, max_length=2000)


class OpeningBalanceRequest(APIModel):
    """Owner-only initial on-hand balance command."""

    warehouse_id: uuid.UUID
    product_id: uuid.UUID
    quantity: int = Field(gt=0, le=1_000_000)
    reason: str = Field(min_length=3, max_length=2000)


class MovementResponse(APIModel):
    """Immutable inventory ledger entry."""

    id: uuid.UUID
    warehouse_id: uuid.UUID
    product_id: uuid.UUID
    movement_type: str
    on_hand_delta: int
    reserved_delta: int
    reference_type: str
    reference_id: uuid.UUID
    actor_user_id: uuid.UUID
    source: str
    reason: str | None
    on_hand_after: int
    reserved_after: int
    created_at: datetime
