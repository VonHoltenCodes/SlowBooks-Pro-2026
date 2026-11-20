"""an estimate can be voided (#1128)

An estimate made in error (a duplicate, the wrong customer, a test) could
only be edited or marked lost, and a lost estimate counts against the
Estimate Closing Rate (#795). From here an estimate has a fifth status,
void, and keeps who voided it, when, and the reason given: three nullable
columns on estimates, NULL on every estimate there is.

PostgreSQL keeps the status in a native enum type (estimatestatus), so the
new value is added to it. SQLite stores the name as text with no
constraint, so nothing there changes for the status. A downgrade drops the
three columns and turns void estimates into lost ones: the older build has
no word for void, and PostgreSQL cannot drop a value from an enum type.

Idempotent, column by column (a database built by create_all already has
them).

Revision ID: c1128e5a7d0f
Revises: a1075c4d8e2f
Create Date: 2026-11-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c1128e5a7d0f"
down_revision: Union[str, None] = "a1075c4d8e2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "estimates"
COLUMNS = (
    ("voided_at", sa.DateTime(timezone=True)),
    ("voided_by", sa.String(100)),
    ("void_reason", sa.String(500)),
)


def _have(insp) -> set[str]:
    return {c["name"] for c in insp.get_columns(TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if TABLE not in insp.get_table_names():
        return
    if bind.dialect.name == "postgresql":
        # ADD VALUE wants to run outside the migration's transaction
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE estimatestatus ADD VALUE IF NOT EXISTS 'VOID'")
    have = _have(insp)
    missing = [(name, kind) for name, kind in COLUMNS if name not in have]
    if not missing:
        return
    with op.batch_alter_table(TABLE) as batch:
        for name, kind in missing:
            batch.add_column(sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names():
        return
    op.execute("UPDATE estimates SET status = 'REJECTED' WHERE status = 'VOID'")
    have = _have(insp)
    present = [name for name, _ in COLUMNS if name in have]
    if not present:
        return
    with op.batch_alter_table(TABLE) as batch:
        for name in present:
            batch.drop_column(name)
