"""estimates.tax_exempt and invoices.tax_exempt: the document's switch (#864)

An estimate, invoice or sales receipt says once that it is sales tax exempt.
One already made for a non-taxable customer, with no tax on it, starts
exempt, which is what it already was; the rest start off.

Revision ID: b8640c3e4a5b
Revises: b8640b2d3f4a
Create Date: 2026-11-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b8640c3e4a5b"
down_revision: Union[str, None] = "b8640b2d3f4a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("estimates", "invoices")


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = insp.get_table_names()
    for table in _TABLES:
        if table not in tables:
            continue
        if "tax_exempt" not in {c["name"] for c in insp.get_columns(table)}:
            op.add_column(
                table,
                sa.Column(
                    "tax_exempt",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                ),
            )
        if "customers" in tables:
            bind.execute(
                sa.text(
                    f"UPDATE {table} SET tax_exempt = :yes "
                    "WHERE (tax_amount IS NULL OR tax_amount = 0) AND customer_id IN "
                    "(SELECT id FROM customers WHERE is_taxable = :no)"
                ),
                {"yes": True, "no": False},
            )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    tables = insp.get_table_names()
    for table in _TABLES:
        if table not in tables:
            continue
        if "tax_exempt" in {c["name"] for c in insp.get_columns(table)}:
            with op.batch_alter_table(table) as batch:
                batch.drop_column("tax_exempt")
