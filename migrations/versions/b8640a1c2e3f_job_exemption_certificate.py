"""jobs.exempt_certificate_*: the certificate an exempt job stands on (#864)

The certificate lives on the job: its number, state (or jurisdiction), kind
(public_entity, nonprofit, resale or other) and an optional expiry. Every
job starts with none, as every job is charged until it is marked exempt.

Revision ID: b8640a1c2e3f
Revises: b7d2e4f6a8c1
Create Date: 2026-11-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b8640a1c2e3f"
down_revision: Union[str, None] = "b7d2e4f6a8c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("exempt_certificate_number", sa.String(50)),
    ("exempt_certificate_state", sa.String(20)),
    ("exempt_certificate_kind", sa.String(20)),
    ("exempt_certificate_expires", sa.Date()),
)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "jobs" not in insp.get_table_names():
        return
    have = {c["name"] for c in insp.get_columns("jobs")}
    for name, type_ in _COLUMNS:
        if name not in have:
            op.add_column("jobs", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "jobs" not in insp.get_table_names():
        return
    have = {c["name"] for c in insp.get_columns("jobs")}
    drop = [name for name, _ in _COLUMNS if name in have]
    if drop:
        with op.batch_alter_table("jobs") as batch:
            for name in drop:
                batch.drop_column(name)
