"""line_taxable

Per-line sales tax: is_taxable on invoice, estimate and recurring-invoice
lines (default true = today's behaviour). Field report from a Texas IT
shop: labor on a customer-owned device is not taxed, the part and its
install on the same invoice are — items already carried a taxable flag
the invoice math ignored.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-03

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("invoice_lines", "estimate_lines", "recurring_invoice_lines")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "is_taxable",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.true(),
                )
            )


def downgrade() -> None:
    for table in reversed(_TABLES):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("is_taxable")
