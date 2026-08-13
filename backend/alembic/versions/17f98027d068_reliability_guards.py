"""reliability_guards

Revision ID: 17f98027d068
Revises: 0fe478e9125e
Create Date: 2026-08-13 18:32:17.188589
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "17f98027d068"
down_revision: str | Sequence[str] | None = "0fe478e9125e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this schema revision."""
    op.create_index(
        "uq_receipt_tracking_number",
        "inbound_receipts",
        [sa.literal_column("lower(tracking_number)")],
        unique=True,
        postgresql_where=sa.text("tracking_number IS NOT NULL"),
    )
    op.create_index(
        "uq_receipt_warehouse_ticket",
        "inbound_receipts",
        ["warehouse_id", sa.literal_column("lower(ticket_number)")],
        unique=True,
        postgresql_where=sa.text("ticket_number IS NOT NULL"),
    )
    op.execute(
        """
        CREATE FUNCTION wms_prevent_immutable_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_append_only
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION wms_prevent_immutable_change()
        """
    )
    op.execute(
        """
        CREATE TRIGGER inventory_movements_append_only
        BEFORE UPDATE OR DELETE ON inventory_movements
        FOR EACH ROW EXECUTE FUNCTION wms_prevent_immutable_change()
        """
    )


def downgrade() -> None:
    """Revert this schema revision."""
    op.execute("DROP TRIGGER inventory_movements_append_only ON inventory_movements")
    op.execute("DROP TRIGGER audit_logs_append_only ON audit_logs")
    op.execute("DROP FUNCTION wms_prevent_immutable_change()")
    op.drop_index(
        "uq_receipt_warehouse_ticket",
        table_name="inbound_receipts",
        postgresql_where=sa.text("ticket_number IS NOT NULL"),
    )
    op.drop_index(
        "uq_receipt_tracking_number",
        table_name="inbound_receipts",
        postgresql_where=sa.text("tracking_number IS NOT NULL"),
    )
