"""change orders: the date a change order takes effect, and allowance returns

Two additive columns on change_orders:

- approved_on (date): the day an approved change order takes effect. Approve
  now takes it (default the change order's own date, else today), so a change
  order entered late still moves the contract, the WIP schedule, the log and a
  pay application's contract sum on its own date. approved_at stays the audit
  stamp of the click. Existing approved rows take the local day of their
  approved_at, which is what every report read before; a change order whose
  schedule line is billed on an application not yet approved takes the
  earlier of its own date and that day, so the draft stays saveable.
- allowance_return (bool, default false): the change order that returns an
  allowance's unspent amount to the owner.
- allowance_draw_written (bool, default false): the approve of an
  allowance-funded change order wrote its draw out of the allowance's budget
  cell. Existing rows are false: the release before never wrote the draw, so
  voiding one takes back its lines alone.

downgrade drops all three.

Revision ID: e2a6c8f4b1d9
Revises: d7f3a9c1b5e2
Create Date: 2026-11-07

"""

from datetime import date, datetime, timezone
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


def _plain_day(value):
    """A stored date (a string on SQLite) as a date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _on_open_application(bind, co_id: int) -> bool:
    """True when a line of the change order's schedule line carries an
    amount on a pay application that is not approved yet."""
    tables = sa.inspect(bind).get_table_names()
    if "pay_application_lines" not in tables or "sov_lines" not in tables:
        return False
    return (
        bind.execute(
            sa.text(
                "SELECT 1 FROM pay_application_lines l "
                "JOIN sov_lines s ON s.id = l.sov_line_id "
                "JOIN pay_applications a ON a.id = l.pay_application_id "
                "WHERE s.change_order_id = :c "
                "AND a.status IN ('draft', 'submitted') "
                "AND (l.this_period <> 0 OR l.stored <> 0) LIMIT 1"
            ),
            {"c": co_id},
        ).first()
        is not None
    )


def _backfill_approved_on(bind) -> None:
    """Every approved change order takes the local day of its approved_at,
    or the earlier of that and its own date when its schedule line is
    billed on an application not yet approved."""
    rows = bind.execute(
        sa.text(
            "SELECT id, approved_at, date FROM change_orders "
            "WHERE approved_at IS NOT NULL"
        )
    ).fetchall()
    for co_id, approved_at, co_date in rows:
        day = _local_day(approved_at)
        if co_date is not None and _on_open_application(bind, co_id):
            day = min(day, _plain_day(co_date))
        bind.execute(
            sa.text(
                "UPDATE change_orders SET approved_on = :d WHERE id = :i"
            ).bindparams(
                sa.bindparam("d", day, type_=sa.Date()),
                sa.bindparam("i", co_id),
            )
        )


def _columns() -> set:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("change_orders")}


def upgrade() -> None:
    # A file built by create_all and stamped back already has the columns.
    existing = _columns()
    if "approved_on" not in existing:
        with op.batch_alter_table("change_orders") as batch:
            batch.add_column(sa.Column("approved_on", sa.Date(), nullable=True))
        _backfill_approved_on(op.get_bind())
    if "allowance_draw_written" not in existing:
        with op.batch_alter_table("change_orders") as batch:
            batch.add_column(
                sa.Column(
                    "allowance_draw_written",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
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
    for name in ("allowance_draw_written", "allowance_return", "approved_on"):
        if name in existing:
            with op.batch_alter_table("change_orders") as batch:
                batch.drop_column(name)
