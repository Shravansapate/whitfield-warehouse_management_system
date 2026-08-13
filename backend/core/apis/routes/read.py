"""Dashboard backlog and append-only audit endpoints."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.commons.auth import CurrentUser, get_current_user, require_roles
from backend.core import logger
from backend.core.apis.schemas.common import AuditResponse, CreatedAtSort
from backend.core.apis.schemas.orders import DashboardSummaryResponse, OrderResponse
from backend.core.apis.schemas.receiving import ReceiptResponse
from backend.core.controllers.order_controller import OrderController
from backend.core.controllers.read_controller import ReadController
from backend.core.controllers.receiving_controller import ReceivingController
from backend.core.database.session import get_session
from backend.core.models.enums import AuditSource, UserRole

router = APIRouter(tags=["operations"])
logging = logger(__name__)
controller = ReadController()
receiving = ReceivingController()
orders = OrderController()


@router.get("/dashboard/summary", response_model=DashboardSummaryResponse)
async def dashboard_summary(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    warehouse_id: uuid.UUID | None = None,
) -> dict:
    """Return current operational counts for an allowed warehouse scope.

    Args:
        user: Authenticated actor.
        session: Request-scoped database session.
        warehouse_id: Optional owner warehouse selector.

    Returns:
        Warehouse identity and current metrics.
    """
    try:
        logging.info("Calling GET /api/v1/dashboard/summary endpoint")
        return await controller.dashboard_summary(
            session, warehouse_id=warehouse_id, user=user
        )
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected dashboard failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.get("/dashboard/receiving-backlog", response_model=list[ReceiptResponse])
async def receiving_backlog(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    warehouse_id: uuid.UUID | None = None,
) -> list[dict]:
    """Return recent inbound work for the authorized warehouse.

    Args:
        user: Authenticated actor.
        session: Request-scoped database session.
        warehouse_id: Optional owner warehouse selector.

    Returns:
        Recent receipt summaries.
    """
    try:
        logging.info("Calling GET /api/v1/dashboard/receiving-backlog endpoint")
        page = await receiving.list_receipts(
            session,
            warehouse_id=warehouse_id,
            limit=100,
            status=None,
            created_from=None,
            created_to=None,
            cursor=None,
            sort=CreatedAtSort.CREATED_AT_DESC,
            user=user,
        )
        return [
            row
            for row in page.items
            if str(row["status"])
            in {"open", "receiving", "ReceiptStatus.OPEN", "ReceiptStatus.RECEIVING"}
        ]
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected receiving-backlog failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.get("/dashboard/fulfillment-backlog", response_model=list[OrderResponse])
async def fulfillment_backlog(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    warehouse_id: uuid.UUID | None = None,
) -> list[dict]:
    """Return nonterminal outbound work for the authorized warehouse.

    Args:
        user: Authenticated actor.
        session: Request-scoped database session.
        warehouse_id: Optional owner warehouse selector.

    Returns:
        Recent nonterminal orders.
    """
    try:
        logging.info("Calling GET /api/v1/dashboard/fulfillment-backlog endpoint")
        page = await orders.list_orders(
            session,
            warehouse_id=warehouse_id,
            limit=100,
            status=None,
            created_from=None,
            created_to=None,
            cursor=None,
            sort=CreatedAtSort.CREATED_AT_DESC,
            user=user,
        )
        return [
            row
            for row in page.items
            if getattr(row["status"], "value", row["status"])
            in {"allocated", "picking", "packed", "label_created"}
        ]
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected fulfillment-backlog failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error


@router.get("/audit-logs", response_model=list[AuditResponse])
async def audit_logs(
    response: Response,
    user: Annotated[
        CurrentUser, Depends(require_roles(UserRole.MANAGER, UserRole.OWNER))
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    warehouse_id: uuid.UUID | None = None,
    table_name: str | None = Query(default=None, min_length=1, max_length=100),
    record_id: uuid.UUID | None = None,
    action: str | None = Query(default=None, min_length=1, max_length=120),
    source_filter: Annotated[AuditSource | None, Query(alias="source")] = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    cursor: str | None = Query(default=None, min_length=1, max_length=512),
    sort: CreatedAtSort = CreatedAtSort.CREATED_AT_DESC,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict]:
    """List a filtered audit cursor page within manager or owner scope.

    The body remains a JSON list; a further page is advertised in
    the ``X-Next-Cursor`` response header.

    Args:
        response: Mutable HTTP response used for the next-cursor header.
        user: Authorized manager or owner actor.
        session: Request-scoped database session.
        warehouse_id: Optional owner warehouse filter.
        table_name: Optional changed-table filter.
        record_id: Optional changed-record filter.
        action: Optional exact audit action filter.
        source_filter: Optional exact source filter.
        created_from: Inclusive creation-time lower bound.
        created_to: Inclusive creation-time upper bound.
        cursor: Opaque position returned by the prior page.
        sort: Ascending or descending creation-time order.
        limit: Maximum event count.

    Returns:
        Human-readable audit records.
    """
    try:
        logging.info("Calling GET /api/v1/audit-logs endpoint")
        page = await controller.audit_logs(
            session,
            user=user,
            warehouse_id=warehouse_id,
            table_name=table_name,
            record_id=record_id,
            action=action,
            source=source_filter,
            created_from=created_from,
            created_to=created_to,
            cursor=cursor,
            sort=sort,
            limit=limit,
        )
        if page.next_cursor is not None:
            response.headers["X-Next-Cursor"] = page.next_cursor
        return page.items
    except HTTPException:
        raise
    except Exception as error:
        logging.error(
            "Unexpected audit-list failure error_type=%s", type(error).__name__
        )
        raise HTTPException(status_code=500, detail="Internal Server Error") from error
