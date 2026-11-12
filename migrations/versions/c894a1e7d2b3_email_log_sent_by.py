"""email_log: who sent it (#894)

A document's Activity says who emailed it. The send log kept the recipient,
the subject and whether it went, never who asked for it: the audit log
skips the email log (its rows are the event), so nothing else held it.
Each row now keeps the acting name the audit log would write (a sign-in's
username, "token:<label>" for an API token, "system" when nobody was).
Rows from before this step have none, and say so.

Revision ID: c894a1e7d2b3
Revises: b8640c3e4a5b
Create Date: 2026-11-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c894a1e7d2b3"
down_revision: Union[str, None] = "b8640c3e4a5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "email_log" not in insp.get_table_names():
        return
    have = {c["name"] for c in insp.get_columns("email_log")}
    if "username" not in have:
        op.add_column("email_log", sa.Column("username", sa.String(100), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "email_log" not in insp.get_table_names():
        return
    have = {c["name"] for c in insp.get_columns("email_log")}
    if "username" in have:
        with op.batch_alter_table("email_log") as batch:
            batch.drop_column("username")
