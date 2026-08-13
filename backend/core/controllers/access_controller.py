"""Owner user administration and warehouse-list controller."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.commons.auth import CurrentUser, hash_password, normalize_email
from backend.core import logger
from backend.core.apis.schemas.common import (
    CreatedAtSort,
    CursorPage,
    decode_created_at_cursor,
    validate_created_at_filters,
)
from backend.core.cruds.access_crud import AccessCRUD
from backend.core.cruds.reliability_crud import ReliabilityCRUD
from backend.core.models.access import User, UserWarehouseAssignment, Warehouse
from backend.core.models.enums import AuditSource, UserRole
from backend.core.services.transaction import command_transaction

logging = logger(__name__)


class AccessController:
    """Manage accounts and expose authorized warehouse choices."""

    def __init__(self) -> None:
        """Initialize access and audit persistence collaborators."""
        self.access = AccessCRUD()
        self.reliability = ReliabilityCRUD()

    async def list_warehouses(
        self,
        session: AsyncSession,
        *,
        user: CurrentUser,
        is_active: bool | None,
        cursor: str | None,
        sort: CreatedAtSort,
        limit: int,
    ) -> CursorPage[Warehouse]:
        """List a cursor page of warehouses visible to the actor.

        Non-owners are restricted to their active assigned warehouse.

        Args:
            session: Request-scoped database session.
            user: Authenticated actor.
            is_active: Optional exact active-state filter.
            cursor: Opaque prior-page position.
            sort: Deterministic creation-time order.
            limit: Maximum warehouse count.

        Returns:
            Authorized warehouses and an opaque next cursor.

        Raises:
            HTTPException 422: If the cursor is malformed or incompatible.
        """
        logging.info("Executing AccessController.list_warehouses")
        visible_warehouse_id = (
            user.warehouse_id if user.role != UserRole.OWNER else None
        )
        try:
            decoded_cursor = decode_created_at_cursor(cursor, sort=sort)
        except ValueError as error:
            logging.warning("Rejected invalid warehouse-list pagination cursor")
            raise HTTPException(
                status_code=422,
                detail={"detail": str(error), "code": "INVALID_PAGINATION"},
            ) from error
        return await self.access.list_warehouses_page(
            session,
            visible_warehouse_id=visible_warehouse_id,
            is_active=is_active,
            cursor=decoded_cursor,
            sort=sort,
            limit=limit,
        )

    async def list_users(
        self,
        session: AsyncSession,
        *,
        query: str | None,
        role: UserRole | None,
        is_active: bool | None,
        warehouse_id: uuid.UUID | None,
        created_from: datetime | None,
        created_to: datetime | None,
        cursor: str | None,
        sort: CreatedAtSort,
        limit: int,
    ) -> CursorPage[dict]:
        """List a filtered safe-account cursor page for the owner console.

        Dates and the opaque page position are validated before querying.

        Args:
            session: Request-scoped database session.
            query: Optional name or email search.
            role: Optional exact role filter.
            is_active: Optional exact active-state filter.
            warehouse_id: Optional active assignment filter.
            created_from: Inclusive creation-time lower bound.
            created_to: Inclusive creation-time upper bound.
            cursor: Opaque prior-page position.
            sort: Deterministic creation-time order.
            limit: Maximum account count.

        Returns:
            Safe user dictionaries and an opaque next cursor.

        Raises:
            HTTPException 422: If dates or the cursor are invalid.
        """
        logging.info("Executing AccessController.list_users")
        try:
            validate_created_at_filters(
                created_from=created_from, created_to=created_to
            )
            decoded_cursor = decode_created_at_cursor(cursor, sort=sort)
        except ValueError as error:
            logging.warning("Rejected invalid user-list pagination filters")
            raise HTTPException(
                status_code=422,
                detail={"detail": str(error), "code": "INVALID_PAGINATION"},
            ) from error
        return await self.access.list_users_page(
            session,
            query=query,
            role=role,
            is_active=is_active,
            warehouse_id=warehouse_id,
            created_from=created_from,
            created_to=created_to,
            cursor=decoded_cursor,
            sort=sort,
            limit=limit,
        )

    async def create_user(
        self,
        session: AsyncSession,
        *,
        data: dict,
        actor: CurrentUser,
        request_id: str,
    ) -> dict:
        """Create an account and required warehouse assignment.

        Args:
            session: Request-scoped database session.
            data: Validated account creation data.
            actor: Owner creating the account.
            request_id: Correlation identifier.

        Returns:
            Safe created-user details.
        """
        logging.info("Executing AccessController.create_user")
        async with command_transaction(session):
            email = normalize_email(data["email"])
            if await self.access.get_user_by_email(session, email):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Email is already in use",
                        "code": "EMAIL_EXISTS",
                    },
                )
            warehouse_id = data.get("warehouse_id")
            if warehouse_id is not None:
                warehouse = await session.get(Warehouse, warehouse_id)
                if warehouse is None or not warehouse.is_active:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "detail": "Warehouse not found",
                            "code": "WAREHOUSE_NOT_FOUND",
                        },
                    )
            user = User(
                name=data["name"].strip(),
                email=email,
                hashed_password=hash_password(data["password"]),
                role=data["role"],
                is_active=True,
            )
            session.add(user)
            try:
                await session.flush()
            except IntegrityError as error:
                constraint = getattr(error.orig, "constraint_name", None) or getattr(
                    getattr(error.orig, "__cause__", None), "constraint_name", None
                )
                if constraint == "uq_users_email_lower":
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "detail": "Email is already in use",
                            "code": "EMAIL_EXISTS",
                        },
                    ) from None
                raise
            if warehouse_id is not None:
                await self.access.replace_assignment(
                    session, user_id=user.id, warehouse_id=warehouse_id
                )
            await self.reliability.add_audit(
                session,
                actor_user_id=actor.id,
                warehouse_id=warehouse_id,
                table_name="users",
                record_id=user.id,
                action="USER_CREATED",
                request_id=request_id,
                source=AuditSource.WEB,
                after_value={
                    "email": user.email,
                    "role": user.role.value,
                    "is_active": True,
                },
            )
            warehouse_name = None
            if warehouse_id is not None:
                warehouse_name = (await session.get(Warehouse, warehouse_id)).name  # type: ignore[union-attr]
            response = {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "role": user.role,
                "is_active": user.is_active,
                "warehouse_id": warehouse_id,
                "warehouse_name": warehouse_name,
            }
        return response

    async def update_user(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        data: dict,
        actor: CurrentUser,
        request_id: str,
    ) -> dict:
        """Update safe account fields while preserving audit history.

        Args:
            session: Request-scoped database session.
            user_id: Account being updated.
            data: Explicit fields to change.
            actor: Owner performing the change.
            request_id: Correlation identifier.

        Returns:
            Updated safe account details.
        """
        logging.info("Executing AccessController.update_user")
        async with command_transaction(session):
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": 9_246_031_001},
            )
            user = (
                await session.execute(
                    select(User).where(User.id == user_id).with_for_update()
                )
            ).scalar_one_or_none()
            if user is None:
                raise HTTPException(
                    status_code=404,
                    detail={"detail": "User not found", "code": "USER_NOT_FOUND"},
                )
            before = {
                "name": user.name,
                "role": user.role.value,
                "is_active": user.is_active,
            }
            requested_warehouse_id = data.pop("warehouse_id", None)
            if user.id == actor.id and data.get("is_active") is False:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "You cannot disable your own account",
                        "code": "SELF_DISABLE_FORBIDDEN",
                    },
                )
            target_role = data.get("role", user.role)
            target_active = data.get("is_active", user.is_active)
            removes_active_owner = (
                user.role == UserRole.OWNER
                and user.is_active
                and (target_role != UserRole.OWNER or not target_active)
            )
            if removes_active_owner:
                active_owner_count = (
                    await session.execute(
                        select(func.count(User.id)).where(
                            User.role == UserRole.OWNER,
                            User.is_active.is_(True),
                        )
                    )
                ).scalar_one()
                if user.id == actor.id:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "detail": "You cannot remove your own owner access",
                            "code": "SELF_OWNER_CHANGE_FORBIDDEN",
                        },
                    )
                if active_owner_count <= 1:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "detail": "At least one active owner account is required",
                            "code": "LAST_OWNER_REQUIRED",
                        },
                    )
            current_assignment = await self.access.get_assignment(session, user.id)
            target_warehouse_id = requested_warehouse_id or (
                current_assignment[0] if current_assignment else None
            )
            if target_role == UserRole.OWNER and requested_warehouse_id is not None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Owner accounts are not warehouse-restricted",
                        "code": "OWNER_ASSIGNMENT_FORBIDDEN",
                    },
                )
            if target_role != UserRole.OWNER:
                if target_warehouse_id is None:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "detail": "A warehouse is required for a warehouse-scoped role",
                            "code": "WAREHOUSE_ASSIGNMENT_REQUIRED",
                        },
                    )
                warehouse = await session.get(Warehouse, target_warehouse_id)
                if warehouse is None or not warehouse.is_active:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "detail": "Warehouse not found",
                            "code": "WAREHOUSE_NOT_FOUND",
                        },
                    )
            for field, value in data.items():
                setattr(user, field, value)
            if user.role == UserRole.OWNER:
                await session.execute(
                    update(UserWarehouseAssignment)
                    .where(
                        UserWarehouseAssignment.user_id == user.id,
                        UserWarehouseAssignment.is_active.is_(True),
                    )
                    .values(is_active=False)
                )
            elif (
                current_assignment is None
                or current_assignment[0] != target_warehouse_id
            ):
                await self.access.replace_assignment(
                    session,
                    user_id=user.id,
                    warehouse_id=target_warehouse_id,  # type: ignore[arg-type]
                )
            await session.flush()
            await self.reliability.add_audit(
                session,
                actor_user_id=actor.id,
                warehouse_id=None,
                table_name="users",
                record_id=user.id,
                action="USER_UPDATED",
                request_id=request_id,
                source=AuditSource.WEB,
                before_value=before,
                after_value={
                    "name": user.name,
                    "role": user.role.value,
                    "is_active": user.is_active,
                },
            )
            assignment = await self.access.get_assignment(session, user.id)
            response = {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "role": user.role,
                "is_active": user.is_active,
                "warehouse_id": assignment[0] if assignment else None,
                "warehouse_name": assignment[1] if assignment else None,
            }
        return response

    async def reset_password(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        password: str,
        actor: CurrentUser,
        request_id: str,
    ) -> dict:
        """Replace a password hash and revoke all refresh sessions.

        Args:
            session: Request-scoped database session.
            user_id: Account whose password changes.
            password: New plain-text password accepted at the boundary.
            actor: Owner performing the reset.
            request_id: Correlation identifier.

        Returns:
            Reset acknowledgement.
        """
        logging.info("Executing AccessController.reset_password")
        async with command_transaction(session):
            user = await session.get(User, user_id)
            if user is None:
                raise HTTPException(
                    status_code=404,
                    detail={"detail": "User not found", "code": "USER_NOT_FOUND"},
                )
            user.hashed_password = hash_password(password)
            from backend.core.models.reliability import RefreshSession

            await session.execute(
                update(RefreshSession)
                .where(
                    RefreshSession.user_id == user.id,
                    RefreshSession.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            )
            await self.reliability.add_audit(
                session,
                actor_user_id=actor.id,
                warehouse_id=None,
                table_name="users",
                record_id=user.id,
                action="USER_PASSWORD_RESET",
                request_id=request_id,
                source=AuditSource.WEB,
            )
        return {"detail": "Password reset and active sessions revoked"}

    async def assign_warehouse(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        actor: CurrentUser,
        request_id: str,
    ) -> dict:
        """Replace a non-owner user's active warehouse assignment.

        Args:
            session: Request-scoped database session.
            user_id: Account receiving a new scope.
            warehouse_id: New active warehouse.
            actor: Owner performing the assignment.
            request_id: Correlation identifier.

        Returns:
            Updated safe account details.
        """
        logging.info("Executing AccessController.assign_warehouse")
        async with command_transaction(session):
            user = await session.get(User, user_id)
            warehouse = await session.get(Warehouse, warehouse_id)
            if user is None:
                raise HTTPException(
                    status_code=404,
                    detail={"detail": "User not found", "code": "USER_NOT_FOUND"},
                )
            if warehouse is None or not warehouse.is_active:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "detail": "Warehouse not found",
                        "code": "WAREHOUSE_NOT_FOUND",
                    },
                )
            if user.role == UserRole.OWNER:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "Owner accounts are not warehouse-restricted",
                        "code": "OWNER_ASSIGNMENT_FORBIDDEN",
                    },
                )
            await self.access.replace_assignment(
                session, user_id=user.id, warehouse_id=warehouse.id
            )
            await self.reliability.add_audit(
                session,
                actor_user_id=actor.id,
                warehouse_id=warehouse.id,
                table_name="user_warehouse_assignments",
                record_id=user.id,
                action="USER_WAREHOUSE_ASSIGNED",
                request_id=request_id,
                source=AuditSource.WEB,
                after_value={"warehouse_id": str(warehouse.id)},
            )
            response = {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "role": user.role,
                "is_active": user.is_active,
                "warehouse_id": warehouse.id,
                "warehouse_name": warehouse.name,
            }
        return response
