"""estimates.decided_at: a lost bid's upgrade date on the local calendar (#795)

The step below filled a lost estimate's decided_at from its last "Lost
YYYY-MM-DD" note line as midnight UTC. The note's date is the machine's
local date and the Estimate Closing Rate report reads decided_at back on
the local calendar, so west of Greenwich the loss landed the day before.
That step now stores local noon; this one moves the dates it already wrote
in a company upgraded before the fix: a lost estimate whose decided_at is
exactly midnight UTC on its last note's date goes to noon on that date,
local time. A decision stamped by the app itself is never at that instant
on the note's own date, so nothing else is touched.

Revision ID: b6e1d4a8c2f5
Revises: a4c8e2f6b0d3
Create Date: 2026-11-12
"""

import re
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b6e1d4a8c2f5"
down_revision: Union[str, None] = "a4c8e2f6b0d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LOST = re.compile(r"^Lost (\d{4}-\d{2}-\d{2})", re.MULTILINE)


def _stored(value):
    """decided_at as read back: SQLite hands back text, Postgres a datetime;
    a naive value is UTC."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "estimates" not in insp.get_table_names():
        return
    if "decided_at" not in {c["name"] for c in insp.get_columns("estimates")}:
        return
    rows = bind.execute(
        sa.text(
            "SELECT id, notes, decided_at FROM estimates "
            "WHERE decided_at IS NOT NULL AND status = 'REJECTED' "
            "AND notes IS NOT NULL"
        )
    ).fetchall()
    for est_id, notes, decided_at in rows:
        found = _LOST.findall(notes)
        if not found:
            continue
        try:
            day = datetime.strptime(found[-1], "%Y-%m-%d")
        except ValueError:
            continue
        if _stored(decided_at) != day.replace(tzinfo=timezone.utc):
            continue
        bind.execute(
            sa.text("UPDATE estimates SET decided_at = :at WHERE id = :id"),
            {"at": day.replace(hour=12).astimezone(timezone.utc), "id": est_id},
        )


def downgrade() -> None:
    # the earlier dates were a day off west of UTC: nothing to put back
    pass
