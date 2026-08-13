"""Command transaction boundary shared by domain services."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def command_transaction(session: AsyncSession) -> AsyncIterator[None]:
    """Run a business command in one new outer transaction.

    Ends any read-only implicit transaction opened by authentication first.

    Args:
        session: Request-scoped SQLAlchemy session.

    Yields:
        Control while the atomic command transaction is active.
    """
    if session.in_transaction():
        await session.rollback()
    async with session.begin():
        yield
