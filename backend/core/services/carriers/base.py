"""Provider-neutral carrier adapter contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class LabelResult:
    """Normalized carrier label result."""

    provider_request_id: str
    carrier: str
    service_level: str
    tracking_number: str
    label_url_or_key: str


@dataclass(frozen=True, slots=True)
class AddressValidationResult:
    """Normalized destination validation result."""

    is_valid: bool
    normalized_address: dict[str, str]
    messages: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RateQuote:
    """Normalized carrier service quote."""

    carrier: str
    service_level: str
    amount: Decimal
    currency: str
    estimated_days: int | None


@dataclass(frozen=True, slots=True)
class VoidResult:
    """Normalized label-void result."""

    provider_request_id: str
    tracking_number: str
    voided: bool


@dataclass(frozen=True, slots=True)
class TrackingResult:
    """Normalized shipment tracking result."""

    tracking_number: str
    status: str
    status_detail: str


class CarrierProvider(ABC):
    """Contract implemented by fake and real carrier providers."""

    @abstractmethod
    async def validate_address(
        self, *, address: dict[str, str]
    ) -> AddressValidationResult:
        """Validate and normalize a shipment destination.

        Args:
            address: Destination fields supplied by the shipping workflow.

        Returns:
            Provider-neutral validation result.
        """
        raise NotImplementedError

    @abstractmethod
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
        """Return normalized service quotes for a measured package.

        Args:
            address: Validated shipment destination.
            weight: Positive package weight.
            weight_unit: Weight unit.
            length: Positive package length.
            width: Positive package width.
            height: Positive package height.
            dimension_unit: Dimension unit.

        Returns:
            Available carrier service quotes.
        """
        raise NotImplementedError

    @abstractmethod
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
        """Create a shipment label from measured package data.

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
            Normalized label result.
        """
        raise NotImplementedError

    @abstractmethod
    async def void_label(
        self,
        *,
        provider_request_id: str,
        tracking_number: str,
        idempotency_key: str,
    ) -> VoidResult:
        """Void an unused provider label idempotently.

        Args:
            provider_request_id: Stored provider request reference.
            tracking_number: Stored shipment tracking number.
            idempotency_key: Provider-side retry identity.

        Returns:
            Normalized void result.
        """
        raise NotImplementedError

    @abstractmethod
    async def track_shipment(self, *, tracking_number: str) -> TrackingResult:
        """Read the current provider shipment status.

        Args:
            tracking_number: Shipment tracking number.

        Returns:
            Normalized tracking result.
        """
        raise NotImplementedError
