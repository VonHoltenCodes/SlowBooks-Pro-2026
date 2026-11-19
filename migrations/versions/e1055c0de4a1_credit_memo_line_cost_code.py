"""credit_memo_lines.cost_code_id + cost_type: revenue by cost code (#1055)

In construction mode an invoice line takes a cost code and a cost type, so
one item ("Project revenue") can be split on the invoice per code and type
while the income account, and the Profit & Loss, stay as they were. The
invoice line already has both columns (job costing, #551). A credit memo
written against that revenue takes them too, so the credit reduces the same
code's revenue on the job reports.

Two nullable columns, the cost code with a foreign key to cost_codes, the
cost type the cost_types table's key as on every other document line.
Every credit memo line from before this step names no cost code, exactly
as it was.

Idempotent (a database built by create_all already has the columns). Batch
mode adds the foreign key on SQLite and PostgreSQL alike; downgrade drops
what is there.

Revision ID: e1055c0de4a1
Revises: e986b4d2a7c3
Create Date: 2026-11-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e1055c0de4a1"
down_revision: Union[str, None] = "e986b4d2a7c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "credit_memo_lines"
FK = "fk_credit_memo_lines_cost_code_id"


def _have(insp) -> set[str]:
    return {c["name"] for c in insp.get_columns(TABLE)}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names():
        return
    have = _have(insp)
    if {"cost_code_id", "cost_type"} <= have:
        return
    with op.batch_alter_table(TABLE) as batch:
        if "cost_code_id" not in have:
            batch.add_column(sa.Column("cost_code_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(FK, "cost_codes", ["cost_code_id"], ["id"])
        if "cost_type" not in have:
            batch.add_column(sa.Column("cost_type", sa.String(20), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names():
        return
    have = _have(insp)
    fks = {f["name"] for f in insp.get_foreign_keys(TABLE)}
    if not ({"cost_code_id", "cost_type"} & have):
        return
    with op.batch_alter_table(TABLE) as batch:
        if FK in fks:
            batch.drop_constraint(FK, type_="foreignkey")
        if "cost_code_id" in have:
            batch.drop_column("cost_code_id")
        if "cost_type" in have:
            batch.drop_column("cost_type")
