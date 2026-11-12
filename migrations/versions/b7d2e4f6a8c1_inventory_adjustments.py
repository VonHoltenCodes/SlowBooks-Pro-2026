"""inventory_adjustments: one adjustment of several items (#801)

An inventory adjustment covered one item, so a count that moved twenty
items was twenty adjustments. An adjustment of several items is a row here;
its lines are its inventory movements (source_type "inventory_adjustment",
source_id this id) and it posts one journal entry for the lot. Nothing
already posted changes: a one-item adjustment keeps source_type "adjustment".

Revision ID: b7d2e4f6a8c1
Revises: c7e3a9d5f1b4
Create Date: 2026-11-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b7d2e4f6a8c1"
down_revision: Union[str, None] = "c7e3a9d5f1b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "inventory_adjustments" in insp.get_table_names():
        return
    op.create_table(
        "inventory_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column(
            "transaction_id",
            sa.Integer(),
            sa.ForeignKey("transactions.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_inventory_adjustments_id", "inventory_adjustments", ["id"], unique=False
    )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "inventory_adjustments" not in insp.get_table_names():
        return
    op.drop_index("ix_inventory_adjustments_id", table_name="inventory_adjustments")
    op.drop_table("inventory_adjustments")
