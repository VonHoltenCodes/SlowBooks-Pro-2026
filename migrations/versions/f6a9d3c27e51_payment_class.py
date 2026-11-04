"""payments.class_id and bill_payments.class_id: a payment's class (#686)

A customer payment and a bill payment posted without a class, so their
bank and A/R or A/P lines could not be put on a division. Each can now
name a class, carried on the payment's own posted lines (and the
early-payment discount taken with it). Both nullable: a payment with no
class posts as before.

Idempotent, table by table, so books that already have the column (a
database built by create_all, or a run interrupted between the two)
upgrade cleanly. Batch mode adds the foreign key on SQLite and PostgreSQL
alike; downgrade drops what is there.

Revision ID: f6a9d3c27e51
Revises: e5c1a7d3f9b2
Create Date: 2026-11-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f6a9d3c27e51"
down_revision: Union[str, None] = "e5c1a7d3f9b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, foreign key name)
TABLES = (
    ("payments", "fk_payments_class_id"),
    ("bill_payments", "fk_bill_payments_class_id"),
)


def _have(insp, table: str) -> set[str]:
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())
    for table, fk in TABLES:
        if table not in tables or "class_id" in _have(insp, table):
            continue
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("class_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(fk, "classes", ["class_id"], ["id"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())
    for table, fk in TABLES:
        if table not in tables or "class_id" not in _have(insp, table):
            continue
        fks = {f["name"] for f in insp.get_foreign_keys(table)}
        with op.batch_alter_table(table) as batch:
            if fk in fks:
                batch.drop_constraint(fk, type_="foreignkey")
            batch.drop_column("class_id")
