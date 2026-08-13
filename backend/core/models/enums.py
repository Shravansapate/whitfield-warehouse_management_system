"""Persisted domain enumerations."""

from enum import StrEnum


def enum_values(enum_class: type[StrEnum]) -> list[str]:
    """Return stable string values for SQLAlchemy enum persistence.

    Args:
        enum_class: String-enum class used by a mapped column.

    Returns:
        Ordered persisted enum values.
    """
    return [member.value for member in enum_class]


class UserRole(StrEnum):
    """Application authorization roles."""

    OWNER = "owner"
    MANAGER = "manager"
    TRUSTED = "trusted"
    STAFF = "staff"


class ReceiptStatus(StrEnum):
    """Inbound receipt lifecycle states."""

    OPEN = "open"
    RECEIVING = "receiving"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class DamagedReturnStatus(StrEnum):
    """Damaged stock return states."""

    PENDING_RETURN = "pending_return"
    RETURNED_TO_SENDER = "returned_to_sender"
    CANCELLED = "cancelled"


class MovementType(StrEnum):
    """Immutable inventory movement kinds."""

    OPENING_BALANCE = "OPENING_BALANCE"
    RECEIPT = "RECEIPT"
    RESERVE = "RESERVE"
    RELEASE = "RELEASE"
    SHIP = "SHIP"
    ADJUST = "ADJUST"
    TRANSFER_OUT = "TRANSFER_OUT"
    TRANSFER_IN = "TRANSFER_IN"


class AdjustmentStatus(StrEnum):
    """Inventory adjustment processing states."""

    APPLIED = "applied"


class OrderStatus(StrEnum):
    """Outbound order lifecycle states."""

    PENDING = "pending"
    ALLOCATED = "allocated"
    PICKING = "picking"
    PACKED = "packed"
    LABEL_CREATED = "label_created"
    SHIPPED = "shipped"
    CANNOT_FULFILL = "cannot_fulfill"
    CANCELLED = "cancelled"


class ReservationStatus(StrEnum):
    """Inventory reservation lifecycle states."""

    ACTIVE = "active"
    RELEASED = "released"
    CONSUMED = "consumed"


class PackageStatus(StrEnum):
    """Outbound package lifecycle states."""

    PACKED = "packed"
    LABEL_CREATED = "label_created"
    SHIPPED = "shipped"


class AuditSource(StrEnum):
    """Origin channels recorded in audit history."""

    WEB = "web"
    SCANNER = "scanner"
    VOICE = "voice"
    AUTOMATION = "automation"
    API = "api"
    SYSTEM = "system"
