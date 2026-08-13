"""Per-request SQLAlchemy session dependency."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database.engine import async_session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield one independent async session for an HTTP request.

    Rolls back uncommitted work when a request exits with an error.

    Yields:
        Request-scoped async SQLAlchemy session.
    """
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
