"""A memorized bill line keeps its Use tax box

- memorized_transaction_lines.use_tax: a memorized bill line's use-tax
  mark, as bill_lines.use_tax: null follows the job's sales tax rule, true
  accrues use tax on the line anyway, false never does. Memorize Bill
  carries a bill line's mark into the template and each bill entered from
  it, so a line ticked to accrue use tax still accrues it. Existing lines
  stay null, which is what every bill entered from them has used so far.

downgrade drops the column.

Revision ID: e2b6c4d8f0a3
Revises: b4d2e7a9c6f1
Create Date: 2026-11-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e2b6c4d8f0a3"
down_revision: Union[str, None] = "b4d2e7a9c6f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "memorized_transaction_lines"


def _columns() -> set:
    insp = sa.inspect(op.get_bind())
    if _TABLE not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(_TABLE)}


def upgrade() -> None:
    # Books built by create_all already have the column: added only when
    # missing, so a re-run is a no-op.
    cols = _columns()
    if cols and "use_tax" not in cols:
        with op.batch_alter_table(_TABLE) as batch:
            batch.add_column(sa.Column("use_tax", sa.Boolean(), nullable=True))


def downgrade() -> None:
    if "use_tax" in _columns():
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column("use_tax")
