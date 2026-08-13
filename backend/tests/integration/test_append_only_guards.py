"""Database-level append-only trigger tests for reliability ledgers."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.models.enums import AuditSource, MovementType
from backend.core.models.inventory import InventoryMovement
from backend.core.models.reliability import AuditLog

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _sqlstate(error: DBAPIError) -> str | None:
    """Extract PostgreSQL SQLSTATE from SQLAlchemy's asyncpg wrapper.

    Args:
        error: SQLAlchemy database exception raised by a trigger.

    Returns:
        PostgreSQL SQLSTATE when exposed by the wrapped driver error.
    """
    return getattr(error.orig, "sqlstate", None)


@pytest.mark.parametrize(
    ("table_name", "model_type"),
    [
        ("audit_logs", AuditLog),
        ("inventory_movements", InventoryMovement),
    ],
)
async def test_reliability_ledgers_reject_updates_and_deletes(
    table_name: str,
    model_type,
    seeded_context,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reject UPDATE and DELETE even when SQL bypasses the application layer.

    Args:
        table_name: Trusted append-only database table under test.
        model_type: ORM model used to verify the protected row remains present.
        seeded_context: Seeded users, products, and warehouse state.
        session_factory: Factory for independent PostgreSQL verification sessions.
    """
    record_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        if table_name == "audit_logs":
            session.add(
                AuditLog(
                    id=record_id,
                    actor_user_id=seeded_context.staff_id,
                    warehouse_id=seeded_context.reno_id,
                    table_name="test_records",
                    record_id=uuid.uuid4(),
                    action="TEST_EVENT",
                    request_id="append-only-test",
                    source=AuditSource.SYSTEM,
                )
            )
        else:
            session.add(
                InventoryMovement(
                    id=record_id,
                    warehouse_id=seeded_context.reno_id,
                    product_id=seeded_context.product_a_id,
                    movement_type=MovementType.OPENING_BALANCE,
                    on_hand_delta=10,
                    reserved_delta=0,
                    reference_type="test_setup",
                    reference_id=uuid.uuid4(),
                    actor_user_id=seeded_context.staff_id,
                    source=AuditSource.SYSTEM,
                    on_hand_after=10,
                    reserved_after=0,
                )
            )

    with pytest.raises(DBAPIError) as update_error:
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        f'UPDATE public."{table_name}" SET id = id WHERE id = :record_id'
                    ),
                    {"record_id": record_id},
                )
    assert _sqlstate(update_error.value) == "55000"

    with pytest.raises(DBAPIError) as delete_error:
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(f'DELETE FROM public."{table_name}" WHERE id = :record_id'),
                    {"record_id": record_id},
                )
    assert _sqlstate(delete_error.value) == "55000"

    async with session_factory() as session:
        remaining = await session.scalar(
            select(func.count())
            .select_from(model_type)
            .where(model_type.id == record_id)
        )
        assert remaining == 1
