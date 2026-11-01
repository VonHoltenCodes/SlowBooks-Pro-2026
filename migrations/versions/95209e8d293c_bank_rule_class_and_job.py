"""bank_rules.class_id and job_id: a rule's class and job (#675)

A bank rule named a category account and a vendor; a matched statement line
came in with those and nothing else, so its class and job were set by hand
on every line. Each rule can now name a class and a job too, and a line it
matches is added classed and on its job. Both nullable: a rule with neither
behaves as before.

Idempotent, column by column, so books that already have one (a database
built by create_all, or a run interrupted between the two) upgrade cleanly.
Batch mode adds the foreign keys on SQLite and PostgreSQL alike; downgrade
drops what is there.

Revision ID: 95209e8d293c
Revises: 4810bd59e481
Create Date: 2026-10-31
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "95209e8d293c"
down_revision: Union[str, None] = "4810bd59e481"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "bank_rules"
# (column, referred table, foreign key name)
COLUMNS = (
    ("class_id", "classes", "fk_bank_rules_class_id"),
    ("job_id", "jobs", "fk_bank_rules_job_id"),
)


def _have(insp) -> set[str]:
    return {c["name"] for c in insp.get_columns(TABLE)}


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names():
        return
    have = _have(insp)
    missing = [c for c in COLUMNS if c[0] not in have]
    if not missing:
        return
    with op.batch_alter_table(TABLE) as batch:
        for column, referred, fk in missing:
            batch.add_column(sa.Column(column, sa.Integer(), nullable=True))
            batch.create_foreign_key(fk, referred, [column], ["id"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if TABLE not in insp.get_table_names():
        return
    have = _have(insp)
    present = [c for c in COLUMNS if c[0] in have]
    if not present:
        return
    fks = {f["name"] for f in insp.get_foreign_keys(TABLE)}
    with op.batch_alter_table(TABLE) as batch:
        for column, _referred, fk in present:
            if fk in fks:
                batch.drop_constraint(fk, type_="foreignkey")
            batch.drop_column(column)
