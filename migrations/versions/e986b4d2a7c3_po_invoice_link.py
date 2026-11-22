"""purchase_orders.invoice_id: the invoice a purchase order fills (#986)

A purchase order ties to a vendor, a job and its items; nothing tied it to
the customer invoice that sells the goods. One invoice may now name many
purchase orders, each PO naming the one invoice it was bought for. A link is
a plain reference: nothing posts and no total changes.

One nullable column, indexed, with a foreign key to invoices that clears
itself when the invoice row goes (ON DELETE SET NULL). Every purchase order
from before this step fills no invoice, exactly as it was.

Idempotent (a database built by create_all already has the column). Batch
mode adds the foreign key on SQLite and PostgreSQL alike; downgrade drops
what is there.

Revision ID: e986b4d2a7c3
Revises: e904b7c3a1d5
Create Date: 2026-11-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e986b4d2a7c3"
down_revision: Union[str, None] = "e904b7c3a1d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "purchase_orders"
COLUMN = "invoice_id"
FK = "fk_purchase_orders_invoice_id"
INDEX = "ix_purchase_orders_invoice_id"


def _have(insp) -> set[str]:
    return {c["name"] for c in insp.get_columns(TABLE)}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names() or COLUMN in _have(insp):
        return
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(sa.Column(COLUMN, sa.Integer(), nullable=True))
        batch.create_foreign_key(FK, "invoices", [COLUMN], ["id"], ondelete="SET NULL")
        batch.create_index(INDEX, [COLUMN])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names() or COLUMN not in _have(insp):
        return
    fks = {f["name"] for f in insp.get_foreign_keys(TABLE)}
    indexes = {i["name"] for i in insp.get_indexes(TABLE)}
    with op.batch_alter_table(TABLE) as batch:
        if INDEX in indexes:
            batch.drop_index(INDEX)
        if FK in fks:
            batch.drop_constraint(FK, type_="foreignkey")
        batch.drop_column(COLUMN)
