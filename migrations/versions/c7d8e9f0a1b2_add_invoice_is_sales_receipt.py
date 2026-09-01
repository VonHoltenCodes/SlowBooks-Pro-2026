"""Sales receipts: is_sales_receipt flag on invoices

A sales receipt is stored as an Invoice plus an immediate Payment; the
flag distinguishes the two document types in lists and QB interop.

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-08-17
"""

import sqlalchemy as sa
from alembic import op

revision = "c7d8e9f0a1b2"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Batch mode for SQLite compatibility (desktop company files)
    with op.batch_alter_table("invoices") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_sales_receipt",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.create_index("ix_invoices_is_sales_receipt", ["is_sales_receipt"])


def downgrade() -> None:
    with op.batch_alter_table("invoices") as batch_op:
        batch_op.drop_index("ix_invoices_is_sales_receipt")
        batch_op.drop_column("is_sales_receipt")
