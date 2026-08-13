"""Shared PostgreSQL integration fixtures for the Whitfield WMS.

The suite refuses non-test databases, rebuilds the schema through Alembic,
and gives every test deterministic users, warehouses, products, and balances.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://postgres@127.0.0.1:55432/whitfield_wms_test"
)
TEST_DATABASE_URL = (
    os.environ.get("TEST_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or DEFAULT_TEST_DATABASE_URL
)


def _validated_test_database_url(candidate: str) -> str:
    """Validate an async PostgreSQL URL reserved for destructive tests.

    The database name must contain ``test`` as a distinct underscore or dash
    segment so a developer or production database cannot be rebuilt by mistake.

    Args:
        candidate: SQLAlchemy database URL selected from the environment.

    Returns:
        The validated URL unchanged.

    Raises:
        RuntimeError: If the URL is not async PostgreSQL or is not test-marked.
    """
    try:
        parsed = make_url(candidate)
    except Exception as error:
        raise RuntimeError(
            "TEST_DATABASE_URL/DATABASE_URL is not a valid URL"
        ) from error

    if parsed.drivername != "postgresql+asyncpg":
        raise RuntimeError("Backend integration tests require postgresql+asyncpg")
    database_name = (parsed.database or "").casefold()
    if re.search(r"(?:^|[_-])test(?:$|[_-])", database_name) is None:
        raise RuntimeError(
            "Refusing destructive tests because the database name is not test-marked"
        )
    return candidate


TEST_DATABASE_URL = _validated_test_database_url(TEST_DATABASE_URL)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["ENVIRONMENT"] = "test"
os.environ.setdefault(
    "JWT_SECRET_KEY", "whitfield-wms-pytest-secret-key-only-do-not-deploy"
)

# Application imports must happen only after DATABASE_URL has been validated and set.
from backend.commons.auth import create_access_token, hash_password
from backend.core.apis.api import app
from backend.core.database.engine import async_session_factory, engine
from backend.core.models import Base
from backend.core.models.access import (
    User,
    UserWarehouseAssignment,
    Warehouse,
)
from backend.core.models.enums import UserRole
from backend.core.models.inventory import InventoryBalance
from backend.core.models.product import Product, WarehouseProductSetting

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = WORKSPACE_ROOT / "backend" / "alembic.ini"
TEST_PASSWORD = "Wms-Test-Password-2026!"
TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)


@dataclass(frozen=True, slots=True)
class SeededContext:
    """Stable identifiers and credentials for one isolated integration test."""

    reno_id: uuid.UUID
    columbus_id: uuid.UUID
    product_a_id: uuid.UUID
    product_b_id: uuid.UUID
    product_c_id: uuid.UUID
    owner_id: uuid.UUID
    manager_id: uuid.UUID
    staff_id: uuid.UUID
    disabled_id: uuid.UUID
    owner_token: str
    manager_token: str
    staff_token: str
    disabled_token: str

    def headers(
        self,
        actor: str = "staff",
        *,
        idempotency_key: str | None = None,
        source: str = "web",
    ) -> dict[str, str]:
        """Build authenticated command headers for a seeded actor.

        Args:
            actor: Seeded actor name whose bearer token should be used.
            idempotency_key: Optional mutation retry key.
            source: Audit source header value.

        Returns:
            HTTP headers accepted by the WMS API.

        Raises:
            KeyError: If the requested actor is not part of the seed context.
        """
        tokens = {
            "owner": self.owner_token,
            "manager": self.manager_token,
            "staff": self.staff_token,
            "disabled": self.disabled_token,
        }
        headers = {
            "Authorization": f"Bearer {tokens[actor]}",
            "X-Source": source,
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return headers


def _run_alembic(target: str) -> None:
    """Move the disposable schema to an explicit Alembic target.

    Args:
        target: Alembic revision name such as ``base`` or ``head``.
    """
    configuration = Config(str(ALEMBIC_CONFIG_PATH))
    configuration.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    if target == "base":
        command.downgrade(configuration, target)
    else:
        command.upgrade(configuration, target)


async def _truncate_application_tables() -> None:
    """Remove all domain rows while preserving Alembic schema state.

    Uses PostgreSQL ``TRUNCATE ... CASCADE`` against the trusted ORM metadata
    table list, avoiding trigger-blocked DELETE operations on immutable ledgers.
    """
    table_names = sorted(Base.metadata.tables)
    quoted_tables = ", ".join(f'public."{name}"' for name in table_names)
    async with engine.begin() as connection:
        await connection.execute(
            text(f"TRUNCATE TABLE {quoted_tables} RESTART IDENTITY CASCADE")
        )


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def migrated_database() -> AsyncIterator[None]:
    """Rebuild and finally clean the disposable schema through Alembic.

    Yields:
        Control after every migration has reached ``head``.
    """
    await engine.dispose()
    await asyncio.to_thread(_run_alembic, "base")
    await asyncio.to_thread(_run_alembic, "head")
    try:
        yield
    finally:
        await engine.dispose()
        await asyncio.to_thread(_run_alembic, "base")
        await asyncio.to_thread(_run_alembic, "head")


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def reset_database(migrated_database: None) -> AsyncIterator[None]:
    """Reset all application tables before and after one test.

    Args:
        migrated_database: Session guard ensuring the schema is migrated.

    Yields:
        Control with an empty, fully migrated database.
    """
    del migrated_database
    await _truncate_application_tables()
    try:
        yield
    finally:
        await _truncate_application_tables()


@pytest.fixture
def session_factory(
    reset_database: None,
) -> async_sessionmaker[AsyncSession]:
    """Expose the application session factory after database isolation.

    Args:
        reset_database: Per-test database reset dependency.

    Returns:
        Async session factory bound to the disposable PostgreSQL database.
    """
    del reset_database
    return async_session_factory


@pytest_asyncio.fixture(loop_scope="session")
async def api_client(reset_database: None) -> AsyncIterator[AsyncClient]:
    """Create an HTTPX client that executes the real FastAPI application.

    Args:
        reset_database: Per-test database reset dependency.

    Yields:
        Async client configured not to hide application 500 responses.
    """
    del reset_database
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest_asyncio.fixture(loop_scope="session")
async def seeded_context(reset_database: None) -> SeededContext:
    """Seed deterministic access, catalog, and warehouse inventory state.

    Args:
        reset_database: Per-test database reset dependency.

    Returns:
        Identifiers and bearer tokens for use by one integration test.
    """
    del reset_database
    reno = Warehouse(code="RNO", name="Reno", location="Reno, NV")
    columbus = Warehouse(code="CMH", name="Columbus", location="Columbus, OH")
    owner = User(
        name="Test Owner",
        email="owner@test.whitfieldwms.com",
        hashed_password=TEST_PASSWORD_HASH,
        role=UserRole.OWNER,
        is_active=True,
    )
    manager = User(
        name="Reno Manager",
        email="manager@test.whitfieldwms.com",
        hashed_password=TEST_PASSWORD_HASH,
        role=UserRole.MANAGER,
        is_active=True,
    )
    staff = User(
        name="Reno Staff",
        email="staff@test.whitfieldwms.com",
        hashed_password=TEST_PASSWORD_HASH,
        role=UserRole.STAFF,
        is_active=True,
    )
    disabled = User(
        name="Disabled Staff",
        email="disabled@test.whitfieldwms.com",
        hashed_password=TEST_PASSWORD_HASH,
        role=UserRole.STAFF,
        is_active=False,
    )
    product_a = Product(
        sku="TEST-A",
        upc="000000000001",
        name="Test Product A",
        is_active=True,
    )
    product_b = Product(
        sku="TEST-B",
        upc="000000000002",
        name="Test Product B",
        is_active=True,
    )
    product_c = Product(
        sku="TEST-C",
        upc="000000000003",
        name="Test Product C",
        is_active=True,
    )

    async with async_session_factory() as session:
        async with session.begin():
            session.add_all(
                [
                    reno,
                    columbus,
                    owner,
                    manager,
                    staff,
                    disabled,
                    product_a,
                    product_b,
                    product_c,
                ]
            )
            await session.flush()
            session.add_all(
                [
                    UserWarehouseAssignment(
                        user_id=manager.id, warehouse_id=reno.id, is_active=True
                    ),
                    UserWarehouseAssignment(
                        user_id=staff.id, warehouse_id=reno.id, is_active=True
                    ),
                    UserWarehouseAssignment(
                        user_id=disabled.id, warehouse_id=reno.id, is_active=True
                    ),
                    InventoryBalance(
                        warehouse_id=reno.id,
                        product_id=product_a.id,
                        on_hand=10,
                        reserved=0,
                    ),
                    InventoryBalance(
                        warehouse_id=reno.id,
                        product_id=product_b.id,
                        on_hand=3,
                        reserved=0,
                    ),
                    InventoryBalance(
                        warehouse_id=columbus.id,
                        product_id=product_a.id,
                        on_hand=40,
                        reserved=0,
                    ),
                    InventoryBalance(
                        warehouse_id=columbus.id,
                        product_id=product_b.id,
                        on_hand=25,
                        reserved=0,
                    ),
                    WarehouseProductSetting(
                        warehouse_id=reno.id,
                        product_id=product_a.id,
                        low_stock_threshold=2,
                    ),
                    WarehouseProductSetting(
                        warehouse_id=reno.id,
                        product_id=product_b.id,
                        low_stock_threshold=2,
                    ),
                ]
            )
        owner_token = create_access_token(owner)[0]
        manager_token = create_access_token(manager)[0]
        staff_token = create_access_token(staff)[0]
        disabled_token = create_access_token(disabled)[0]

    return SeededContext(
        reno_id=reno.id,
        columbus_id=columbus.id,
        product_a_id=product_a.id,
        product_b_id=product_b.id,
        product_c_id=product_c.id,
        owner_id=owner.id,
        manager_id=manager.id,
        staff_id=staff.id,
        disabled_id=disabled.id,
        owner_token=owner_token,
        manager_token=manager_token,
        staff_token=staff_token,
        disabled_token=disabled_token,
    )
