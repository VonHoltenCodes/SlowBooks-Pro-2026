"""sales line unit prices to four decimal places

The rate (unit price) on invoice, estimate, credit memo and recurring
invoice lines was Numeric(15, 2), so a sub-cent price could not be kept:
bulk packaging priced at $0.045 a box was refused by the forms, and one
entered through the API was stored as $0.05 (PostgreSQL) or read back as
$0.04 (SQLite) while its line amount had been worked out at $0.045, so the
document showed 1,000 x $0.04 = $45.00 and re-saving it changed the total
(2.17.3 exploratory, macbase1 suggestion S-f).

Numeric(17, 4) keeps the same thirteen integer digits as the money columns
(a9b0c1d2e3f4) and adds two decimal places, so every stored value fits and
none changes. Line amounts stay Numeric(15, 2): each is still rounded to
the cent. PostgreSQL alters the column in place; SQLite rewrites the table
(batch_alter_table), as a9b0c1d2e3f4 does.

The downgrade rounds unit prices back to the cent.

Revision ID: 6f57f762f464
Revises: a9b0c1d2e3f4
Create Date: 2026-09-26

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "6f57f762f464"
down_revision: Union[str, None] = "a9b0c1d2e3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "invoice_lines",
    "estimate_lines",
    "credit_memo_lines",
    "recurring_invoice_lines",
)


def _set_rate(new: sa.Numeric, old: sa.Numeric) -> None:
    insp = sa.inspect(op.get_bind())
    present = set(insp.get_table_names())
    for table in TABLES:
        if table not in present:
            continue
        with op.batch_alter_table(table) as batch:
            batch.alter_column("rate", existing_type=old, type_=new)


def upgrade() -> None:
    _set_rate(sa.Numeric(17, 4), sa.Numeric(15, 2))


def downgrade() -> None:
    _set_rate(sa.Numeric(15, 2), sa.Numeric(17, 4))
