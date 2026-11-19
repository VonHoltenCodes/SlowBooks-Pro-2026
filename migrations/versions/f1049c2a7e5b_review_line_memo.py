"""bank_transactions.pick_memo: a memo on each To review line (#1049)

A line waiting in To review kept the category, class, job and name picked
for it (#904), but no memo: a note about what the money was for had to be
added by reopening the entry after it posted. The memo typed on the row is
kept on the line now, the way the other picks are, and posted as the
entry's memo when the line is added.

One nullable column; every line from before this step has no memo typed,
exactly as it was. Idempotent (a database built by create_all already has
it); downgrade drops what is there.

Revision ID: f1049c2a7e5b
Revises: e1055c0de4a1
Create Date: 2026-11-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f1049c2a7e5b"
down_revision: Union[str, None] = "e1055c0de4a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "bank_transactions"
COLUMN = "pick_memo"


def _have(insp) -> set[str]:
    return {c["name"] for c in insp.get_columns(TABLE)}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names() or COLUMN in _have(insp):
        return
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(sa.Column(COLUMN, sa.String(500), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names() or COLUMN not in _have(insp):
        return
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_column(COLUMN)
