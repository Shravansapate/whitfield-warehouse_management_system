"""Request identifiers and stable API error handling."""

from __future__ import annotations

import re
import uuid

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from backend.commons.logger import reset_request_id, set_request_id

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a stable request identifier to each request and response."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process a request with request-ID context.

        Preserves a valid caller ID or generates a UUID and returns it in headers.

        Args:
            request: Incoming HTTP request.
            call_next: Next ASGI handler.

        Returns:
            HTTP response with an X-Request-ID header.
        """
        supplied = request.headers.get("X-Request-ID", "").strip()
        request_id = (
            supplied if _SAFE_REQUEST_ID.fullmatch(supplied) else str(uuid.uuid4())
        )
        request.state.request_id = request_id
        token = set_request_id(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            reset_request_id(token)


async def http_exception_handler(
    request: Request, error: HTTPException
) -> JSONResponse:
    """Normalize known HTTP exceptions into the stable error envelope.

    Args:
        request: Failed request.
        error: FastAPI HTTP exception.

    Returns:
        JSON error response with detail, code, and request ID.
    """
    detail = error.detail
    if isinstance(detail, dict):
        message = str(detail.get("detail", "Request failed"))
        code = str(detail.get("code", "REQUEST_FAILED"))
        extra: dict[str, object] = {
            key: value for key, value in detail.items() if key not in {"detail", "code"}
        }
    else:
        message = str(detail)
        code = {
            400: "BAD_REQUEST",
            401: "AUTHENTICATION_REQUIRED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
            422: "VALIDATION_ERROR",
        }.get(error.status_code, "REQUEST_FAILED")
        extra = {}
    return JSONResponse(
        status_code=error.status_code,
        content={
            "detail": message,
            "code": code,
            "request_id": getattr(request.state, "request_id", "unknown"),
            **extra,
        },
        headers=error.headers,
    )


async def unhandled_exception_handler(
    request: Request, error: Exception
) -> JSONResponse:
    """Return a safe error envelope for unexpected failures.

    Does not expose internal exception details to the caller.

    Args:
        request: Failed request.
        error: Unhandled application exception.

    Returns:
        Generic internal-server-error response.
    """
    del error
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal Server Error",
            "code": "INTERNAL_SERVER_ERROR",
            "request_id": getattr(request.state, "request_id", "unknown"),
        },
    )


async def validation_exception_handler(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    """Normalize Pydantic request validation failures.

    Args:
        request: Failed request.
        error: FastAPI validation exception.

    Returns:
        Stable 422 error envelope with field issues.
    """
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder(
            {
                "detail": "Request validation failed",
                "code": "VALIDATION_ERROR",
                "request_id": getattr(request.state, "request_id", "unknown"),
                "errors": error.errors(),
            }
        ),
    )
