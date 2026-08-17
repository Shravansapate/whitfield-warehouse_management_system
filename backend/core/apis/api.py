"""FastAPI application aggregation and process lifecycle wiring."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from backend.commons.request_context import (
    RequestContextMiddleware,
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from backend.core import logger
from backend.core.apis.routes import (
    auth,
    dev_labels,
    health,
    inventory,
    orders,
    products,
    read,
    receiving,
    users,
    voice,
)
from backend.core.config import get_settings
from backend.core.database.engine import engine
from backend.core.jobs import LowStockScheduler

settings = get_settings()
logging = logger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Manage process-level scheduler and database resources.

    Starts the optional read-only job loop and always cancels it before closing
    the SQLAlchemy engine. Startup never creates or migrates database tables.

    Args:
        application: FastAPI application instance.

    Yields:
        Control while the application serves requests.
    """
    scheduler: LowStockScheduler | None = None
    application.state.low_stock_scheduler = None
    if settings.low_stock_scheduler_enabled:
        scheduler = LowStockScheduler(
            interval_seconds=settings.low_stock_scheduler_interval_seconds,
            run_immediately=settings.low_stock_scheduler_run_immediately,
        )
        application.state.low_stock_scheduler = scheduler
        scheduler.start()
    else:
        logging.info("Low-stock scheduler disabled")
    try:
        yield
    finally:
        if scheduler is not None:
            await scheduler.stop()
        application.state.low_stock_scheduler = None
        await engine.dispose()


def create_app() -> FastAPI:
    """Create and configure the Whitfield WMS ASGI application.

    Registers middleware, stable exceptions, and every versioned router.

    Returns:
        Configured FastAPI application.
    """
    application = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url="/redoc" if settings.environment != "production" else None,
    )
    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_origin_regex=r"https://.*\.vercel\.app",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Request-ID",
            "X-Source",
        ],
        expose_headers=["X-Next-Cursor", "X-Request-ID"],
    )
    application.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    application.add_exception_handler(
        RequestValidationError,
        validation_exception_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(Exception, unhandled_exception_handler)

    for route_module in (
        health,
        auth,
        users,
        products,
        receiving,
        inventory,
        orders,
        read,
        voice,
    ):
        application.include_router(route_module.router, prefix=settings.api_prefix)
    application.include_router(dev_labels.router)
    return application


app = create_app()
