"""add is_voided to bill_payments

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-06-01 17:00:00.000000

Mirror customer-payment void support on the AP side. The route
POST /api/bill-payments/{id}/void posts a reversing JE and restores
each allocated bill's balance — the column is the idempotency guard
that lets a concurrent second void return 400 instead of double-
reversing.

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bill_payments",
        sa.Column(
            "is_voided",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("bill_payments", "is_voided")
