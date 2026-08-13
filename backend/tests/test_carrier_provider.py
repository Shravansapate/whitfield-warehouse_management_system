"""Provider-neutral carrier contract regressions."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException

from backend.core.services.carriers.fake_provider import FakeCarrierProvider
from backend.core.services.carriers.unconfigured_provider import (
    UnconfiguredCarrierProvider,
)


@pytest.mark.asyncio(loop_scope="session")
async def test_fake_carrier_supports_complete_adapter_contract() -> None:
    """Exercise validation, rates, labels, voids, and tracking without a network."""
    provider = FakeCarrierProvider()
    address = {
        "line1": " 10 Warehouse Way ",
        "city": "Reno",
        "postal_code": "89501",
        "country": "US",
    }
    validated = await provider.validate_address(address=address)
    rates = await provider.get_rates(
        address=validated.normalized_address,
        weight=Decimal("2.5"),
        weight_unit="lb",
        length=Decimal(10),
        width=Decimal(8),
        height=Decimal(4),
        dimension_unit="in",
    )
    label = await provider.create_label(
        idempotency_key="carrier-contract-test",
        carrier=rates[0].carrier,
        service_level=rates[0].service_level,
        weight=Decimal("2.5"),
        weight_unit="lb",
        length=Decimal(10),
        width=Decimal(8),
        height=Decimal(4),
        dimension_unit="in",
    )
    tracking = await provider.track_shipment(tracking_number=label.tracking_number)
    voided = await provider.void_label(
        provider_request_id=label.provider_request_id,
        tracking_number=label.tracking_number,
        idempotency_key="carrier-void-test",
    )

    assert validated.is_valid is True
    assert validated.normalized_address["line1"] == "10 Warehouse Way"
    assert [quote.service_level for quote in rates] == ["ground", "express"]
    assert label.tracking_number.startswith("WF")
    assert tracking.status == "in_transit"
    assert voided.voided is True


@pytest.mark.asyncio(loop_scope="session")
async def test_unconfigured_carrier_fails_closed_with_stable_code() -> None:
    """Reject a production carrier operation with a normalized service code."""
    provider = UnconfiguredCarrierProvider()

    with pytest.raises(HTTPException) as caught:
        await provider.validate_address(
            address={
                "line1": "10 Warehouse Way",
                "city": "Reno",
                "postal_code": "89501",
                "country": "US",
            }
        )

    assert caught.value.status_code == 503
    detail = caught.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "CARRIER_NOT_CONFIGURED"
