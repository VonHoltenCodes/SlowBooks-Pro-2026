"""change orders: the date a change order takes effect, and allowance returns

Two additive columns on change_orders:

- approved_on (date): the day an approved change order takes effect. Approve
  now takes it (default the change order's own date, else today), so a change
  order entered late still moves the contract, the WIP schedule, the log and a
  pay application's contract sum on its own date. approved_at stays the audit
  stamp of the click. Existing approved rows take the local day of their
  approved_at, which is what every report read before.
- allowance_return (bool, default false): the change order that returns an
  allowance's unspent amount to the owner.

downgrade drops both.

Revision ID: e2a6c8f4b1d9
Revises: d7f3a9c1b5e2
Create Date: 2026-11-07

"""

from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e2a6c8f4b1d9"
down_revision: Union[str, None] = "d7f3a9c1b5e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _local_day(value):
    """The local calendar day of a stored UTC timestamp (a string on SQLite)."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().date()


def _columns() -> set:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("change_orders")}


def upgrade() -> None:
    # A file built by create_all and stamped back already has the columns.
    existing = _columns()
    if "approved_on" not in existing:
        with op.batch_alter_table("change_orders") as batch:
            batch.add_column(sa.Column("approved_on", sa.Date(), nullable=True))
        bind = op.get_bind()
        rows = bind.execute(
            sa.text(
                "SELECT id, approved_at FROM change_orders WHERE approved_at IS NOT NULL"
            )
        ).fetchall()
        for co_id, approved_at in rows:
            bind.execute(
                sa.text(
                    "UPDATE change_orders SET approved_on = :d WHERE id = :i"
                ).bindparams(
                    sa.bindparam("d", _local_day(approved_at), type_=sa.Date()),
                    sa.bindparam("i", co_id),
                )
            )
    if "allowance_return" not in existing:
        with op.batch_alter_table("change_orders") as batch:
            batch.add_column(
                sa.Column(
                    "allowance_return",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )


def downgrade() -> None:
    existing = _columns()
    for name in ("allowance_return", "approved_on"):
        if name in existing:
            with op.batch_alter_table("change_orders") as batch:
                batch.drop_column(name)
