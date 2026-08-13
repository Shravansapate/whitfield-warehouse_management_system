"""Warehouse and user access persistence operations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import logger
from backend.core.apis.schemas.common import (
    CreatedAtCursor,
    CreatedAtSort,
    CursorPage,
    encode_created_at_cursor,
)
from backend.core.cruds.pagination import apply_created_at_pagination
from backend.core.models.access import User, UserWarehouseAssignment, Warehouse
from backend.core.models.enums import UserRole

logging = logger(__name__)


class AccessCRUD:
    """Persistence wrapper for warehouses, users, and assignments."""

    async def get_user_by_email(self, session: AsyncSession, email: str) -> User | None:
        """Read a user by normalized case-insensitive email.

        Args:
            session: Request-scoped database session.
            email: Normalized email address.

        Returns:
            User model when found, otherwise None.
        """
        logging.info("Executing AccessCRUD.get_user_by_email")
        return (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()

    async def list_warehouses(self, session: AsyncSession) -> list[Warehouse]:
        """List active warehouses in stable name order.

        Args:
            session: Request-scoped database session.

        Returns:
            Active warehouse models.
        """
        logging.info("Executing AccessCRUD.list_warehouses")
        return list(
            (
                await session.scalars(
                    select(Warehouse)
                    .where(Warehouse.is_active.is_(True))
                    .order_by(Warehouse.name)
                )
            ).all()
        )

    async def list_warehouses_page(
        self,
        session: AsyncSession,
        *,
        visible_warehouse_id: uuid.UUID | None,
        is_active: bool | None,
        cursor: CreatedAtCursor | None,
        sort: CreatedAtSort,
        limit: int,
    ) -> CursorPage[Warehouse]:
        """List a scoped warehouse keyset page.

        Creation time and UUID form the stable page boundary.

        Args:
            session: Request-scoped database session.
            visible_warehouse_id: Restrict a non-owner to this warehouse, or None.
            is_active: Optional exact active-state filter.
            cursor: Exclusive prior-page position.
            sort: Deterministic creation-time order.
            limit: Maximum warehouse count.

        Returns:
            Warehouse models and an opaque next cursor.
        """
        logging.info("Executing AccessCRUD.list_warehouses_page")
        statement = select(Warehouse)
        if visible_warehouse_id is not None:
            statement = statement.where(Warehouse.id == visible_warehouse_id)
        if is_active is not None:
            statement = statement.where(Warehouse.is_active.is_(is_active))
        statement = apply_created_at_pagination(
            statement,
            created_at_column=Warehouse.created_at,
            id_column=Warehouse.id,
            created_from=None,
            created_to=None,
            cursor=cursor,
            sort=sort,
        ).limit(limit + 1)
        warehouses = list((await session.scalars(statement)).all())
        has_more = len(warehouses) > limit
        visible_warehouses = warehouses[:limit]
        next_cursor = None
        if has_more:
            last_warehouse = visible_warehouses[-1]
            next_cursor = encode_created_at_cursor(
                created_at=last_warehouse.created_at,
                record_id=last_warehouse.id,
                sort=sort,
            )
        return CursorPage(items=visible_warehouses, next_cursor=next_cursor)

    async def get_assignment(
        self, session: AsyncSession, user_id: uuid.UUID
    ) -> tuple[uuid.UUID, str] | None:
        """Read a user's active warehouse assignment and name.

        Args:
            session: Request-scoped database session.
            user_id: User whose scope is requested.

        Returns:
            Warehouse ID/name pair, or None.
        """
        logging.info("Executing AccessCRUD.get_assignment")
        row = (
            await session.execute(
                select(UserWarehouseAssignment.warehouse_id, Warehouse.name)
                .join(Warehouse, Warehouse.id == UserWarehouseAssignment.warehouse_id)
                .where(
                    UserWarehouseAssignment.user_id == user_id,
                    UserWarehouseAssignment.is_active.is_(True),
                )
            )
        ).one_or_none()
        return (row.warehouse_id, row.name) if row else None

    async def list_users(self, session: AsyncSession) -> list[dict]:
        """List safe user details with active assignments.

        Args:
            session: Request-scoped database session.

        Returns:
            Safe user response dictionaries.
        """
        logging.info("Executing AccessCRUD.list_users")
        rows = (
            (
                await session.execute(
                    select(
                        User.id,
                        User.name,
                        User.email,
                        User.role,
                        User.is_active,
                        UserWarehouseAssignment.warehouse_id,
                        Warehouse.name.label("warehouse_name"),
                    )
                    .outerjoin(
                        UserWarehouseAssignment,
                        (UserWarehouseAssignment.user_id == User.id)
                        & UserWarehouseAssignment.is_active.is_(True),
                    )
                    .outerjoin(
                        Warehouse, Warehouse.id == UserWarehouseAssignment.warehouse_id
                    )
                    .order_by(User.name)
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def list_users_page(
        self,
        session: AsyncSession,
        *,
        query: str | None,
        role: UserRole | None,
        is_active: bool | None,
        warehouse_id: uuid.UUID | None,
        created_from: datetime | None,
        created_to: datetime | None,
        cursor: CreatedAtCursor | None,
        sort: CreatedAtSort,
        limit: int,
    ) -> CursorPage[dict[str, Any]]:
        """List a filtered safe-user keyset page for the owner console.

        Password material is never selected into the response rows.

        Args:
            session: Request-scoped database session.
            query: Optional name or email search.
            role: Optional exact role filter.
            is_active: Optional exact active-state filter.
            warehouse_id: Optional active assignment filter.
            created_from: Inclusive creation-time lower bound.
            created_to: Inclusive creation-time upper bound.
            cursor: Exclusive prior-page position.
            sort: Deterministic creation-time order.
            limit: Maximum account count.

        Returns:
            Safe user dictionaries and an opaque next cursor.
        """
        logging.info("Executing AccessCRUD.list_users_page")
        statement = (
            select(
                User.id,
                User.name,
                User.email,
                User.role,
                User.is_active,
                UserWarehouseAssignment.warehouse_id,
                Warehouse.name.label("warehouse_name"),
                User.created_at.label("_created_at"),
            )
            .outerjoin(
                UserWarehouseAssignment,
                (UserWarehouseAssignment.user_id == User.id)
                & UserWarehouseAssignment.is_active.is_(True),
            )
            .outerjoin(Warehouse, Warehouse.id == UserWarehouseAssignment.warehouse_id)
        )
        normalized = (query or "").strip()
        if normalized:
            like = f"%{normalized}%"
            statement = statement.where(
                or_(User.name.ilike(like), User.email.ilike(like))
            )
        if role is not None:
            statement = statement.where(User.role == role)
        if is_active is not None:
            statement = statement.where(User.is_active.is_(is_active))
        if warehouse_id is not None:
            statement = statement.where(
                UserWarehouseAssignment.warehouse_id == warehouse_id
            )
        statement = apply_created_at_pagination(
            statement,
            created_at_column=User.created_at,
            id_column=User.id,
            created_from=created_from,
            created_to=created_to,
            cursor=cursor,
            sort=sort,
        ).limit(limit + 1)
        rows = (await session.execute(statement)).mappings().all()
        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        next_cursor = None
        if has_more:
            last_row = visible_rows[-1]
            next_cursor = encode_created_at_cursor(
                created_at=last_row["_created_at"],
                record_id=last_row.id,
                sort=sort,
            )
        items: list[dict[str, Any]] = []
        for row in visible_rows:
            item = dict(row)
            item.pop("_created_at")
            items.append(item)
        return CursorPage(items=items, next_cursor=next_cursor)

    async def replace_assignment(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        warehouse_id: uuid.UUID,
    ) -> UserWarehouseAssignment:
        """End any current assignment and create the replacement.

        Args:
            session: Active owner-management transaction.
            user_id: User receiving the scope.
            warehouse_id: New assigned warehouse.

        Returns:
            New active assignment.
        """
        logging.info("Executing AccessCRUD.replace_assignment")
        await session.execute(
            update(UserWarehouseAssignment)
            .where(
                UserWarehouseAssignment.user_id == user_id,
                UserWarehouseAssignment.is_active.is_(True),
            )
            .values(is_active=False, ended_at=datetime.now(UTC))
        )
        assignment = UserWarehouseAssignment(user_id=user_id, warehouse_id=warehouse_id)
        session.add(assignment)
        await session.flush()
        return assignment
