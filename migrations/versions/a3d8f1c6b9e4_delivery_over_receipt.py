"""a delivery ticket line that took a PO line past what was ordered

- field_ticket_lines.over_receipt: true on a delivery line saved with
  `over_receipt: true` that took the PO line's received quantity past its
  ordered quantity. The ticket answers with a warning for it. False on every
  existing line.

downgrade reverses.

Revision ID: a3d8f1c6b9e4
Revises: c7e3a1f5d9b2
Create Date: 2026-11-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a3d8f1c6b9e4"
down_revision: Union[str, None] = "c7e3a1f5d9b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    # A book built by create_all already has it: added only when missing.
    have = _columns("field_ticket_lines")
    if have and "over_receipt" not in have:
        with op.batch_alter_table("field_ticket_lines") as batch:
            batch.add_column(
                sa.Column(
                    "over_receipt",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )


def downgrade() -> None:
    if "over_receipt" in _columns("field_ticket_lines"):
        with op.batch_alter_table("field_ticket_lines") as batch:
            batch.drop_column("over_receipt")
