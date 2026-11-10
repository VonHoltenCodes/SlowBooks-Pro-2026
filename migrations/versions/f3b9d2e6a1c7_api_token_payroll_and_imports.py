"""api tokens may run payroll and imports (#755): api_tokens.allow_payroll,
api_tokens.allow_imports

A token is never an administrator, and payroll and the migration imports
were an administrator's. An agent keeping the books gets them the way
backups came (#434): an administrator ticks "Can run payroll" or "Can
import" on a bookkeeper token. Every token that exists today starts without
either.

Revision ID: f3b9d2e6a1c7
Revises: e2b6c4d8f0a3
Create Date: 2026-11-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f3b9d2e6a1c7"
down_revision: Union[str, None] = "e2b6c4d8f0a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = ("allow_payroll", "allow_imports")


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "api_tokens" not in insp.get_table_names():
        return
    present = {c["name"] for c in insp.get_columns("api_tokens")}
    for name in _COLUMNS:
        if name not in present:
            op.add_column(
                "api_tokens",
                sa.Column(
                    name, sa.Boolean(), nullable=False, server_default=sa.false()
                ),
            )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "api_tokens" not in insp.get_table_names():
        return
    present = {c["name"] for c in insp.get_columns("api_tokens")}
    with op.batch_alter_table("api_tokens") as batch:
        for name in _COLUMNS:
            if name in present:
                batch.drop_column(name)
