"""Fail-closed carrier provider used until production credentials are connected."""

from __future__ import annotations

from decimal import Decimal
from typing import NoReturn

from fastapi import HTTPException, status

from backend.core import logger
from backend.core.services.carriers.base import (
    AddressValidationResult,
    CarrierProvider,
    LabelResult,
    RateQuote,
    TrackingResult,
    VoidResult,
)

logging = logger(__name__)


class UnconfiguredCarrierProvider(CarrierProvider):
    """Reject label purchases when no production carrier is configured."""

    @staticmethod
    def _unavailable() -> NoReturn:
        """Raise the normalized fail-closed provider response.

        Raises:
            HTTPException 503: Always, because no provider is installed.
        """
        logging.warning("Carrier operation rejected because no provider is configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "detail": "A shipping carrier has not been configured",
                "code": "CARRIER_NOT_CONFIGURED",
            },
        )

    async def validate_address(
        self, *, address: dict[str, str]
    ) -> AddressValidationResult:
        """Reject address validation while no provider is configured.

        Args:
            address: Destination fields that are not transmitted.

        Raises:
            HTTPException 503: Always.
        """
        del address
        self._unavailable()

    async def get_rates(
        self,
        *,
        address: dict[str, str],
        weight: Decimal,
        weight_unit: str,
        length: Decimal,
        width: Decimal,
        height: Decimal,
        dimension_unit: str,
    ) -> list[RateQuote]:
        """Reject rate shopping while no provider is configured.

        Args:
            address: Destination fields that are not transmitted.
            weight: Package weight that is not transmitted.
            weight_unit: Weight unit that is not transmitted.
            length: Package length that is not transmitted.
            width: Package width that is not transmitted.
            height: Package height that is not transmitted.
            dimension_unit: Dimension unit that is not transmitted.

        Raises:
            HTTPException 503: Always.
        """
        del address, weight, weight_unit, length, width, height, dimension_unit
        self._unavailable()

    async def create_label(
        self,
        *,
        idempotency_key: str,
        carrier: str,
        service_level: str,
        weight: Decimal,
        weight_unit: str,
        length: Decimal,
        width: Decimal,
        height: Decimal,
        dimension_unit: str,
    ) -> LabelResult:
        """Return a stable service error without contacting a carrier.

        Args:
            idempotency_key: Provider-side retry identity.
            carrier: Requested carrier name.
            service_level: Requested service tier.
            weight: Positive package weight.
            weight_unit: Weight unit.
            length: Positive package length.
            width: Positive package width.
            height: Positive package height.
            dimension_unit: Dimension unit.

        Raises:
            HTTPException 503: Always, because no production provider is installed.
        """
        del (
            idempotency_key,
            carrier,
            service_level,
            weight,
            weight_unit,
            length,
            width,
            height,
            dimension_unit,
        )
        self._unavailable()

    async def void_label(
        self,
        *,
        provider_request_id: str,
        tracking_number: str,
        idempotency_key: str,
    ) -> VoidResult:
        """Reject label voiding while no provider is configured.

        Args:
            provider_request_id: Provider request reference not transmitted.
            tracking_number: Tracking reference not transmitted.
            idempotency_key: Retry identity not transmitted.

        Raises:
            HTTPException 503: Always.
        """
        del provider_request_id, tracking_number, idempotency_key
        self._unavailable()

    async def track_shipment(self, *, tracking_number: str) -> TrackingResult:
        """Reject tracking while no provider is configured.

        Args:
            tracking_number: Tracking reference not transmitted.

        Raises:
            HTTPException 503: Always.
        """
        del tracking_number
        self._unavailable()
