"""state_w4_fields

State withholding for all 50 states + DC (table-driven engines): the
employee record gains the state W-4 inputs the engines need — allowances,
extra state withholding, an elected rate (Arizona) and a flat local rate
(Indiana counties, Maryland counties, Ohio cities, PA municipalities ...).

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-09-03

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("employees") as batch_op:
        batch_op.add_column(
            sa.Column(
                "state_allowances", sa.Integer(), nullable=False, server_default="0"
            )
        )
        batch_op.add_column(
            sa.Column(
                "state_extra_withholding",
                sa.Numeric(12, 2),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("state_rate_override", sa.Numeric(6, 3), nullable=True)
        )
        batch_op.add_column(
            sa.Column("local_tax_rate", sa.Numeric(6, 3), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("employees") as batch_op:
        batch_op.drop_column("local_tax_rate")
        batch_op.drop_column("state_rate_override")
        batch_op.drop_column("state_extra_withholding")
        batch_op.drop_column("state_allowances")
