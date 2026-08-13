"""Shared HTTP header and request-context dependencies."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Header, Request

from backend.core.models.enums import AuditSource

IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=200),
]
SourceHeader = Annotated[
    Literal[AuditSource.WEB, AuditSource.SCANNER],
    Header(alias="X-Source"),
]


def request_id(request: Request) -> str:
    """Read the middleware-generated correlation identifier.

    Args:
        request: Current HTTP request.

    Returns:
        Stable request identifier.
    """
    return str(request.state.request_id)
