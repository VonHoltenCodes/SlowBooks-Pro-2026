"""a job's sales tax treatment, and a bill line's use-tax override

- jobs.sales_tax_treatment: "charge_owner" (sales tax on the owner's pay
  applications, as every job has done) or "contractor_pays" (the
  contractor pays tax on the materials it buys, the lump-sum real-property
  rule: pay applications bill no sales tax and material bills accrue use
  tax). Null is "charge_owner", so every existing job bills as before.
- bill_lines.use_tax: a line's override. Null follows the job's rule, true
  accrues use tax on the line whatever the rule says, false never does.
  Null on every existing line, and no posting already made changes.

downgrade reverses.

Revision ID: c7e3a1f5d9b2
Revises: a4d8e2f6b9c3
Create Date: 2026-11-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c7e3a1f5d9b2"
down_revision: Union[str, None] = "a4d8e2f6b9c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ADDED = (
    ("jobs", "sales_tax_treatment", sa.String(20)),
    ("bill_lines", "use_tax", sa.Boolean()),
)


def _columns(table: str) -> set:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    # Books built by create_all already have them: each is added only when
    # missing, so a re-run is a no-op.
    for table, column, kind in _ADDED:
        have = _columns(table)
        if have and column not in have:
            with op.batch_alter_table(table) as batch:
                batch.add_column(sa.Column(column, kind, nullable=True))


def downgrade() -> None:
    for table, column, _kind in _ADDED:
        if column in _columns(table):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(column)
