"""persisted_picking_checklist

Revision ID: 55a9d9b33e21
Revises: 17f98027d068
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "55a9d9b33e21"
down_revision: str | Sequence[str] | None = "17f98027d068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a constrained physical picked count to every order line."""
    op.add_column(
        "order_items",
        sa.Column(
            "picked_quantity",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_order_items_order_item_picked_quantity_valid"),
        "order_items",
        "picked_quantity >= 0 AND picked_quantity <= quantity",
    )


def downgrade() -> None:
    """Remove persisted picking checklist state."""
    op.drop_constraint(
        op.f("ck_order_items_order_item_picked_quantity_valid"),
        "order_items",
        type_="check",
    )
    op.drop_column("order_items", "picked_quantity")
