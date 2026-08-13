"""Persistence helpers for idempotency and append-only audit history."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import logger
from backend.core.apis.schemas.common import (
    CreatedAtCursor,
    CreatedAtSort,
    CursorPage,
    encode_created_at_cursor,
)
from backend.core.cruds.pagination import apply_created_at_pagination
from backend.core.models.access import User
from backend.core.models.enums import AuditSource
from backend.core.models.reliability import AuditLog, IdempotencyRecord

logging = logger(__name__)


class ReliabilityCRUD:
    """Atomic idempotency and audit persistence operations."""

    @staticmethod
    def request_hash(payload: Any) -> str:
        """Create a stable SHA-256 hash for a command payload.

        Args:
            payload: JSON-compatible command payload.

        Returns:
            Lower-case hexadecimal digest.
        """
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def acquire_idempotency(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        operation: str,
        key: str,
        payload: Any,
    ) -> tuple[IdempotencyRecord, bool]:
        """Acquire a command key atomically or return its completed record.

        Same key with different content is rejected as a conflict.

        Args:
            session: Active business transaction session.
            user_id: Actor owning the idempotency namespace.
            operation: Stable command operation name.
            key: Client-provided idempotency key.
            payload: Canonical command input.

        Returns:
            Idempotency record and whether this transaction inserted it.

        Raises:
            HTTPException 409: If the key was reused with different content.
        """
        logging.info("Executing ReliabilityCRUD.acquire_idempotency")
        digest = self.request_hash(payload)
        record_id = uuid.uuid4()
        statement = (
            insert(IdempotencyRecord)
            .values(
                id=record_id,
                user_id=user_id,
                operation=operation,
                idempotency_key=key,
                request_hash=digest,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
            .on_conflict_do_nothing(
                index_elements=["user_id", "operation", "idempotency_key"]
            )
            .returning(IdempotencyRecord.id)
        )
        inserted_id = (await session.execute(statement)).scalar_one_or_none()
        if inserted_id is not None:
            record = await session.get(IdempotencyRecord, inserted_id)
            if record is None:
                raise RuntimeError("Inserted idempotency record could not be loaded")
            return record, True

        record = (
            await session.execute(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.user_id == user_id,
                    IdempotencyRecord.operation == operation,
                    IdempotencyRecord.idempotency_key == key,
                )
            )
        ).scalar_one()
        if record.request_hash != digest:
            logging.warning("Idempotency key reused with a different request hash")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "detail": "Idempotency key was already used for a different request",
                    "code": "IDEMPOTENCY_MISMATCH",
                },
            )
        if record.response_body is None or record.response_status is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "detail": "Command is still being processed",
                    "code": "COMMAND_IN_PROGRESS",
                },
            )
        return record, False

    async def complete_idempotency(
        self,
        session: AsyncSession,
        *,
        record: IdempotencyRecord,
        response_status: int,
        response_body: dict | list,
        resource_type: str,
        resource_id: uuid.UUID,
    ) -> None:
        """Store a command result inside the same business transaction.

        Args:
            session: Active business transaction session.
            record: Newly acquired idempotency record.
            response_status: HTTP status replayed for retries.
            response_body: JSON response replayed for retries.
            resource_type: Mutated business resource type.
            resource_id: Mutated business resource ID.
        """
        logging.info("Executing ReliabilityCRUD.complete_idempotency")
        record.response_status = response_status
        record.response_body = response_body
        record.resource_type = resource_type
        record.resource_id = resource_id
        await session.flush()

    async def add_audit(
        self,
        session: AsyncSession,
        *,
        actor_user_id: uuid.UUID,
        warehouse_id: uuid.UUID | None,
        table_name: str,
        record_id: uuid.UUID,
        action: str,
        request_id: str,
        source: AuditSource,
        before_value: dict | None = None,
        after_value: dict | None = None,
        reason: str | None = None,
    ) -> AuditLog:
        """Append an audit record to the active transaction.

        Args:
            session: Active business transaction session.
            actor_user_id: Actor responsible for the change.
            warehouse_id: Warehouse scope, if applicable.
            table_name: Changed business table.
            record_id: Changed record ID.
            action: Stable domain action name.
            request_id: Correlation identifier.
            source: Originating client channel.
            before_value: Safe pre-change snapshot.
            after_value: Safe post-change snapshot.
            reason: Human-provided explanation, if any.

        Returns:
            Pending append-only audit model.
        """
        logging.info("Executing ReliabilityCRUD.add_audit")
        audit = AuditLog(
            actor_user_id=actor_user_id,
            warehouse_id=warehouse_id,
            table_name=table_name,
            record_id=record_id,
            action=action,
            before_value=before_value,
            after_value=after_value,
            request_id=request_id,
            source=source,
            reason=reason,
        )
        session.add(audit)
        await session.flush()
        return audit

    async def list_audit_logs(
        self,
        session: AsyncSession,
        *,
        warehouse_id: uuid.UUID | None,
        table_name: str | None,
        record_id: uuid.UUID | None,
        action: str | None,
        source: AuditSource | None,
        created_from: datetime | None,
        created_to: datetime | None,
        cursor: CreatedAtCursor | None,
        sort: CreatedAtSort,
        limit: int,
    ) -> CursorPage[tuple[AuditLog, str]]:
        """List a filtered keyset page of immutable audit history.

        Args:
            session: Request-scoped database session.
            warehouse_id: Authorized warehouse filter or None for an owner-wide view.
            table_name: Optional exact changed-table filter.
            record_id: Optional exact changed-record filter.
            action: Optional exact audit action filter.
            source: Optional exact audit source filter.
            created_from: Inclusive creation-time lower bound.
            created_to: Inclusive creation-time upper bound.
            cursor: Exclusive prior-page position.
            sort: Deterministic creation-time order.
            limit: Maximum event count.

        Returns:
            Audit rows with actor names and an opaque next cursor.
        """
        logging.info("Executing ReliabilityCRUD.list_audit_logs")
        statement = select(AuditLog, User.name.label("actor_name")).join(
            User, User.id == AuditLog.actor_user_id
        )
        if warehouse_id is not None:
            statement = statement.where(AuditLog.warehouse_id == warehouse_id)
        if table_name is not None:
            statement = statement.where(AuditLog.table_name == table_name)
        if record_id is not None:
            statement = statement.where(AuditLog.record_id == record_id)
        if action is not None:
            statement = statement.where(AuditLog.action == action)
        if source is not None:
            statement = statement.where(AuditLog.source == source)
        statement = apply_created_at_pagination(
            statement,
            created_at_column=AuditLog.created_at,
            id_column=AuditLog.id,
            created_from=created_from,
            created_to=created_to,
            cursor=cursor,
            sort=sort,
        ).limit(limit + 1)
        rows = (await session.execute(statement)).all()
        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        next_cursor = None
        if has_more:
            last_audit = visible_rows[-1][0]
            next_cursor = encode_created_at_cursor(
                created_at=last_audit.created_at,
                record_id=last_audit.id,
                sort=sort,
            )
        return CursorPage(
            items=[(audit, actor_name) for audit, actor_name in visible_rows],
            next_cursor=next_cursor,
        )
