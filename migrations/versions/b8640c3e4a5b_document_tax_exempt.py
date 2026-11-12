"""estimates and invoices: the tax_exempt switch and the certificate copy (#864)

An estimate, invoice or sales receipt says once that it is sales tax exempt.
One already made for a non-taxable customer, with no tax on it, starts
exempt, which is what it already was; the rest start off. Each also keeps
its own copy of its job's certificate (exempt_certificate_number, _state,
_kind, _expires), taken when it is made exempt, so a later edit of the job
never changes it; no job has a certificate before this, so every document
starts with none.

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
_COPY = (
    ("exempt_certificate_number", sa.String(50)),
    ("exempt_certificate_state", sa.String(20)),
    ("exempt_certificate_kind", sa.String(20)),
    ("exempt_certificate_expires", sa.Date()),
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = insp.get_table_names()
    for table in _TABLES:
        if table not in tables:
            continue
        have = {c["name"] for c in insp.get_columns(table)}
        if "tax_exempt" not in have:
            op.add_column(
                table,
                sa.Column(
                    "tax_exempt",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                ),
            )
        for name, type_ in _COPY:
            if name not in have:
                op.add_column(table, sa.Column(name, type_, nullable=True))
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
        have = {c["name"] for c in insp.get_columns(table)}
        drop = [n for n in ("tax_exempt", *(n for n, _ in _COPY)) if n in have]
        if drop:
            with op.batch_alter_table(table) as batch:
                for name in drop:
                    batch.drop_column(name)
