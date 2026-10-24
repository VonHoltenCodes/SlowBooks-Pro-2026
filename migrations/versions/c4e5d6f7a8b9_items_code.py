"""items.code: a short code an item is found by (#445)

A lumber yard or parts counter knows an item by its code ("2x6-16")
as much as by its name ("2x6x16 SPF"). The code is optional, and unique
when set: one unique index on the column, which lets any number of rows
leave it empty (NULLs are distinct on SQLite and Postgres both). The
routes also refuse a code that differs only in capitals, which the index
cannot see.

Existing rows take no code; nothing changes for them.

Revision ID: c4e5d6f7a8b9
Revises: c5e8a1d47b20
Create Date: 2026-10-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c4e5d6f7a8b9"
down_revision: Union[str, None] = "c5e8a1d47b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEX = "ix_items_code"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "items" not in insp.get_table_names():
        return
    present = {c["name"] for c in insp.get_columns("items")}
    if "code" not in present:
        op.add_column("items", sa.Column("code", sa.String(50), nullable=True))
    indexes = {i["name"] for i in insp.get_indexes("items")}
    if INDEX not in indexes:
        op.create_index(INDEX, "items", ["code"], unique=True)


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "items" not in insp.get_table_names():
        return
    if INDEX in {i["name"] for i in insp.get_indexes("items")}:
        op.drop_index(INDEX, table_name="items")
    if "code" in {c["name"] for c in insp.get_columns("items")}:
        with op.batch_alter_table("items") as batch:
            batch.drop_column("code")
