"""rename sales tax payable account to gst

Revision ID: c3d9e0f1a2b3
Revises: b2c8d9e0f1a2
Create Date: 2026-04-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d9e0f1a2b3"
down_revision: Union[str, None] = "b2c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


accounts = sa.table(
    "accounts",
    sa.column("name", sa.String),
    sa.column("account_number", sa.String),
    sa.column("account_type", sa.String),
    sa.column("is_active", sa.Boolean),
    sa.column("is_system", sa.Boolean),
    sa.column("balance", sa.Numeric),
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = bind.execute(
        sa.select(accounts.c.account_number).where(accounts.c.account_number == "2200")
    ).first()
    if existing:
        bind.execute(
            accounts.update()
            .where(accounts.c.account_number == "2200")
            .values(name="GST", account_type="LIABILITY", is_system=True)
        )
    else:
        op.bulk_insert(
            accounts,
            [{
                "name": "GST",
                "account_number": "2200",
                "account_type": "LIABILITY",
                "is_active": True,
                "is_system": True,
                "balance": 0,
            }],
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        accounts.update()
        .where(accounts.c.account_number == "2200")
        .values(name="Sales Tax Payable", account_type="LIABILITY", is_system=True)
    )
