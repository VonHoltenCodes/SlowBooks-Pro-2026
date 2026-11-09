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
Revises: e1b7c4a92d68
Create Date: 2026-11-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a4d8e2f6b9c3"
down_revision: Union[str, None] = "e1b7c4a92d68"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set:
    insp = sa.inspect(op.get_bind())
    if "sov_lines" not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns("sov_lines")}


def _default_existing_lines_to_income(bind) -> None:
    """Point every line made before this at the default income account
    (4000), where the pay applications before this credited. A company
    without one leaves them null. account_type is stored as the enum NAME
    ('INCOME') on every engine; PostgreSQL has no upper() for its enum."""
    income = bind.execute(
        sa.text(
            "SELECT id FROM accounts WHERE account_number = '4000' "
            "AND account_type = 'INCOME' LIMIT 1"
        )
    ).scalar()
    if income is not None:
        bind.execute(
            sa.text(
                "UPDATE sov_lines SET revenue_account_id = :a "
                "WHERE revenue_account_id IS NULL"
            ),
            {"a": income},
        )


def upgrade() -> None:
    # Books built by create_all (or stamped back over the columns) already
    # have them: each is added only when missing, so a re-run is a no-op.
    have = _columns()
    if "revenue_account_id" in have and "is_taxable" in have:
        return
    backfill = "revenue_account_id" not in have
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
    if backfill:
        _default_existing_lines_to_income(op.get_bind())


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
