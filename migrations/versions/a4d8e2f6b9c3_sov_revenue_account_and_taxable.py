"""schedule-of-values lines carry a revenue account and a taxable flag

The SOV line already carries its cost code (section); this adds what the
pay application needs to post a line to the books on its own terms:

- revenue_account_id: the income account the line's billing credits (SET
  NULL). Null — every line made before this — posts to 4050 Contract
  Revenue, which construction setup creates.
- is_taxable: whether sales tax is charged on the line's billing. Every
  existing line is taxable, so an application on an existing job taxes
  exactly as it did; Build from accepted estimate sets it from the
  estimate's lines from now on.

downgrade reverses.

Revision ID: a4d8e2f6b9c3
Revises: d7f3a9c1b5e2
Create Date: 2026-11-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a4d8e2f6b9c3"
down_revision: Union[str, None] = "d7f3a9c1b5e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sov_lines") as batch:
        batch.add_column(sa.Column("revenue_account_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "is_taxable", sa.Boolean(), nullable=False, server_default=sa.true()
            )
        )
        batch.create_foreign_key(
            "fk_sov_lines_revenue_account",
            "accounts",
            ["revenue_account_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("sov_lines") as batch:
        batch.drop_constraint("fk_sov_lines_revenue_account", type_="foreignkey")
        batch.drop_column("is_taxable")
        batch.drop_column("revenue_account_id")
