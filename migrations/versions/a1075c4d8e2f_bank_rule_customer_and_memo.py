"""bank_rules.customer_id and memo: a rule's customer and memo (#1075)

A bank rule named a category, a vendor, a class and a job. It could not
name a customer, so a regular client's deposit matched by a rule came in
under nobody, and it could not carry a memo, so the To review memo (#1049)
was typed on every line the rule matched. Each rule can now name a customer
(for money in, beside its vendor for money out) and a memo. Both nullable:
a rule with neither behaves exactly as before.

Idempotent, column by column (a database built by create_all already has
them). Batch mode adds the foreign key on SQLite and PostgreSQL alike;
downgrade drops what is there.

Revision ID: a1075c4d8e2f
Revises: f1049c2a7e5b
Create Date: 2026-11-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1075c4d8e2f"
down_revision: Union[str, None] = "f1049c2a7e5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "bank_rules"
FK = "fk_bank_rules_customer_id"


def _have(insp) -> set[str]:
    return {c["name"] for c in insp.get_columns(TABLE)}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names():
        return
    have = _have(insp)
    if {"customer_id", "memo"} <= have:
        return
    with op.batch_alter_table(TABLE) as batch:
        if "customer_id" not in have:
            batch.add_column(sa.Column("customer_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(FK, "customers", ["customer_id"], ["id"])
        if "memo" not in have:
            batch.add_column(sa.Column("memo", sa.String(500), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names():
        return
    have = _have(insp)
    if not {"customer_id", "memo"} & have:
        return
    fks = {f["name"] for f in insp.get_foreign_keys(TABLE)}
    with op.batch_alter_table(TABLE) as batch:
        if "customer_id" in have:
            if FK in fks:
                batch.drop_constraint(FK, type_="foreignkey")
            batch.drop_column("customer_id")
        if "memo" in have:
            batch.drop_column("memo")
