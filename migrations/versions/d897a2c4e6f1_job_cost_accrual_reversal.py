"""job_costs.reverses_on and reversal_transaction_id: accrued job costs (#897)

A job cost entry that credits 2010 Accrued Job Costs is an accrual: the
cost belongs in this period's WIP, the subcontractor's bill has not come
yet. It reverses itself on its Reverses on date, posted by the start-up
pass, and the reversal's transaction is kept on the entry so a second
start posts nothing. Both nullable: every entry from before this step is
an ordinary job cost entry with no reversal, exactly as it was.

Idempotent, column by column (a database built by create_all already has
them). Batch mode adds the foreign key on SQLite and PostgreSQL alike;
downgrade drops what is there.

Revision ID: d897a2c4e6f1
Revises: c894a1e7d2b3
Create Date: 2026-11-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d897a2c4e6f1"
down_revision: Union[str, None] = "c894a1e7d2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "job_costs"
FK = "fk_job_costs_reversal_transaction_id"


def _have(insp) -> set[str]:
    return {c["name"] for c in insp.get_columns(TABLE)}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names():
        return
    have = _have(insp)
    if {"reverses_on", "reversal_transaction_id"} <= have:
        return
    with op.batch_alter_table(TABLE) as batch:
        if "reverses_on" not in have:
            batch.add_column(sa.Column("reverses_on", sa.Date(), nullable=True))
        if "reversal_transaction_id" not in have:
            batch.add_column(
                sa.Column("reversal_transaction_id", sa.Integer(), nullable=True)
            )
            batch.create_foreign_key(
                FK, "transactions", ["reversal_transaction_id"], ["id"]
            )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names():
        return
    have = _have(insp)
    if not {"reverses_on", "reversal_transaction_id"} & have:
        return
    fks = {f["name"] for f in insp.get_foreign_keys(TABLE)}
    with op.batch_alter_table(TABLE) as batch:
        if "reversal_transaction_id" in have:
            if FK in fks:
                batch.drop_constraint(FK, type_="foreignkey")
            batch.drop_column("reversal_transaction_id")
        if "reverses_on" in have:
            batch.drop_column("reverses_on")
