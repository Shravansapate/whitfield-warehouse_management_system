"""Environment-driven carrier provider selection."""

from backend.core import logger
from backend.core.config import get_settings
from backend.core.services.carriers.base import CarrierProvider
from backend.core.services.carriers.fake_provider import FakeCarrierProvider
from backend.core.services.carriers.unconfigured_provider import (
    UnconfiguredCarrierProvider,
)

logging = logger(__name__)


def get_carrier_provider() -> CarrierProvider:
    """Build the explicitly configured carrier adapter.

    Returns:
        Development fake provider or fail-closed unconfigured provider.
    """
    logging.info("Executing get_carrier_provider")
    provider = get_settings().carrier_provider
    if provider == "fake":
        return FakeCarrierProvider()
    return UnconfiguredCarrierProvider()
