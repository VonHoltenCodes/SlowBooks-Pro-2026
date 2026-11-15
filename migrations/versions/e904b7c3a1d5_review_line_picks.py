"""bank_transactions pick_*: the To review row's Class, Job and Name (#904)

A category picked for a statement line in To review was kept on the line as
it was picked; the class, the job and the name (a vendor, a customer, or a
name typed that is no vendor's) lived only in the page, so any redraw (one
line's Add, Exclude, Match, Rule…, a reload) lost them on every other line.
They are kept on the line now, as the category is, until the line is added.

Five nullable columns, every line from before this step has none (nothing
picked), exactly as it was. Plain integers, no foreign keys: a pick is a
note on a line not yet in the books, and must never stop a vendor, customer,
class or job from being deleted; a pick whose record is gone is ignored.

Idempotent, column by column (a database built by create_all already has
them); downgrade drops what is there.

Revision ID: e904b7c3a1d5
Revises: d897a2c4e6f1
Create Date: 2026-11-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e904b7c3a1d5"
down_revision: Union[str, None] = "d897a2c4e6f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "bank_transactions"
COLUMNS = {
    "pick_class_id": sa.Integer,
    "pick_job_id": sa.Integer,
    "pick_vendor_id": sa.Integer,
    "pick_customer_id": sa.Integer,
    "pick_payee": lambda: sa.String(200),
}


def _have(insp) -> set[str]:
    return {c["name"] for c in insp.get_columns(TABLE)}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names():
        return
    missing = [name for name in COLUMNS if name not in _have(insp)]
    if not missing:
        return
    with op.batch_alter_table(TABLE) as batch:
        for name in missing:
            batch.add_column(sa.Column(name, COLUMNS[name](), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names():
        return
    there = [name for name in COLUMNS if name in _have(insp)]
    if not there:
        return
    with op.batch_alter_table(TABLE) as batch:
        for name in reversed(there):
            batch.drop_column(name)
