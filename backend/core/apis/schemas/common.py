"""Shared API schema primitives."""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

CursorItem = TypeVar("CursorItem")


class CreatedAtSort(StrEnum):
    """Supported deterministic creation-time sort orders."""

    CREATED_AT_DESC = "created_at_desc"
    CREATED_AT_ASC = "created_at_asc"


class InventorySort(StrEnum):
    """Supported deterministic inventory list sort orders."""

    NAME_ASC = "name_asc"
    NAME_DESC = "name_desc"
    AVAILABLE_ASC = "available_asc"
    AVAILABLE_DESC = "available_desc"


@dataclass(frozen=True, slots=True)
class CreatedAtCursor:
    """Decoded position for stable creation-time keyset pagination."""

    created_at: datetime
    record_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ScalarCursor:
    """Decoded scalar and UUID position for deterministic keyset pagination."""

    value: str | int
    record_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class CursorPage(Generic[CursorItem]):
    """Internal page result that preserves list-shaped HTTP bodies."""

    items: list[CursorItem]
    next_cursor: str | None


def _encode_cursor_payload(payload: dict[str, object]) -> str:
    """Encode a validated cursor payload as an opaque URL-safe token.

    Serialization is deterministic and omits transport padding.

    Args:
        payload: Versioned JSON-compatible cursor fields.

    Returns:
        URL-safe opaque cursor without padding.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _decode_cursor_payload(cursor: str) -> dict[str, object]:
    """Decode the common base64url and JSON cursor envelope safely.

    Only object-shaped JSON envelopes are accepted.

    Args:
        cursor: Caller-provided opaque token.

    Returns:
        Decoded JSON object.

    Raises:
        ValueError: If the cursor is malformed.
    """
    try:
        if not cursor or any(
            character
            not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
            for character in cursor
        ):
            raise ValueError
        padding = "=" * (-len(cursor) % 4)
        raw_payload = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        payload = json.loads(raw_payload.decode("utf-8"))
        if not isinstance(payload, dict):
            raise TypeError
        return payload
    except (
        binascii.Error,
        json.JSONDecodeError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ) as error:
        raise ValueError("Invalid pagination cursor") from error


def encode_created_at_cursor(
    *, created_at: datetime, record_id: uuid.UUID, sort: CreatedAtSort
) -> str:
    """Encode a deterministic page position as an opaque URL-safe token.

    The token binds its position to the selected sort order.

    Args:
        created_at: Creation timestamp of the last visible record.
        record_id: Identifier used to break equal-timestamp ties.
        sort: Creation-time ordering used by the page query.

    Returns:
        URL-safe opaque cursor without padding.

    Raises:
        ValueError: If the timestamp is not timezone-aware.
    """
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("Cursor timestamps must include a timezone offset")
    return _encode_cursor_payload(
        {
            "v": 1,
            "sort": sort.value,
            "created_at": created_at.astimezone(UTC).isoformat(),
            "id": str(record_id),
        }
    )


def decode_created_at_cursor(
    cursor: str | None, *, sort: CreatedAtSort
) -> CreatedAtCursor | None:
    """Decode and validate an opaque creation-time cursor.

    Rejects malformed, unsupported, or differently sorted tokens.

    Args:
        cursor: Caller-provided opaque cursor, if any.
        sort: Sort order required for this query.

    Returns:
        Decoded page position or None for the first page.

    Raises:
        ValueError: If the cursor is malformed or incompatible.
    """
    if cursor is None:
        return None
    try:
        payload = _decode_cursor_payload(cursor)
        if set(payload) != {
            "v",
            "sort",
            "created_at",
            "id",
        }:
            raise ValueError
        if (
            type(payload["v"]) is not int
            or payload["v"] != 1
            or not isinstance(payload["sort"], str)
            or payload["sort"] != sort.value
            or not isinstance(payload["created_at"], str)
            or not isinstance(payload["id"], str)
        ):
            raise ValueError
        created_at = datetime.fromisoformat(payload["created_at"])
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError
        return CreatedAtCursor(
            created_at=created_at.astimezone(UTC),
            record_id=uuid.UUID(payload["id"]),
        )
    except (KeyError, ValueError) as error:
        raise ValueError("Invalid pagination cursor") from error


def encode_scalar_cursor(
    *, value: str | int, record_id: uuid.UUID, sort: StrEnum
) -> str:
    """Encode a scalar and UUID page position for one named sort.

    The token records its scalar type and binds itself to the sort.

    Args:
        value: Normalized string or integer primary sort value.
        record_id: UUID used to break equal-value ties.
        sort: Sort enum member binding the token to query order.

    Returns:
        URL-safe opaque cursor without padding.
    """
    value_type = "integer" if isinstance(value, int) else "string"
    return _encode_cursor_payload(
        {
            "v": 1,
            "sort": sort.value,
            "value_type": value_type,
            "value": value,
            "id": str(record_id),
        }
    )


def decode_scalar_cursor(cursor: str | None, *, sort: StrEnum) -> ScalarCursor | None:
    """Decode a scalar keyset cursor bound to one selected sort.

    Malformed, type-confused, or differently sorted tokens are rejected.

    Args:
        cursor: Caller-provided opaque cursor, if any.
        sort: Sort enum member required for this query.

    Returns:
        Decoded scalar page position or None for a first page.

    Raises:
        ValueError: If the cursor is malformed or incompatible.
    """
    if cursor is None:
        return None
    try:
        payload = _decode_cursor_payload(cursor)
        if set(payload) != {"v", "sort", "value_type", "value", "id"}:
            raise ValueError
        if (
            type(payload["v"]) is not int
            or payload["v"] != 1
            or not isinstance(payload["sort"], str)
            or payload["sort"] != sort.value
            or not isinstance(payload["value_type"], str)
            or payload["value_type"] not in {"string", "integer"}
            or not isinstance(payload["id"], str)
        ):
            raise ValueError
        value = payload["value"]
        if payload["value_type"] == "string":
            if not isinstance(value, str):
                raise ValueError
            decoded_value: str | int = value
        elif type(value) is not int:
            raise ValueError
        else:
            decoded_value = value
        return ScalarCursor(
            value=decoded_value,
            record_id=uuid.UUID(payload["id"]),
        )
    except (KeyError, ValueError) as error:
        raise ValueError("Invalid pagination cursor") from error


def validate_created_at_filters(
    *, created_from: datetime | None, created_to: datetime | None
) -> None:
    """Validate timezone-aware and ordered creation-time filters.

    Both bounds are inclusive when a query later applies them.

    Args:
        created_from: Inclusive lower creation-time bound.
        created_to: Inclusive upper creation-time bound.

    Raises:
        ValueError: If a bound is naive or the range is reversed.
    """
    for value in (created_from, created_to):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Date filters must include a timezone offset")
    if (
        created_from is not None
        and created_to is not None
        and created_from > created_to
    ):
        raise ValueError("created_from must be earlier than or equal to created_to")


class ORMModel(BaseModel):
    """Base response schema supporting SQLAlchemy attribute loading."""

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


class APIModel(BaseModel):
    """Base wire schema that trims surrounding string whitespace."""

    model_config = ConfigDict(str_strip_whitespace=True)


class WarehouseResponse(ORMModel):
    """Warehouse identity exposed to authenticated clients."""

    id: uuid.UUID
    code: str
    name: str
    location: str
    is_active: bool


class MessageResponse(APIModel):
    """Simple successful command acknowledgement."""

    detail: str


class AuditResponse(APIModel):
    """Human-readable append-only audit event."""

    id: uuid.UUID
    actor_user_id: uuid.UUID
    actor_name: str
    warehouse_id: uuid.UUID | None
    table_name: str
    record_id: uuid.UUID
    action: str
    source: str
    reason: str | None
    before_value: dict | None
    after_value: dict | None
    created_at: datetime
