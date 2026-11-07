"""estimates.show_markup_rows and estimates.terms (the bid an owner receives)

Revision ID: e1b7c4a92d68
Revises: d7f3a9c1b5e2
Create Date: 2026-11-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e1b7c4a92d68"
down_revision: Union[str, None] = "d7f3a9c1b5e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("estimates")}


def upgrade() -> None:
    # A file built by create_all already has both columns.
    have = _columns()
    with op.batch_alter_table("estimates") as batch:
        if "show_markup_rows" not in have:
            batch.add_column(
                sa.Column(
                    "show_markup_rows",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.true(),
                )
            )
        if "terms" not in have:
            batch.add_column(sa.Column("terms", sa.Text(), nullable=True))


def downgrade() -> None:
    have = _columns()
    with op.batch_alter_table("estimates") as batch:
        if "terms" in have:
            batch.drop_column("terms")
        if "show_markup_rows" in have:
            batch.drop_column("show_markup_rows")
