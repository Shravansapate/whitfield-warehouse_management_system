"""Production owner-bootstrap and low-stock scheduler safety tests."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import backend.core.apis.api as api_module
from backend.commons.auth import verify_password
from backend.core.config import Settings, get_settings
from backend.core.jobs.low_stock_scheduler import LowStockScheduler
from backend.core.models.access import User, Warehouse
from backend.core.models.enums import UserRole
from backend.seed import ensure_bootstrap_owner, seed

BOOTSTRAP_EMAIL = "first-owner@test.whitfieldwms.com"
BOOTSTRAP_PASSWORD = "First-Owner-Test-Secret-2026!"


def configure_bootstrap_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    email: str = BOOTSTRAP_EMAIL,
    password: str = BOOTSTRAP_PASSWORD,
) -> None:
    """Configure isolated owner-bootstrap settings for one test.

    Args:
        monkeypatch: Pytest environment mutation helper.
        email: Bootstrap owner email.
        password: Bootstrap owner password.
    """
    monkeypatch.setenv("SEED_OWNER_NAME", "First Production Owner")
    monkeypatch.setenv("SEED_OWNER_EMAIL", email)
    monkeypatch.setenv("SEED_OWNER_PASSWORD", password)
    get_settings.cache_clear()


@pytest.mark.asyncio(loop_scope="session")
async def test_bootstrap_owner_creates_then_verifies_exact_replay(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Create one owner and treat only matching credentials as idempotent.

    Args:
        monkeypatch: Pytest environment mutation helper.
        session_factory: Disposable PostgreSQL session factory.
    """
    configure_bootstrap_environment(monkeypatch)
    try:
        assert await seed("test", demo=False, bootstrap_owner=True) is True
        assert await seed("test", demo=False, bootstrap_owner=True) is False

        async with session_factory() as session:
            users = list((await session.scalars(select(User))).all())
            warehouse_count = await session.scalar(select(func.count(Warehouse.id)))
        assert len(users) == 1
        assert users[0].email == BOOTSTRAP_EMAIL
        assert users[0].role == UserRole.OWNER
        assert users[0].is_active is True
        assert verify_password(BOOTSTRAP_PASSWORD, users[0].hashed_password)
        assert warehouse_count == 2
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio(loop_scope="session")
async def test_bootstrap_owner_serializes_concurrent_exact_replays(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Serialize two first-owner attempts into one create and one safe replay.

    Args:
        session_factory: Disposable PostgreSQL session factory.
    """

    async def attempt() -> bool:
        """Execute one isolated bootstrap transaction.

        Returns:
            True for the creator and False for the verified replay.
        """
        async with session_factory() as session, session.begin():
            return await ensure_bootstrap_owner(
                session,
                name="Concurrent First Owner",
                email=BOOTSTRAP_EMAIL,
                password=BOOTSTRAP_PASSWORD,
            )

    results = await asyncio.gather(attempt(), attempt())
    assert sorted(results) == [False, True]

    async with session_factory() as session:
        user_count = await session.scalar(select(func.count(User.id)))
    assert user_count == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_bootstrap_owner_refuses_password_and_identity_conflicts(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reject a stale secret or a different identity after initial bootstrap.

    Args:
        monkeypatch: Pytest environment mutation helper.
        session_factory: Disposable PostgreSQL session factory.
    """
    configure_bootstrap_environment(monkeypatch)
    try:
        assert await seed("test", demo=False, bootstrap_owner=True) is True

        configure_bootstrap_environment(
            monkeypatch,
            password="Different-Owner-Test-Secret-2026!",
        )
        with pytest.raises(RuntimeError, match="conflicts"):
            await seed("test", demo=False, bootstrap_owner=True)

        configure_bootstrap_environment(
            monkeypatch,
            email="other-owner@test.whitfieldwms.com",
        )
        with pytest.raises(RuntimeError, match="before any other user"):
            await seed("test", demo=False, bootstrap_owner=True)

        async with session_factory() as session:
            user_count = await session.scalar(select(func.count(User.id)))
        assert user_count == 1
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio(loop_scope="session")
async def test_bootstrap_owner_refuses_inactive_replay(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reject replay when the matching bootstrap identity is no longer active.

    Args:
        monkeypatch: Pytest environment mutation helper.
        session_factory: Disposable PostgreSQL session factory.
    """
    configure_bootstrap_environment(monkeypatch)
    try:
        assert await seed("test", demo=False, bootstrap_owner=True) is True
        async with session_factory() as session, session.begin():
            owner = (
                await session.execute(select(User).where(User.email == BOOTSTRAP_EMAIL))
            ).scalar_one()
            owner.is_active = False

        with pytest.raises(RuntimeError, match="conflicts"):
            await seed("test", demo=False, bootstrap_owner=True)
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio(loop_scope="session")
async def test_bootstrap_owner_refuses_demo_missing_and_mismatched_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject unsafe flag combinations, missing secrets, and environment mismatch.

    Args:
        monkeypatch: Pytest environment mutation helper.
    """
    configure_bootstrap_environment(monkeypatch)
    try:
        with pytest.raises(RuntimeError, match="cannot be combined"):
            await seed("test", demo=True, bootstrap_owner=True)
        with pytest.raises(RuntimeError, match="match configured"):
            await seed("production", demo=False, bootstrap_owner=True)

        monkeypatch.delenv("SEED_OWNER_EMAIL")
        monkeypatch.delenv("SEED_OWNER_PASSWORD")
        get_settings.cache_clear()
        with pytest.raises(RuntimeError, match="SEED_OWNER_EMAIL"):
            await seed("test", demo=False, bootstrap_owner=True)
    finally:
        get_settings.cache_clear()


def test_low_stock_scheduler_settings_are_disabled_and_bounded_by_default() -> None:
    """Keep the scheduler opt-in and reject unsafe polling intervals."""
    settings = Settings()
    assert settings.low_stock_scheduler_enabled is False
    assert settings.low_stock_scheduler_interval_seconds == 900
    assert settings.low_stock_scheduler_run_immediately is True

    with pytest.raises(ValidationError):
        Settings(low_stock_scheduler_interval_seconds=59)
    with pytest.raises(ValidationError):
        Settings(low_stock_scheduler_interval_seconds=86_401)


@pytest.mark.asyncio(loop_scope="session")
async def test_low_stock_scheduler_contains_failure_and_runs_again() -> None:
    """Continue periodic work after one contained job exception."""
    job_ids: list[str] = []
    second_run_finished = asyncio.Event()

    async def runner(job_id: str) -> dict[str, int]:
        """Fail once, then expose a successful aggregate result.

        Args:
            job_id: Generated scheduler correlation ID.

        Returns:
            One warehouse low-stock count after the first call.

        Raises:
            RuntimeError: On the intentional first invocation.
        """
        job_ids.append(job_id)
        if len(job_ids) == 1:
            raise RuntimeError("intentional scheduler test failure")
        second_run_finished.set()
        return {"RNO": 2}

    scheduler = LowStockScheduler(
        interval_seconds=0.01,
        run_immediately=True,
        runner=runner,
    )
    scheduler.start()
    await asyncio.wait_for(second_run_finished.wait(), timeout=1)
    await scheduler.stop()

    assert len(job_ids) >= 2
    assert len(set(job_ids)) == len(job_ids)
    assert scheduler.is_running is False


@pytest.mark.asyncio(loop_scope="session")
async def test_low_stock_scheduler_can_delay_first_run() -> None:
    """Wait one configured interval when immediate execution is disabled."""
    called = asyncio.Event()

    async def runner(job_id: str) -> dict[str, int]:
        """Record the first delayed scheduler invocation.

        Args:
            job_id: Generated scheduler correlation ID.

        Returns:
            Empty warehouse count mapping.
        """
        del job_id
        called.set()
        return {}

    scheduler = LowStockScheduler(
        interval_seconds=0.02,
        run_immediately=False,
        runner=runner,
    )
    scheduler.start()
    await asyncio.sleep(0)
    assert called.is_set() is False
    await asyncio.wait_for(called.wait(), timeout=1)
    await scheduler.stop()


@pytest.mark.asyncio(loop_scope="session")
async def test_fastapi_lifespan_starts_and_stops_enabled_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wire the configured scheduler through FastAPI startup and shutdown.

    Args:
        monkeypatch: Pytest attribute mutation helper.
    """
    events: list[str] = []
    captured: dict[str, object] = {}

    class FakeScheduler:
        """Minimal scheduler double recording lifecycle calls."""

        def __init__(
            self,
            *,
            interval_seconds: float,
            run_immediately: bool,
        ) -> None:
            """Capture lifecycle configuration.

            Args:
                interval_seconds: Configured scheduler delay.
                run_immediately: Configured initial-run behavior.
            """
            captured["interval_seconds"] = interval_seconds
            captured["run_immediately"] = run_immediately

        def start(self) -> None:
            """Record scheduler startup."""
            events.append("start")

        async def stop(self) -> None:
            """Record scheduler shutdown."""
            events.append("stop")

    monkeypatch.setattr(api_module.settings, "low_stock_scheduler_enabled", True)
    monkeypatch.setattr(
        api_module.settings,
        "low_stock_scheduler_interval_seconds",
        321,
    )
    monkeypatch.setattr(
        api_module.settings,
        "low_stock_scheduler_run_immediately",
        False,
    )
    monkeypatch.setattr(api_module, "LowStockScheduler", FakeScheduler)

    application = FastAPI()
    async with api_module.lifespan(application):
        assert events == ["start"]
        assert application.state.low_stock_scheduler is not None

    assert events == ["start", "stop"]
    assert application.state.low_stock_scheduler is None
    assert captured == {"interval_seconds": 321, "run_immediately": False}
