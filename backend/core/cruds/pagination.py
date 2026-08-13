"""Shared PostgreSQL keyset pagination query helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.sql import Select

from backend.core.apis.schemas.common import (
    CreatedAtCursor,
    CreatedAtSort,
    ScalarCursor,
)


def apply_created_at_pagination(
    statement: Select[Any],
    *,
    created_at_column: Any,
    id_column: Any,
    created_from: datetime | None,
    created_to: datetime | None,
    cursor: CreatedAtCursor | None,
    sort: CreatedAtSort,
) -> Select[Any]:
    """Apply a date window, keyset boundary, and deterministic ordering.

    Equal timestamps are ordered by UUID so pages never skip or repeat rows.

    Args:
        statement: SQLAlchemy select statement to constrain.
        created_at_column: Model creation-time column.
        id_column: UUID primary-key column used as a stable tie breaker.
        created_from: Inclusive lower creation-time bound.
        created_to: Inclusive upper creation-time bound.
        cursor: Exclusive position after the prior page, if any.
        sort: Ascending or descending creation-time order.

    Returns:
        Select statement with all pagination clauses applied.
    """
    if created_from is not None:
        statement = statement.where(created_at_column >= created_from)
    if created_to is not None:
        statement = statement.where(created_at_column <= created_to)
    if cursor is not None:
        if sort == CreatedAtSort.CREATED_AT_ASC:
            statement = statement.where(
                or_(
                    created_at_column > cursor.created_at,
                    and_(
                        created_at_column == cursor.created_at,
                        id_column > cursor.record_id,
                    ),
                )
            )
        else:
            statement = statement.where(
                or_(
                    created_at_column < cursor.created_at,
                    and_(
                        created_at_column == cursor.created_at,
                        id_column < cursor.record_id,
                    ),
                )
            )
    if sort == CreatedAtSort.CREATED_AT_ASC:
        return statement.order_by(created_at_column.asc(), id_column.asc())
    return statement.order_by(created_at_column.desc(), id_column.desc())


def apply_scalar_pagination(
    statement: Select[Any],
    *,
    value_column: Any,
    id_column: Any,
    cursor: ScalarCursor | None,
    descending: bool,
) -> Select[Any]:
    """Apply a scalar/UUID keyset boundary and deterministic ordering.

    Args:
        statement: SQLAlchemy select statement to constrain.
        value_column: Normalized scalar primary sort expression.
        id_column: UUID primary-key column used as a tie breaker.
        cursor: Exclusive position after the prior page, if any.
        descending: Whether both sort components descend.

    Returns:
        Select statement with keyset and ordering clauses applied.
    """
    if cursor is not None:
        comparison = (
            value_column < cursor.value if descending else value_column > cursor.value
        )
        id_comparison = (
            id_column < cursor.record_id if descending else id_column > cursor.record_id
        )
        statement = statement.where(
            or_(
                comparison,
                and_(value_column == cursor.value, id_comparison),
            )
        )
    if descending:
        return statement.order_by(value_column.desc(), id_column.desc())
    return statement.order_by(value_column.asc(), id_column.asc())
