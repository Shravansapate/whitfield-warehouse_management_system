"""Deterministic fake carrier used for local development and tests."""

from __future__ import annotations

import hashlib
from decimal import Decimal

from backend.core import logger
from backend.core.config import get_settings
from backend.core.services.carriers.base import (
    AddressValidationResult,
    CarrierProvider,
    LabelResult,
    RateQuote,
    TrackingResult,
    VoidResult,
)

logging = logger(__name__)


class FakeCarrierProvider(CarrierProvider):
    """Create deterministic labels without an external carrier account."""

    async def validate_address(
        self, *, address: dict[str, str]
    ) -> AddressValidationResult:
        """Validate required fake destination fields deterministically.

        Args:
            address: Destination fields supplied by a caller.

        Returns:
            Normalized validation result without a network call.
        """
        logging.info("Executing FakeCarrierProvider.validate_address")
        normalized = {
            key: value.strip() for key, value in address.items() if value.strip()
        }
        missing = tuple(
            f"{field} is required"
            for field in ("line1", "city", "postal_code", "country")
            if not normalized.get(field)
        )
        return AddressValidationResult(
            is_valid=not missing,
            normalized_address=normalized,
            messages=missing,
        )

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
        """Return stable fake ground and express quotes.

        Args:
            address: Validated shipment destination.
            weight: Positive package weight.
            weight_unit: Weight unit.
            length: Positive package length.
            width: Positive package width.
            height: Positive package height.
            dimension_unit: Dimension unit.

        Returns:
            Two deterministic USD quotes.
        """
        logging.info("Executing FakeCarrierProvider.get_rates")
        del address, weight_unit, length, width, height, dimension_unit
        ground = Decimal("6.50") + weight * Decimal("0.75")
        return [
            RateQuote(
                carrier="fake",
                service_level="ground",
                amount=ground.quantize(Decimal("0.01")),
                currency="USD",
                estimated_days=5,
            ),
            RateQuote(
                carrier="fake",
                service_level="express",
                amount=(ground + Decimal("8.00")).quantize(Decimal("0.01")),
                currency="USD",
                estimated_days=2,
            ),
        ]

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
        """Create a stable fake label for one idempotent request.

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

        Returns:
            Deterministic local label metadata.
        """
        logging.info("Executing FakeCarrierProvider.create_label")
        del weight, weight_unit, length, width, height, dimension_unit
        fingerprint = (
            hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:18].upper()
        )
        settings = get_settings()
        return LabelResult(
            provider_request_id=f"fake-{fingerprint.lower()}",
            carrier=carrier,
            service_level=service_level,
            tracking_number=f"WF{fingerprint}",
            label_url_or_key=f"{settings.fake_carrier_base_url}/{fingerprint}.svg",
        )

    async def void_label(
        self,
        *,
        provider_request_id: str,
        tracking_number: str,
        idempotency_key: str,
    ) -> VoidResult:
        """Return a deterministic successful fake label void.

        Args:
            provider_request_id: Stored fake request reference.
            tracking_number: Stored fake tracking number.
            idempotency_key: Provider-side retry identity.

        Returns:
            Successful normalized void result.
        """
        logging.info("Executing FakeCarrierProvider.void_label")
        del idempotency_key
        return VoidResult(
            provider_request_id=provider_request_id,
            tracking_number=tracking_number,
            voided=True,
        )

    async def track_shipment(self, *, tracking_number: str) -> TrackingResult:
        """Return a stable fake in-transit tracking state.

        Args:
            tracking_number: Stored fake tracking number.

        Returns:
            Normalized development tracking result.
        """
        logging.info("Executing FakeCarrierProvider.track_shipment")
        return TrackingResult(
            tracking_number=tracking_number,
            status="in_transit",
            status_detail="Deterministic development shipment; no carrier contacted",
        )
