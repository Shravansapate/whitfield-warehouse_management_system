"""Central structured logging configuration for the WMS."""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar, Token

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """Inject the active request identifier into each application log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Attach request context and allow the record.

        Args:
            record: Pending Python log record.

        Returns:
            Always True so the record is emitted.
        """
        record.request_id = request_id_context.get()
        return True


def set_request_id(request_id: str) -> Token[str]:
    """Set the current asynchronous request identifier.

    Args:
        request_id: Correlation identifier from middleware.

    Returns:
        Context token used to restore the prior value.
    """
    return request_id_context.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    """Restore the request identifier context after a response.

    Args:
        token: Context token returned by set_request_id.
    """
    request_id_context.reset(token)


def logger(name: str) -> logging.Logger:
    """Return a consistently configured application logger.

    Adds a single stdout handler and prevents duplicate propagation.

    Args:
        name: Python module name for the logger.

    Returns:
        Configured logger instance.
    """
    application_logger = logging.getLogger(name)
    if not application_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.addFilter(RequestIdFilter())
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s"
            )
        )
        application_logger.addHandler(handler)
    application_logger.setLevel(logging.INFO)
    application_logger.propagate = False
    return application_logger
