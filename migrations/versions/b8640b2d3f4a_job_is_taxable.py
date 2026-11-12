"""jobs.is_taxable: sales tax charged on the job, or the job exempt (#864)

Every job is charged, as before. A non-taxable customer's documents stay
untaxed by the customer's own flag, whatever its jobs say.

Revision ID: b8640b2d3f4a
Revises: b8640a1c2e3f
Create Date: 2026-11-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b8640b2d3f4a"
down_revision: Union[str, None] = "b8640a1c2e3f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "jobs" not in insp.get_table_names():
        return
    if "is_taxable" not in {c["name"] for c in insp.get_columns("jobs")}:
        op.add_column(
            "jobs",
            sa.Column(
                "is_taxable", sa.Boolean(), nullable=False, server_default=sa.true()
            ),
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "jobs" not in insp.get_table_names():
        return
    if "is_taxable" in {c["name"] for c in insp.get_columns("jobs")}:
        with op.batch_alter_table("jobs") as batch:
            batch.drop_column("is_taxable")
