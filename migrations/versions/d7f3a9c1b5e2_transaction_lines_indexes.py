"""transaction_lines(transaction_id) and (account_id) indexes (#693)

The model has always declared both columns indexed, but the initial schema
migration created only the index on the line id, and no later migration
added these two. A company file built by migrations therefore had neither,
while a file built by create_all had both, which is why the tests never
saw it. Without them, every lookup of an entry's lines or an account's
lines reads the whole table. The cash-basis backfill (#483) does one such
lookup per payment at start-up, and on a file with 617,000 lines and 8,580
payments it never finished; with the indexes it took about 25 seconds.

Each index is created only when the table has no index of that name, so a
file built by create_all is left alone. Indexes a customer added by hand
under other names (ix_tmp_*) are left in place and do not stop these.

Revision ID: d7f3a9c1b5e2
Revises: c4e8a1f7d2b6
Create Date: 2026-11-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d7f3a9c1b5e2"
down_revision: Union[str, None] = "c4e8a1f7d2b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "transaction_lines"
INDEXES = {
    "ix_transaction_lines_transaction_id": ["transaction_id"],
    "ix_transaction_lines_account_id": ["account_id"],
}


def _existing() -> set | None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names():
        return None
    return {i["name"] for i in insp.get_indexes(TABLE)}


def upgrade() -> None:
    existing = _existing()
    if existing is None:
        return
    for name, columns in INDEXES.items():
        if name not in existing:
            op.create_index(name, TABLE, columns)


def downgrade() -> None:
    existing = _existing()
    if existing is None:
        return
    for name in INDEXES:
        if name in existing:
            op.drop_index(name, table_name=TABLE)
