"""Async PostgreSQL engine and session factory."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    hide_parameters=True,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=10,
)
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autoflush=False,
    expire_on_commit=False,
)
