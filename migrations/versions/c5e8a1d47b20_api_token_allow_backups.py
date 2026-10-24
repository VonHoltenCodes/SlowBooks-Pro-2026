"""api tokens may make backups (#434): api_tokens.allow_backups

A token is never an administrator, so the backups a program takes before
it writes (an import tool's safety net) need a permission of their own:
an administrator ticks "Can make backups" on the token. It lets the token
make, list and download this company's backups on top of its role.
Restoring one stays with a signed-in administrator. Every token that
exists today starts without it.

Revision ID: c5e8a1d47b20
Revises: a4330b1c2d3e
Create Date: 2026-10-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c5e8a1d47b20"
down_revision: Union[str, None] = "a4330b1c2d3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column() -> sa.Column:
    return sa.Column(
        "allow_backups", sa.Boolean(), nullable=False, server_default=sa.false()
    )


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "api_tokens" not in insp.get_table_names():
        return
    present = {c["name"] for c in insp.get_columns("api_tokens")}
    if "allow_backups" not in present:
        op.add_column("api_tokens", _column())


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "api_tokens" not in insp.get_table_names():
        return
    present = {c["name"] for c in insp.get_columns("api_tokens")}
    if "allow_backups" in present:
        with op.batch_alter_table("api_tokens") as batch:
            batch.drop_column("allow_backups")
