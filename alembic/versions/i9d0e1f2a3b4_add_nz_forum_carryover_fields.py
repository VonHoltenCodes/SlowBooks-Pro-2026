"""add nz forum carryover fields

Revision ID: i9d0e1f2a3b4
Revises: h8c9d0e1f2a3
Create Date: 2026-04-16 06:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "i9d0e1f2a3b4"
down_revision: Union[str, None] = "h8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("payments") as batch_op:
        batch_op.add_column(sa.Column("deposit_transaction_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("is_voided", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.create_foreign_key(
            "fk_payments_deposit_transaction_id",
            "transactions",
            ["deposit_transaction_id"],
            ["id"],
        )
        batch_op.alter_column("is_voided", server_default=None, existing_type=sa.Boolean())

    with op.batch_alter_table("vendors") as batch_op:
        batch_op.add_column(sa.Column("default_expense_account_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_vendors_default_expense_account_id",
            "accounts",
            ["default_expense_account_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("vendors") as batch_op:
        batch_op.drop_constraint("fk_vendors_default_expense_account_id", type_="foreignkey")
        batch_op.drop_column("default_expense_account_id")

    with op.batch_alter_table("payments") as batch_op:
        batch_op.drop_constraint("fk_payments_deposit_transaction_id", type_="foreignkey")
        batch_op.drop_column("is_voided")
        batch_op.drop_column("deposit_transaction_id")
