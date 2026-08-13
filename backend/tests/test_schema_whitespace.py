"""Whitespace-only command validation regressions."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from backend.core.apis.schemas.inventory import InventoryAdjustmentRequest
from backend.core.apis.schemas.orders import (
    CancelOrderRequest,
    LabelRequest,
    OrderCreateRequest,
    OrderItemRequest,
)
from backend.core.apis.schemas.products import ProductCreateRequest
from backend.core.apis.schemas.receiving import (
    DamagedReturnCompleteRequest,
    ReceiptCreateRequest,
)
from backend.core.apis.schemas.users import UserCreateRequest
from backend.core.models.enums import UserRole


@pytest.mark.parametrize(
    "build_request",
    [
        pytest.param(
            lambda: OrderCreateRequest(
                external_reference=" \t ",
                items=[OrderItemRequest(product_id=uuid.uuid4(), quantity=1)],
            ),
            id="order-reference",
        ),
        pytest.param(
            lambda: ReceiptCreateRequest(
                tracking_number=" \n ",
                ticket_number="\t",
                sender_name="Vendor",
                sender_return_address="1 Test Street",
            ),
            id="receipt-reference",
        ),
        pytest.param(
            lambda: CancelOrderRequest(reason=" \t "),
            id="cancellation-reason",
        ),
        pytest.param(
            lambda: InventoryAdjustmentRequest(
                product_id=uuid.uuid4(), quantity_delta=1, reason=" \n "
            ),
            id="adjustment-reason",
        ),
        pytest.param(
            lambda: LabelRequest(carrier=" \t ", service_level="ground"),
            id="carrier-name",
        ),
        pytest.param(
            lambda: DamagedReturnCompleteRequest(return_tracking_number=" \n "),
            id="return-tracking-number",
        ),
        pytest.param(
            lambda: ProductCreateRequest(
                sku=" \t ", upc="000000009999", name="Whitespace Product"
            ),
            id="product-sku",
        ),
        pytest.param(
            lambda: UserCreateRequest(
                name=" \n ",
                email="whitespace-user@example.com",
                password="Strong-Test-Password!",
                role=UserRole.STAFF,
                warehouse_id=uuid.uuid4(),
            ),
            id="user-name",
        ),
    ],
)
def test_whitespace_only_command_fields_are_rejected(
    build_request: Callable[[], object],
) -> None:
    """Reject command strings that become empty after global trimming.

    Args:
        build_request: Constructor exercising one command schema boundary.
    """
    with pytest.raises(ValidationError):
        build_request()
