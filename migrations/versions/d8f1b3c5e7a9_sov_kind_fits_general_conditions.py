"""sov_lines.kind fits "general_conditions"

- sov_lines.kind: String(16) -> String(24). The SOV kinds include
  "general_conditions" (18 characters), the line Build from accepted
  estimate writes for an estimate's general-conditions markup. SQLite never
  checked the length; PostgreSQL refused the insert (StringDataRightTruncation),
  so a Server Edition or Docker install could not build any schedule of
  values from an estimate that carried a general-conditions percent.

downgrade narrows it back (and fails on PostgreSQL while a
"general_conditions" row exists, which is the honest answer).

Revision ID: d8f1b3c5e7a9
Revises: c7e4a9b2d1f5
Create Date: 2026-11-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d8f1b3c5e7a9"
down_revision: Union[str, None] = "c7e4a9b2d1f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _set_kind(new: sa.String, old: sa.String) -> None:
    if "sov_lines" not in sa.inspect(op.get_bind()).get_table_names():
        return
    with op.batch_alter_table("sov_lines") as batch:
        batch.alter_column("kind", existing_type=old, type_=new, existing_nullable=False)


def upgrade() -> None:
    _set_kind(sa.String(24), sa.String(16))


def downgrade() -> None:
    _set_kind(sa.String(16), sa.String(24))
