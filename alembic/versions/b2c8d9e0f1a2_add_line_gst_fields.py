"""add line gst fields

Revision ID: b2c8d9e0f1a2
Revises: a1f7c8d9e0b1
Create Date: 2026-04-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c8d9e0f1a2"
down_revision: Union[str, None] = "a1f7c8d9e0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LINE_TABLES = [
    "invoice_lines",
    "estimate_lines",
    "bill_lines",
    "purchase_order_lines",
    "credit_memo_lines",
    "recurring_invoice_lines",
]


def upgrade() -> None:
    for table_name in LINE_TABLES:
        op.add_column(
            table_name,
            sa.Column("gst_code", sa.String(length=20), server_default="GST15", nullable=False),
        )
        op.add_column(
            table_name,
            sa.Column("gst_rate", sa.Numeric(precision=6, scale=4), server_default="0.1500", nullable=False),
        )
        op.create_foreign_key(
            op.f(f"fk_{table_name}_gst_code_gst_codes"),
            table_name,
            "gst_codes",
            ["gst_code"],
            ["code"],
        )

    for table_name in LINE_TABLES:
        op.alter_column(table_name, "gst_code", server_default=None)
        op.alter_column(table_name, "gst_rate", server_default=None)


def downgrade() -> None:
    for table_name in reversed(LINE_TABLES):
        op.drop_constraint(op.f(f"fk_{table_name}_gst_code_gst_codes"), table_name, type_="foreignkey")
        op.drop_column(table_name, "gst_rate")
        op.drop_column(table_name, "gst_code")
