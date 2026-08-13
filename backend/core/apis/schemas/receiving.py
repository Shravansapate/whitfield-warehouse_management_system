"""Inbound receiving and damaged-return API contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, model_validator

from backend.core.apis.schemas.common import APIModel
from backend.core.models.enums import DamagedReturnStatus, ReceiptStatus


class ReceiptCreateRequest(APIModel):
    """Open a draft inbound receipt in one warehouse."""

    warehouse_id: uuid.UUID | None = None
    tracking_number: str | None = Field(default=None, max_length=160)
    ticket_number: str | None = Field(default=None, max_length=160)
    sender_name: str = Field(min_length=2, max_length=200)
    sender_contact: str | None = Field(default=None, max_length=200)
    sender_return_address: str = Field(min_length=4, max_length=2000)

    @model_validator(mode="after")
    def validate_reference(self) -> ReceiptCreateRequest:
        """Require at least one nonblank inbound shipment reference.

        Returns:
            Validated receipt request.

        Raises:
            ValueError: If tracking and ticket references are both absent.
        """
        self.tracking_number = (
            self.tracking_number.strip() if self.tracking_number else None
        )
        self.ticket_number = self.ticket_number.strip() if self.ticket_number else None
        if not self.tracking_number and not self.ticket_number:
            raise ValueError("tracking_number or ticket_number is required")
        return self


class ReceiptItemRequest(APIModel):
    """Add accepted and damaged quantities to a draft receipt."""

    product_id: uuid.UUID
    quantity_received: int = Field(gt=0, le=1_000_000)
    quantity_accepted: int = Field(ge=0, le=1_000_000)
    quantity_damaged: int = Field(ge=0, le=1_000_000)
    damage_notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_quantities(self) -> ReceiptItemRequest:
        """Ensure accepted and damaged units explain the received total.

        Returns:
            Validated receipt-line request.

        Raises:
            ValueError: If quantities do not reconcile or damage lacks notes.
        """
        if self.quantity_accepted + self.quantity_damaged != self.quantity_received:
            raise ValueError("accepted plus damaged must equal received")
        if self.quantity_damaged > 0 and not (self.damage_notes or "").strip():
            raise ValueError(
                "damage_notes are required when quantity_damaged is positive"
            )
        return self


class ReceiptItemUpdateRequest(APIModel):
    """Replace quantities on one draft receipt line."""

    quantity_received: int = Field(gt=0, le=1_000_000)
    quantity_accepted: int = Field(ge=0, le=1_000_000)
    quantity_damaged: int = Field(ge=0, le=1_000_000)
    damage_notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_quantities(self) -> ReceiptItemUpdateRequest:
        """Ensure replacement line quantities are internally consistent.

        Returns:
            Validated receipt-line update.

        Raises:
            ValueError: If quantities do not reconcile or damage lacks notes.
        """
        if self.quantity_accepted + self.quantity_damaged != self.quantity_received:
            raise ValueError("accepted plus damaged must equal received")
        if self.quantity_damaged > 0 and not (self.damage_notes or "").strip():
            raise ValueError(
                "damage_notes are required when quantity_damaged is positive"
            )
        return self


class ReceiptItemResponse(APIModel):
    """Draft receipt line details."""

    id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    upc: str
    product_name: str
    quantity_received: int
    quantity_accepted: int
    quantity_damaged: int
    damage_notes: str | None


class ReceiptResponse(APIModel):
    """Receipt summary and optionally expanded draft lines."""

    id: uuid.UUID
    warehouse_id: uuid.UUID
    tracking_number: str | None
    ticket_number: str | None
    sender_name: str
    sender_contact: str | None
    sender_return_address: str
    status: ReceiptStatus
    accepted: int
    damaged: int
    lines: int
    items: list[ReceiptItemResponse] = Field(default_factory=list)
    created_at: datetime
    received_at: datetime | None


class DamagedReturnCompleteRequest(APIModel):
    """Close a damaged return with carrier evidence."""

    return_tracking_number: str = Field(min_length=2, max_length=160)
    notes: str | None = Field(default=None, max_length=2000)


class ReceiptCancelRequest(APIModel):
    """Mandatory reason for cancelling and retaining a draft receipt."""

    reason: str = Field(min_length=3, max_length=2000)


class DamagedReturnResponse(APIModel):
    """Pending or completed return-to-sender record."""

    id: uuid.UUID
    receipt_id: uuid.UUID
    warehouse_id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    product_name: str
    quantity: int
    status: DamagedReturnStatus
    return_tracking_number: str | None
    returned_at: datetime | None
