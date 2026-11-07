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


def _columns() -> set:
    insp = sa.inspect(op.get_bind())
    if "sov_lines" not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns("sov_lines")}


def upgrade() -> None:
    # Books built by create_all (or stamped back over the columns) already
    # have them: each is added only when missing, so a re-run is a no-op.
    have = _columns()
    if "revenue_account_id" in have and "is_taxable" in have:
        return
    with op.batch_alter_table("sov_lines") as batch:
        if "revenue_account_id" not in have:
            batch.add_column(
                sa.Column("revenue_account_id", sa.Integer(), nullable=True)
            )
            batch.create_foreign_key(
                "fk_sov_lines_revenue_account",
                "accounts",
                ["revenue_account_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if "is_taxable" not in have:
            batch.add_column(
                sa.Column(
                    "is_taxable", sa.Boolean(), nullable=False, server_default=sa.true()
                )
            )


def downgrade() -> None:
    have = _columns()
    if not have & {"revenue_account_id", "is_taxable"}:
        return
    # create_all books carry the foreign key unnamed; dropping the column
    # takes it with it there.
    named = {
        fk.get("name") for fk in sa.inspect(op.get_bind()).get_foreign_keys("sov_lines")
    }
    with op.batch_alter_table("sov_lines") as batch:
        if "revenue_account_id" in have:
            if "fk_sov_lines_revenue_account" in named:
                batch.drop_constraint(
                    "fk_sov_lines_revenue_account", type_="foreignkey"
                )
            batch.drop_column("revenue_account_id")
        if "is_taxable" in have:
            batch.drop_column("is_taxable")
