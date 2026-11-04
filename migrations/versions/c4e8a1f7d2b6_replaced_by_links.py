"""void-and-replace links: replaces_id / replaced_by_id (#689)

A posted expense, card charge, transfer, credit memo, vendor credit,
customer payment or bill payment has no edit of its own; from a report's
drill-down it is changed by voiding it and posting a new one. Each of the
two names the other: the new one `replaces_id`, the voided one
`replaced_by_id`. Expenses, card charges and transfers are transactions
rows; the others have their own tables.

Plain nullable integers with no foreign key: both rows always stay (a void
keeps the original), and a self-reference would make SQLite rebuild the
ledger's largest table for nothing. NULL on everything already posted.
Books that already have the columns are left as they are; downgrade drops
them.

Revision ID: c4e8a1f7d2b6
Revises: f6a9d3c27e51
Create Date: 2026-11-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c4e8a1f7d2b6"
down_revision: Union[str, None] = "f6a9d3c27e51"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "transactions",
    "payments",
    "bill_payments",
    "credit_memos",
    "vendor_credits",
)
COLUMNS = ("replaces_id", "replaced_by_id")


def _columns(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    # books that already have the columns (made by create_all and stamped
    # back) are left as they are
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())
    for table in TABLES:
        if table not in tables:
            continue
        have = _columns(insp, table)
        for column in COLUMNS:
            if column not in have:
                op.add_column(table, sa.Column(column, sa.Integer(), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())
    for table in TABLES:
        if table not in tables:
            continue
        gone = [c for c in COLUMNS if c in _columns(insp, table)]
        if gone:
            with op.batch_alter_table(table) as batch:
                for column in gone:
                    batch.drop_column(column)
