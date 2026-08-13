"""Explicit transaction helper for business commands."""

from __future__ import annotations

from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction


class UnitOfWork:
    """Own one outer database transaction for a business workflow."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the transaction wrapper.

        Keeps every balance, movement, audit, and idempotency change together.

        Args:
            session: Request-scoped SQLAlchemy session.
        """
        self.session = session
        self._transaction: AsyncSessionTransaction | None = None

    async def __aenter__(self) -> Self:
        """Begin the unit-of-work transaction.

        Returns:
            Active unit of work.
        """
        self._transaction = await self.session.begin()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        """Commit successful work or roll it back after an error.

        Args:
            exc_type: Raised exception type, if any.
            exc: Raised exception instance, if any.
            traceback: Exception traceback, if any.
        """
        if self._transaction is None:
            return
        if exc_type is None:
            await self._transaction.commit()
        else:
            await self._transaction.rollback()
