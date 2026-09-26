"""purchase line and item prices to four decimal places; bills get due dates

Sub-cent unit prices (6f57f762f464) reached the sales lines only, but the
case that asked for them was a purchase: cake boxes bought at $0.045 each
(2.17.3 exploratory, macbase1 S-f). The rate on bill, purchase order and
vendor credit lines, and an item's rate and standard cost, go from
Numeric(15, 2) to Numeric(17, 4): the same thirteen integer digits as the
money columns, two more places, so no stored value changes. Line amounts
stay Numeric(15, 2), rounded to the cent. The downgrade rounds them back.

Bills made from a purchase order before this release were saved with terms
and no due date, so they never aged (macbase1 F10). Each one now gets the
due date its date and terms give, by the rule bills and invoices use:
"Due on Receipt" (or COD, Net 0) is the bill's date, "Net N" is N days on,
anything else 30. The downgrade leaves the dates: they are right either way.

Revision ID: 4c7e2a9d1b05
Revises: 697f63b2975e
Create Date: 2026-09-26

"""

from datetime import date, timedelta
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "4c7e2a9d1b05"
down_revision: Union[str, None] = "697f63b2975e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    ("bill_lines", "rate"),
    ("purchase_order_lines", "rate"),
    ("vendor_credit_lines", "rate"),
    ("items", "rate"),
    ("items", "cost"),
)


def _set_type(new: sa.Numeric, old: sa.Numeric) -> None:
    present = set(sa.inspect(op.get_bind()).get_table_names())
    by_table: dict[str, list[str]] = {}
    for table, column in COLUMNS:
        if table in present:
            by_table.setdefault(table, []).append(column)
    for table, columns in by_table.items():
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.alter_column(column, existing_type=old, type_=new)


def _due(base, terms):
    if isinstance(base, str):
        base = date.fromisoformat(base[:10])
    t = (terms or "").strip().lower()
    if t in ("due on receipt", "due upon receipt", "cod", "net 0"):
        return base
    try:
        return base + timedelta(days=int(t.replace("net ", "").strip()))
    except ValueError:
        return base + timedelta(days=30)


def _fill_bill_due_dates() -> None:
    bind = op.get_bind()
    if "bills" not in set(sa.inspect(bind).get_table_names()):
        return
    bills = sa.table(
        "bills",
        sa.column("id", sa.Integer),
        sa.column("date", sa.Date),
        sa.column("due_date", sa.Date),
        sa.column("terms", sa.String),
    )
    rows = bind.execute(
        sa.select(bills.c.id, bills.c.date, bills.c.terms).where(
            bills.c.due_date.is_(None), bills.c.date.isnot(None)
        )
    ).fetchall()
    for bill_id, bill_date, terms in rows:
        bind.execute(
            bills.update()
            .where(bills.c.id == bill_id)
            .values(due_date=_due(bill_date, terms))
        )


def upgrade() -> None:
    _set_type(sa.Numeric(17, 4), sa.Numeric(15, 2))
    _fill_bill_due_dates()


def downgrade() -> None:
    _set_type(sa.Numeric(15, 2), sa.Numeric(17, 4))
