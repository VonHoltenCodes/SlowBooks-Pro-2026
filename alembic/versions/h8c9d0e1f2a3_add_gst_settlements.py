"""add gst settlement records

Revision ID: h8c9d0e1f2a3
Revises: g7b8c9d0e1f2
Create Date: 2026-04-14 17:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "h8c9d0e1f2a3"
down_revision: Union[str, None] = "g7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gst_settlements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("settlement_date", sa.Date(), nullable=False),
        sa.Column("net_position", sa.String(length=20), nullable=False),
        sa.Column("net_gst", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("box9_adjustments", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("box13_adjustments", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("status", sa.Enum("CONFIRMED", "VOIDED", name="gstsettlementstatus"), nullable=False),
        sa.Column("bank_transaction_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["bank_transaction_id"], ["bank_transactions.id"]),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bank_transaction_id"),
        sa.UniqueConstraint("transaction_id"),
    )
    op.create_index(op.f("ix_gst_settlements_id"), "gst_settlements", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_gst_settlements_id"), table_name="gst_settlements")
    op.drop_table("gst_settlements")
    op.execute("DROP TYPE IF EXISTS gstsettlementstatus")
