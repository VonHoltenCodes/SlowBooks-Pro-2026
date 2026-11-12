"""estimates.decided_at: when a bid was won or lost (#795)

The Estimate Closing Rate report averages the days from sent to decision,
and nothing dated a decision but acceptance. An estimate decided before
this keeps what it can show: its accepted_at when it was won, the date in
its last "Lost YYYY-MM-DD: reason" note line when it was marked lost. The rest
stay NULL and the report leaves them out of the average.

Revision ID: a4c8e2f6b0d3
Revises: a4c8e2f6b1d3
Create Date: 2026-11-11
"""

import re
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a4c8e2f6b0d3"
down_revision: Union[str, None] = "a4c8e2f6b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LOST = re.compile(r"^Lost (\d{4}-\d{2}-\d{2})", re.MULTILINE)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "estimates" not in insp.get_table_names():
        return
    if "decided_at" not in {c["name"] for c in insp.get_columns("estimates")}:
        op.add_column(
            "estimates",
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        )
    bind.execute(
        sa.text(
            "UPDATE estimates SET decided_at = accepted_at "
            "WHERE decided_at IS NULL AND accepted_at IS NOT NULL "
            "AND status IN ('ACCEPTED', 'CONVERTED')"
        )
    )
    lost = bind.execute(
        sa.text(
            "SELECT id, notes FROM estimates "
            "WHERE decided_at IS NULL AND status = 'REJECTED' AND notes IS NOT NULL"
        )
    ).fetchall()
    for est_id, notes in lost:
        # lost, reopened and lost again leaves a line per loss: the last one
        # is the decision the status stands on
        found = _LOST.findall(notes)
        if not found:
            continue
        try:
            when = datetime.strptime(found[-1], "%Y-%m-%d")
        except ValueError:
            continue
        bind.execute(
            sa.text("UPDATE estimates SET decided_at = :at WHERE id = :id"),
            {"at": when.replace(tzinfo=timezone.utc), "id": est_id},
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "estimates" not in insp.get_table_names():
        return
    if "decided_at" in {c["name"] for c in insp.get_columns("estimates")}:
        with op.batch_alter_table("estimates") as batch:
            batch.drop_column("decided_at")
