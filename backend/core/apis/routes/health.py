"""Liveness and PostgreSQL readiness endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import logger
from backend.core.database.session import get_session

router = APIRouter(prefix="/health", tags=["health"])
logging = logger(__name__)


@router.get("/live")
async def live() -> dict:
    """Report that the API process is running.

    Returns:
        Liveness status payload.

    Raises:
        HTTPException 500: If an unexpected check failure occurs.
    """
    try:
        logging.info("Calling GET /api/v1/health/live endpoint")
        return {"status": "ok"}
    except Exception as error:
        logging.error("Unexpected liveness failure error_type=%s", type(error).__name__)
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.get("/ready")
async def ready(session: Annotated[AsyncSession, Depends(get_session)]) -> dict:
    """Verify that PostgreSQL accepts a simple query.

    Args:
        session: Request-scoped database session.

    Returns:
        Readiness status payload.

    Raises:
        HTTPException 503: If PostgreSQL cannot be reached.
    """
    try:
        logging.info("Calling GET /api/v1/health/ready endpoint")
        await session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ready"}
    except Exception as error:
        logging.error("Readiness check failed error_type=%s", type(error).__name__)
        raise HTTPException(
            status_code=503,
            detail={"detail": "Database is not ready", "code": "DATABASE_NOT_READY"},
        ) from error
